"""
data_pipeline/jobs/audit_and_heal.py
Job do ciclo contínuo de auditoria & saneamento (roda no mesmo cron diário,
depois dos jobs de coleta — NÃO aumenta a frequência de scraping).

Fluxo por execução (incremental, N empresas):
  scheduler.next_batch → collect_and_resolve (1x web) → audita/decide
  → grava correções (≥2 fontes, backup+auditoria) → score por campo
  → relatório (banco + JSON/CSV artifact).

Gravação no banco é controlada por env `AUDIT_HEAL_APPLY` (default "false" =
dry-run seguro). Defina "true" no workflow/Secrets após revisar um artifact.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from data_pipeline.quality import report as _report
from data_pipeline.quality import sanitizer as _san
from data_pipeline.quality import scheduler as _sched
from data_pipeline.quality import score as _score

logger = logging.getLogger(__name__)

JOB_NAME = "audit_and_heal"
_FONTES = ["Banco", "Fundamentus", "StatusInvest", "brapi"]


# ── Núcleo PURO (testável) ────────────────────────────────────────────────────

def summarize(resolutions_by_ticker: dict, gravados: int = 0,
              ciclo_reiniciado: bool = False) -> dict:
    """Métricas do ciclo a partir das resoluções (independe de banco/rede)."""
    empresas = len(resolutions_by_ticker or {})
    corrigidas: set[str] = set()
    campos_atualizados = 0
    campos_invalidos = 0
    divergencias = 0
    for tk, resolutions in (resolutions_by_ticker or {}).items():
        for r in resolutions:
            decisao = _san.decide(r)
            if _san.is_write_decision(decisao):
                campos_atualizados += 1
                corrigidas.add(tk)
            if r.acao == "divergencia_nao_resolvida":
                divergencias += 1
            if r.acao in ("sem_corroboracao", "sem_dado", "divergencia_nao_resolvida"):
                campos_invalidos += 1
    return {
        "empresas_verificadas": empresas,
        "empresas_corrigidas": len(corrigidas),
        "campos_atualizados": int(gravados) if gravados else campos_atualizados,
        "campos_invalidos": campos_invalidos,
        "divergencias": divergencias,
        "ciclo_reiniciado": ciclo_reiniciado,
        "fontes": _FONTES,
    }


def build_score_rows(resolutions_by_ticker: dict) -> list[dict]:
    """Score de confiabilidade por (ticker, campo) a partir das resoluções."""
    rows: list[dict] = []
    for tk, resolutions in (resolutions_by_ticker or {}).items():
        # divergências do ticker pesam no score de cada campo
        n_div_tk = sum(1 for r in resolutions if r.acao == "divergencia_nao_resolvida")
        for r in resolutions:
            sc = _score.compute_field_score(
                n_sources_agree=int(r.n_fontes or 0),
                age_days=0.0,
                hist_cv=None,
                n_validations=1 if r.acao in ("mantido", "corrigido", "preenchido") else 0,
                n_divergences=1 if r.acao == "divergencia_nao_resolvida" else 0,
            )
            rows.append({
                "ticker": tk, "indicador": r.field, "score": sc,
                "n_fontes": int(r.n_fontes or 0), "idade_dias": 0.0,
                "consistencia": 0.0, "n_validacoes": 1 if r.acao != "sem_dado" else 0,
                "n_divergencias": n_div_tk,
            })
    return rows


def _apply_enabled(apply: bool | None) -> bool:
    if apply is not None:
        return bool(apply)
    return os.getenv("AUDIT_HEAL_APPLY", "false").strip().lower() in ("1", "true", "yes", "sim")


# ── Entrada do orquestrador ───────────────────────────────────────────────────

def run(apply: bool | None = None, batch_size: int | None = None) -> dict:
    started = datetime.now(timezone.utc)
    n = batch_size or int(os.getenv("AUDIT_HEAL_BATCH", str(_sched.DEFAULT_BATCH)))
    apply_writes = _apply_enabled(apply)

    result = {
        "status": "success",
        "table_name": "multiplos, data_quality_scores, data_quality_reports",
        "source_name": "Data Quality (Banco x Fundamentus x Status Invest)",
        "job_name": JOB_NAME,
        "records_inserted": 0, "records_updated": 0, "records_failed": 0,
        "error_message": None,
    }

    try:
        from core.data_healing import (
            collect_and_resolve, resolutions_to_preview_df, apply_healing,
        )
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"import data_healing: {exc}"[:500]
        return result

    try:
        tickers, cursor, wrapped = _sched.next_batch(n)
        if not tickers:
            result["status"] = "skipped"
            result["error_message"] = "Universo vazio (tabela setores indisponível)."
            return result

        # 1x coleta web + resolução de todos os campos
        resolutions = collect_and_resolve(tickers)

        # gravação (env-gated; sempre com backup + auditoria)
        gravados = 0
        if apply_writes:
            preview = resolutions_to_preview_df(resolutions)
            if not preview.empty:
                out = apply_healing(preview)
                gravados = int(out.get("gravados", 0) or 0)
                if out.get("erro"):
                    result["records_failed"] += 1
                    result["error_message"] = str(out["erro"])[:500]

        # score por campo + média do banco
        try:
            _score.upsert_scores(build_score_rows(resolutions))
        except Exception as exc:
            logger.warning("score upsert: %s", exc)
        avg = _score.bank_average_score()

        # relatório
        metrics = summarize(resolutions, gravados=gravados, ciclo_reiniciado=wrapped)
        metrics["score_medio_banco"] = avg
        metrics["confiabilidade_geral"] = avg
        metrics["tempo_execucao_s"] = (datetime.now(timezone.utc) - started).total_seconds()
        run_ts = started.isoformat(timespec="seconds")
        try:
            _report.persist_report(metrics, run_ts)
        except Exception as exc:
            logger.warning("persist_report: %s", exc)

        result["records_updated"] = gravados
        result["records_inserted"] = 0
        if not apply_writes:
            result["error_message"] = (
                f"dry-run: {metrics['campos_atualizados']} correção(ões) propostas "
                f"em {len(tickers)} empresas (defina AUDIT_HEAL_APPLY=true para gravar)."
            )
        return result
    except Exception as exc:
        logger.warning("audit_and_heal falhou: %s", exc)
        result["status"] = "failed"
        result["error_message"] = str(exc)[:500]
        return result

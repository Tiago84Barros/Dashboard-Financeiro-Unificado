"""
data_pipeline/orchestrator.py
Orquestrador central do pipeline de dados.

Uso:
    from data_pipeline.orchestrator import run_updates, get_update_status

    # Atualiza tudo
    result = run_updates()

    # Atualiza só cotações
    result = run_updates(update_group="update_b3_quotes")

    # Força re-execução independente de frequência
    result = run_updates(force=True)

Também executável via CLI:
    python run_data_updates.py --all
    python run_data_updates.py --source bcb --force
"""
from __future__ import annotations

import importlib
import logging
import traceback
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Mapeamento job_name → módulo Python
_JOB_MAP: dict[str, str] = {
    "update_b3_quotes":       "data_pipeline.jobs.update_b3_quotes",
    "update_bcb":             "data_pipeline.jobs.update_bcb",
    "update_macro":           "data_pipeline.jobs.update_macro",
    "update_cvm":             "data_pipeline.jobs.update_cvm",
    "update_cvm_ipe":         "data_pipeline.jobs.update_cvm_ipe",
    "update_cvm_fulltext":    "data_pipeline.jobs.update_cvm_fulltext",
    "update_brapi_history":   "data_pipeline.jobs.update_brapi_history",
    "update_b3_fundamentals": "data_pipeline.jobs.update_b3_fundamentals",
    "update_dividendos":      "data_pipeline.jobs.update_dividendos",
    "update_empresas_eua":    "data_pipeline.jobs.update_empresas_eua",
    "update_fx_rates":        "data_pipeline.jobs.update_fx_rates",
    "update_noticias":        "data_pipeline.jobs.update_noticias",
    "audit_and_heal":         "data_pipeline.jobs.audit_and_heal",
}


def _run_job(job_name: str, registry_item: dict) -> dict:
    """Executa um job e retorna resultado padronizado."""
    table_name  = registry_item.get("table_name", "")
    source_name = registry_item.get("source_name", "")
    frequency   = registry_item.get("frequency", "diario")
    started_at  = datetime.now(tz=timezone.utc)

    base = {
        "status":           "failed",
        "table_name":       table_name,
        "source_name":      source_name,
        "job_name":         job_name,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    None,
        "execution_time_seconds": 0.0,
    }

    from data_pipeline.utils.logging_utils import (
        log_finish,
        log_start,
        update_freshness,
    )

    log_id = log_start(table_name, source_name, job_name)

    module_path = _JOB_MAP.get(job_name)
    if not module_path:
        err = f"Job '{job_name}' não encontrado no mapa de jobs"
        logger.error(err)
        base["error_message"] = err
        log_finish(log_id, "failed", error_message=err, started_at=started_at)
        update_freshness(table_name, source_name, job_name, "failed",
                         error_message=err, frequency=frequency)
        return base

    try:
        mod = importlib.import_module(module_path)
        result: dict = mod.run()
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}"
        logger.error("Job %s falhou: %s", job_name, exc)
        base["error_message"] = str(exc)
        log_finish(log_id, "failed", error_message=str(exc), started_at=started_at)
        update_freshness(table_name, source_name, job_name, "failed",
                         error_message=str(exc), frequency=frequency)
        return base

    # Normaliza resultado do job. A UI e os logs esperam tipos previsiveis,
    # mesmo quando um job novo retorna campos nulos ou formato inesperado.
    if not isinstance(result, dict):
        result = {
            "status": "failed",
            "error_message": f"Retorno invalido do job {job_name}: {type(result).__name__}",
        }
    result["status"] = result.get("status") or "success"
    if result["status"] not in {"success", "partial_success", "skipped", "failed"}:
        result["status"] = "failed"
        result["error_message"] = result.get("error_message") or "Status de job invalido"
    result["table_name"] = result.get("table_name") or table_name
    result["source_name"] = result.get("source_name") or source_name
    result["job_name"] = result.get("job_name") or job_name
    for key in ("records_inserted", "records_updated", "records_failed"):
        try:
            result[key] = int(result.get(key) or 0)
        except (TypeError, ValueError):
            result[key] = 0
    result["error_message"] = result.get("error_message") or None

    finished = datetime.now(tz=timezone.utc)
    result["execution_time_seconds"] = (finished - started_at).total_seconds()

    log_finish(
        log_id,
        result["status"],
        records_inserted=result["records_inserted"],
        records_updated=result["records_updated"],
        records_failed=result["records_failed"],
        error_message=result.get("error_message"),
        started_at=started_at,
    )
    update_freshness(
        table_name, source_name, job_name,
        status=result["status"],
        records_inserted=result["records_inserted"],
        records_updated=result["records_updated"],
        records_failed=result["records_failed"],
        error_message=result.get("error_message"),
        frequency=frequency,
    )
    return result


def run_updates(
    update_group: str = "all",
    force: bool = False,
) -> dict:
    """
    Executa atualizações conforme o registry.

    update_group: "all" | nome do job_name específico (ex: "update_bcb")
    force: True ignora verificação de frequência

    Retorna resumo da execução.
    """
    from data_pipeline.update_registry import get_registry, seed_registry
    from data_pipeline.utils.date_utils import should_run_job
    from data_pipeline.utils.db_utils import table_exists

    if not table_exists("data_update_registry"):
        return {
            "status": "error",
            "error": "Tabelas do pipeline não existem. Execute 'Criar tabelas admin' primeiro.",
            "total_jobs": 0, "success_count": 0, "failed_count": 0,
            "skipped_count": 0, "results": [],
        }

    seed_registry()
    registry = get_registry(active_only=True)

    started_at = datetime.now(tz=timezone.utc)
    results: list[dict] = []
    skipped = 0

    for item in registry:
        job_name = item.get("job_name", "")

        if update_group != "all" and job_name != update_group:
            continue

        if not should_run_job(item, force=force):
            skipped += 1
            logger.info("Pulando %s (frequência não atingida)", job_name)
            from data_pipeline.utils.logging_utils import log_skipped, update_freshness
            tn = item.get("table_name", "")
            sn = item.get("source_name", "")
            freq = item.get("frequency", "diario")
            log_skipped(tn, sn, job_name)
            update_freshness(tn, sn, job_name, status="skipped", frequency=freq)
            continue

        logger.info("Executando job: %s", job_name)
        result = _run_job(job_name, item)
        results.append(result)

    finished_at = datetime.now(tz=timezone.utc)
    success_count = sum(1 for r in results if r.get("status") in ("success", "partial_success"))
    failed_count  = sum(1 for r in results if r.get("status") == "failed")

    overall = "success" if failed_count == 0 else ("partial_success" if success_count > 0 else "failed")

    return {
        "status":        overall,
        "started_at":    started_at.isoformat(),
        "finished_at":   finished_at.isoformat(),
        "total_jobs":    len(results),
        "success_count": success_count,
        "failed_count":  failed_count,
        "skipped_count": skipped,
        "results":       results,
    }


# ── Funções auxiliares de consulta ───────────────────────────────────────────

def get_update_status() -> list[dict]:
    """Retorna data_freshness_status completo para a UI."""
    from data_pipeline.utils.db_utils import get_pipeline_engine, table_exists
    engine = get_pipeline_engine()
    if engine is None or not table_exists("data_freshness_status"):
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT * FROM data_freshness_status
                ORDER BY freshness_status DESC, last_attempt_at DESC NULLS LAST
            """)).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception as exc:
        logger.warning("get_update_status: %s", exc)
        return []


def get_recent_update_logs(limit: int = 50) -> list[dict]:
    """Retorna os logs mais recentes de execução."""
    from data_pipeline.utils.db_utils import get_pipeline_engine, table_exists
    engine = get_pipeline_engine()
    if engine is None or not table_exists("data_update_logs"):
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT * FROM data_update_logs
                ORDER BY started_at DESC NULLS LAST
                LIMIT :lim
            """), {"lim": limit}).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception as exc:
        logger.warning("get_recent_update_logs: %s", exc)
        return []


def get_outdated_sources() -> list[dict]:
    """Retorna fontes com freshness_status em 'outdated', 'error' ou 'never_updated'."""
    status = get_update_status()
    return [s for s in status if s.get("freshness_status") in
            ("outdated", "error", "never_updated")]


def get_last_global_update() -> datetime | None:
    """Retorna o timestamp da última execução com sucesso em qualquer fonte."""
    from data_pipeline.utils.db_utils import get_pipeline_engine, table_exists
    engine = get_pipeline_engine()
    if engine is None or not table_exists("data_freshness_status"):
        return None
    try:
        with engine.connect() as conn:
            val = conn.execute(text(
                "SELECT MAX(last_success_at) FROM data_freshness_status"
            )).scalar()
            return val
    except Exception:
        return None

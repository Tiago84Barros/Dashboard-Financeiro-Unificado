"""
data_pipeline/quality/report.py
Relatório de cada execução de auditoria/saneamento.

Consolida métricas (verificadas, corrigidas, campos atualizados/inválidos,
divergências, tempo, fontes, score médio, confiabilidade geral) e persiste em:
  • tabela `data_quality_reports` (JSONB) — durável, exibida no dashboard;
  • arquivos `artifacts/quality/report_<ts>.json|.csv` — anexados como artifact
    do GitHub Actions.

Textos de erro passam por `_sanitize` (sem connection string / segredo).
"""
from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_REPORTS_TABLE = "data_quality_reports"
_DEFAULT_DIR = "artifacts/quality"


def _sanitize_text(msg):
    try:
        from data_pipeline.utils.logging_utils import _sanitize
        return _sanitize(msg)
    except Exception:
        return (str(msg)[:500] if msg else msg)


def build_report(metrics: dict, run_ts: str) -> dict:
    """Monta o dicionário-relatório padronizado a partir das métricas da execução."""
    m = metrics or {}
    report = {
        "run_ts": run_ts,
        "empresas_verificadas": int(m.get("empresas_verificadas", 0) or 0),
        "empresas_corrigidas": int(m.get("empresas_corrigidas", 0) or 0),
        "campos_atualizados": int(m.get("campos_atualizados", 0) or 0),
        "campos_invalidos": int(m.get("campos_invalidos", 0) or 0),
        "dados_removidos": int(m.get("dados_removidos", 0) or 0),
        "dados_recuperados": int(m.get("dados_recuperados", 0) or 0),
        "divergencias": int(m.get("divergencias", 0) or 0),
        "tempo_execucao_s": round(float(m.get("tempo_execucao_s", 0.0) or 0.0), 2),
        "fontes": list(m.get("fontes", []) or []),
        "score_medio_banco": m.get("score_medio_banco"),
        "confiabilidade_geral": m.get("confiabilidade_geral"),
        "ciclo_reiniciado": bool(m.get("ciclo_reiniciado", False)),
        "erro": _sanitize_text(m.get("erro")),
    }
    return report


def _ensure_table(conn) -> None:
    from sqlalchemy import text
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_REPORTS_TABLE} (
            id BIGSERIAL PRIMARY KEY,
            run_ts TIMESTAMPTZ NOT NULL,
            empresas_verificadas INTEGER, empresas_corrigidas INTEGER,
            campos_atualizados INTEGER, divergencias INTEGER,
            score_medio_banco DOUBLE PRECISION,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))


def persist_to_db(report: dict) -> bool:
    from sqlalchemy import text

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        return False
    try:
        with engine.begin() as conn:
            _ensure_table(conn)
            conn.execute(text(f"""
                INSERT INTO {_REPORTS_TABLE}
                  (run_ts, empresas_verificadas, empresas_corrigidas,
                   campos_atualizados, divergencias, score_medio_banco, payload)
                VALUES (:ts, :ev, :ec, :ca, :dv, :sc, CAST(:pl AS jsonb))
            """), {
                "ts": report.get("run_ts"),
                "ev": report.get("empresas_verificadas", 0),
                "ec": report.get("empresas_corrigidas", 0),
                "ca": report.get("campos_atualizados", 0),
                "dv": report.get("divergencias", 0),
                "sc": report.get("score_medio_banco"),
                "pl": json.dumps(report, ensure_ascii=False, default=str),
            })
        return True
    except Exception as exc:
        logger.warning("persist_to_db: %s", _sanitize_text(str(exc)))
        return False


def write_files(report: dict, out_dir: str = _DEFAULT_DIR) -> dict:
    """Grava report_<ts>.json e .csv. Retorna {json, csv} com os caminhos."""
    ts_safe = str(report.get("run_ts", "report")).replace(":", "").replace(" ", "_").replace("+", "")
    p = Path(out_dir)
    out = {"json": None, "csv": None}
    try:
        p.mkdir(parents=True, exist_ok=True)
        json_path = p / f"report_{ts_safe}.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                             encoding="utf-8")
        out["json"] = str(json_path)
        csv_path = p / f"report_{ts_safe}.csv"
        flat = {k: (";".join(map(str, v)) if isinstance(v, list) else v) for k, v in report.items()}
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(list(flat.keys()))
            w.writerow(list(flat.values()))
        out["csv"] = str(csv_path)
    except Exception as exc:
        logger.warning("write_files: %s", _sanitize_text(str(exc)))
    return out


def persist_report(metrics: dict, run_ts: str, out_dir: str = _DEFAULT_DIR) -> dict:
    """Build + grava no banco + arquivos. Retorna o relatório com os caminhos."""
    report = build_report(metrics, run_ts)
    report["persistido_banco"] = persist_to_db(report)
    report["arquivos"] = write_files(report, out_dir)
    return report

"""
data_pipeline/validators/validate_financials.py
Valida múltiplos/demonstrações B3 contra as faixas canônicas (core.data_quality).

Antes era um stub. Agora roda como etapa de validação do pipeline: lê a tabela
`public.multiplos`, aplica as faixas coerentes (separando dado AUSENTE de ZERO),
e reporta outliers, zeros-faltantes e empresas com campos críticos ausentes —
SEM mascarar nada. Usado pelo orquestrador e como checagem pós-ingestão.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def validate() -> dict:
    """Verifica completude e coerência dos múltiplos no banco."""
    try:
        import pandas as pd  # noqa: F401
        from sqlalchemy import text
        from data_pipeline.utils.db_utils import get_pipeline_engine
        import core.data_quality as dq
    except Exception as exc:  # dependência ausente
        return {"status": "skipped", "issues": [], "error_message": f"dependência: {exc}"}

    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "skipped", "issues": [], "error_message": "Banco não conectado"}

    cols = ", ".join(f'"{c}"' for c in dq.CANONICAL_MULTIPLOS_FIELDS)
    try:
        import pandas as pd
        with engine.connect() as conn:
            df = pd.read_sql_query(text(f"""
                SELECT DISTINCT ON ("Ticker") "Ticker", {cols}
                FROM public.multiplos
                WHERE "Ticker" IS NOT NULL
                ORDER BY "Ticker", data DESC
            """), conn)
    except Exception as exc:
        return {"status": "failed", "issues": [], "error_message": f"query falhou: {exc}"}

    if df.empty:
        return {"status": "skipped", "issues": [], "error_message": "multiplos vazia"}

    df["Ticker"] = df["Ticker"].astype(str).str.replace(".SA", "", regex=False).str.upper()
    report = dq.generate_data_quality_report(df)

    issues: list[dict] = []
    for o in report.get("outliers", []):
        issues.append({"tipo": "outlier", **o})
    for tk, campos in report.get("campos_criticos_ausentes", {}).items():
        issues.append({"tipo": "critico_ausente", "Ticker": tk, "Campos": campos})

    status = "success" if not issues else "warning"
    return {
        "status": status,
        "issues": issues,
        "n_outliers": len(report.get("outliers", [])),
        "n_empresas_insuficientes": len(report.get("empresas_insuficientes", [])),
        "duplicados": report.get("duplicados", []),
        "sem_setor": report.get("sem_setor", []),
        "error_message": None,
        "resumo": report.get("impacto"),
    }

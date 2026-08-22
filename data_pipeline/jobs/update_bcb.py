"""
data_pipeline/jobs/update_bcb.py
Atualiza public.macro com dados macro do BCB/SGS (Selic, IPCA, câmbio, PIB, etc.).

Reutiliza a lógica de scripts/seed_macro_bcb.py mas de forma headless.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

TABLE_NAME  = "macro"
SOURCE_NAME = "Banco Central do Brasil (BCB/SGS)"
JOB_NAME    = "update_bcb"


def run() -> dict:
    """Baixa séries SGS do BCB e faz UPSERT em public.macro."""
    result = {
        "status":           "success",
        "table_name":       TABLE_NAME,
        "source_name":      SOURCE_NAME,
        "job_name":         JOB_NAME,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    None,
    }

    try:
        __import__("pandas")
        __import__("requests")
    except ImportError as exc:
        result["status"] = "failed"
        result["error_message"] = f"Dependência ausente: {exc}"
        return result

    from data_pipeline.utils.db_utils import get_pipeline_engine
    engine = get_pipeline_engine()
    if engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco não conectado"
        return result

    # Importa lógica do script existente sem executar main()
    try:
        import sys
        from pathlib import Path
        _root = Path(__file__).resolve().parents[3]
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))

        from scripts.seed_macro_bcb import _fetch_macro, _upsert_macro
    except ImportError as exc:
        result["status"] = "failed"
        result["error_message"] = f"Não foi possível importar seed_macro_bcb: {exc}"
        return result

    try:
        start = date(2010, 1, 1)
        end   = datetime.today().date() - timedelta(days=2)
        df, counts = _fetch_macro(start, end)

        if df.empty:
            result["status"] = "failed"
            result["error_message"] = "Nenhuma série macro retornou dados"
            return result

        with engine.begin() as conn:
            count = _upsert_macro(conn, df, apply=True)

        result["records_inserted"] = count
        logger.info("update_bcb: %d anos gravados em public.macro", count)

    except Exception as exc:
        logger.exception("update_bcb falhou")
        result["status"] = "failed"
        result["error_message"] = str(exc)

    return result

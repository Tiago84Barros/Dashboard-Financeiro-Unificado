"""
data_pipeline/jobs/update_macro.py
Importa public.macro do App 1 (SUPABASE_DB_URL_B3 / SOURCE_DB_APP1) para o App 4.

Requer SOURCE_DB_APP1 ou SUPABASE_DB_URL_B3 configurados no .env.
Se a variável não estiver presente, o job é marcado como skipped.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

TABLE_NAME  = "macro"
SOURCE_NAME = "App1 / Supabase B3"
JOB_NAME    = "update_macro"


def run() -> dict:
    """Importa tabela macro do banco do App1 para o App4."""
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

    source_url = (
        os.getenv("SOURCE_DB_APP1", "").strip()
        or os.getenv("SUPABASE_DB_URL_B3", "").strip()
    )

    if not source_url:
        logger.info("update_macro: SOURCE_DB_APP1 não configurado — pulando")
        result["status"] = "skipped"
        result["error_message"] = "SOURCE_DB_APP1/SUPABASE_DB_URL_B3 não configurado"
        return result

    try:
        import sys
        from pathlib import Path
        _root = Path(__file__).resolve().parents[3]
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))

        from scripts.import_macro_app1_to_app4 import (
            _engine, _source_columns, _normalize_macro, _create_or_extend_target, _upsert
        )
        import pandas as pd
        from sqlalchemy import text
    except ImportError as exc:
        result["status"] = "failed"
        result["error_message"] = f"Dependência ausente: {exc}"
        return result

    from data_pipeline.utils.db_utils import get_pipeline_engine
    dst_engine = get_pipeline_engine()
    if dst_engine is None:
        result["status"] = "failed"
        result["error_message"] = "Banco destino não conectado"
        return result

    try:
        src_engine = _engine(source_url)

        with src_engine.connect() as src_conn:
            columns = _source_columns(src_conn)
            df = pd.read_sql_query(text("SELECT * FROM public.macro ORDER BY ano"), src_conn)

        df = _normalize_macro(df)
        if df.empty:
            result["status"] = "failed"
            result["error_message"] = "public.macro da origem não retornou linhas válidas"
            return result

        with dst_engine.begin() as dst_conn:
            _create_or_extend_target(dst_conn, columns, apply=True)
            count = _upsert(dst_conn, df, apply=True)

        result["records_inserted"] = count
        logger.info("update_macro: %d linha(s) importadas para public.macro", count)

    except Exception as exc:
        logger.exception("update_macro falhou")
        result["status"] = "failed"
        result["error_message"] = str(exc)

    return result

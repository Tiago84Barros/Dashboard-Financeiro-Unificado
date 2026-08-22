"""
data_pipeline/utils/db_utils.py
Utilitários de banco para o pipeline — reutiliza core/database.py.
"""
from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def get_pipeline_engine() -> Engine | None:
    """Retorna a engine principal do banco unificado (mesma de core/database)."""
    try:
        from core.database import get_engine
        return get_engine()
    except Exception as exc:
        logger.error("get_pipeline_engine: %s", exc)
        return None



def table_exists(table_name: str) -> bool:
    """Verifica se uma tabela existe no banco."""
    engine = get_pipeline_engine()
    if engine is None:
        return False
    try:
        from sqlalchemy import inspect as sa_inspect
        return table_name in sa_inspect(engine).get_table_names()
    except Exception:
        return False


def ensure_pipeline_tables() -> dict[str, object]:
    """
    Cria todas as tabelas do schema (incluindo as 3 admin do pipeline).
    Delega para etl.schema_setup — DDL único, sem duplicação.
    """
    try:
        from etl.schema_setup import criar_schema
        return criar_schema()
    except Exception as exc:
        return {"ok": False, "criadas": [], "erros": [str(exc)]}

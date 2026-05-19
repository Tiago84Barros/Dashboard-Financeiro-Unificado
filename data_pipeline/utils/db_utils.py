"""
data_pipeline/utils/db_utils.py
Utilitários de banco para o pipeline — reutiliza core/database.py.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy.engine import Engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


def get_pipeline_engine() -> Engine | None:
    """Retorna a engine principal do banco unificado (mesma de core/database)."""
    try:
        from core.database import get_engine
        return get_engine()
    except Exception as exc:
        logger.error("get_pipeline_engine: %s", exc)
        return None


@contextmanager
def pipeline_conn() -> Generator:
    """Context manager que fornece uma conexão do banco do pipeline."""
    engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("Banco não configurado — verifique SUPABASE_UNIFICADO_URL")
    with engine.connect() as conn:
        yield conn


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
    Cria as 3 tabelas administrativas do pipeline se não existirem.
    Idempotente — seguro executar múltiplas vezes.
    """
    from data_pipeline.utils.db_utils import get_pipeline_engine
    engine = get_pipeline_engine()
    if engine is None:
        return {"ok": False, "erros": ["Banco não conectado"]}

    _DDL_ADMIN = [
        ("data_update_registry", """
            CREATE TABLE IF NOT EXISTS data_update_registry (
                id              BIGSERIAL PRIMARY KEY,
                table_name      TEXT NOT NULL,
                source_name     TEXT NOT NULL,
                job_name        TEXT,
                update_type     TEXT NOT NULL DEFAULT 'incremental',
                frequency       TEXT NOT NULL DEFAULT 'diario',
                priority        INTEGER DEFAULT 1,
                is_active       BOOLEAN DEFAULT TRUE,
                description     TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """),
        ("data_update_logs", """
            CREATE TABLE IF NOT EXISTS data_update_logs (
                id                      BIGSERIAL PRIMARY KEY,
                table_name              TEXT NOT NULL,
                source_name             TEXT NOT NULL,
                job_name                TEXT,
                started_at              TIMESTAMPTZ,
                finished_at             TIMESTAMPTZ,
                status                  TEXT,
                records_inserted        INTEGER DEFAULT 0,
                records_updated         INTEGER DEFAULT 0,
                records_failed          INTEGER DEFAULT 0,
                error_message           TEXT,
                execution_time_seconds  REAL,
                created_at              TIMESTAMPTZ DEFAULT NOW()
            )
        """),
        ("data_freshness_status", """
            CREATE TABLE IF NOT EXISTS data_freshness_status (
                id                    BIGSERIAL PRIMARY KEY,
                table_name            TEXT UNIQUE NOT NULL,
                source_name           TEXT NOT NULL,
                job_name              TEXT,
                last_success_at       TIMESTAMPTZ,
                last_attempt_at       TIMESTAMPTZ,
                last_status           TEXT,
                next_expected_update  TIMESTAMPTZ,
                freshness_status      TEXT DEFAULT 'never_updated',
                total_records         INTEGER DEFAULT 0,
                last_records_inserted INTEGER DEFAULT 0,
                last_records_updated  INTEGER DEFAULT 0,
                last_records_failed   INTEGER DEFAULT 0,
                last_error_message    TEXT,
                updated_at            TIMESTAMPTZ DEFAULT NOW()
            )
        """),
    ]

    criadas: list[str] = []
    erros: list[str] = []

    try:
        with engine.begin() as conn:
            for nome, ddl in _DDL_ADMIN:
                try:
                    conn.execute(text(ddl))
                    criadas.append(nome)
                except Exception as exc:
                    erros.append(f"{nome}: {exc}")
    except Exception as exc:
        return {"ok": False, "criadas": [], "erros": [str(exc)]}

    return {"ok": len(erros) == 0, "criadas": criadas, "erros": erros}

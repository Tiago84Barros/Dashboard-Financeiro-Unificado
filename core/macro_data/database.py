"""Conexão exclusiva da camada macro com o PostgreSQL Docker local."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def get_local_macro_engine() -> Engine | None:
    """Não faz fallback para o banco principal/remoto por segurança operacional."""
    from core.config import settings

    if not settings.MACRO_LOCAL_DB_URL:
        return None
    return create_engine(
        settings.MACRO_LOCAL_DB_URL,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=1,
        connect_args={"connect_timeout": 10},
    )

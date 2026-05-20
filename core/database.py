"""
core/database.py
Engine SQLAlchemy singleton com @st.cache_resource.
Centraliza toda conexão ao banco — nenhum outro módulo deve criar engine próprio.

SEGURANÇA:
- Nunca expor DATABASE_URL ou credenciais na interface.
- Usar pool_pre_ping=True para detectar conexões mortas.
- service_role_key do Supabase jamais deve ser usado aqui.
"""
import os

import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


SUPABASE_FREE_DB_LIMIT_MB = 500.0


@st.cache_resource
def get_engine():
    """
    Cria e cacheia o engine SQLAlchemy.
    Retorna None se DATABASE_URL não estiver configurada.
    """
    from core.config import settings

    url = settings.db_url
    if not url:
        return None

    is_sqlite = url.startswith("sqlite")
    kwargs: dict = {"pool_pre_ping": True}
    if not is_sqlite:
        # sslmode=require: obrigatório para Supabase no Streamlit Cloud.
        # connect_timeout: evita que o app trave se o DB não responder.
        kwargs.update(
            {
                "pool_size": 3,
                "max_overflow": 2,
                "connect_args": {
                    "connect_timeout": 10,
                    "sslmode": "require",
                },
            }
        )
    return create_engine(url, **kwargs)


@st.cache_resource
def get_session_factory():
    """
    Retorna a SessionFactory para uso com context manager.
    Retorna None se o engine não estiver disponível.
    """
    engine = get_engine()
    if engine is None:
        return None
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_connection() -> bool:
    """
    Testa a conectividade com o banco.
    Retorna True se a conexão estiver ativa, False caso contrário.
    Não lança exceção — use para health-check silencioso.
    """
    try:
        engine = get_engine()
        if engine is None:
            return False
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def get_db_status() -> dict:
    """
    Retorna dict com status da conexão para exibir em Configurações.
    Não expõe credenciais — apenas status e tipo de banco.
    """
    from core.config import settings

    conectado = test_connection()
    tem_url = settings.has_database

    return {
        "configurado": tem_url,
        "conectado": conectado,
        "mock_mode": settings.MOCK_MODE,
    }


def _database_limit_mb() -> float:
    """Retorna o limite monitorado sem expor configuração sensível."""
    raw = os.getenv("SUPABASE_DB_LIMIT_MB") or os.getenv("SUPABASE_FREE_DB_LIMIT_MB")
    if not raw:
        return SUPABASE_FREE_DB_LIMIT_MB
    try:
        value = float(str(raw).replace(",", "."))
        return value if value > 0 else SUPABASE_FREE_DB_LIMIT_MB
    except (TypeError, ValueError):
        return SUPABASE_FREE_DB_LIMIT_MB


@st.cache_data(ttl=300)
def get_database_storage_status() -> dict:
    """
    Mede o uso atual do banco PostgreSQL/Supabase.

    A consulta é somente leitura e não retorna URL, usuário, senha ou OWNER_USER_ID.
    O limite padrão acompanha o plano Free do Supabase, mas pode ser ajustado via
    SUPABASE_DB_LIMIT_MB quando o projeto mudar de plano.
    """
    engine = get_engine()
    limit_mb = _database_limit_mb()
    limit_bytes = int(limit_mb * 1024 * 1024)

    if engine is None:
        return {
            "ok": False,
            "status": "unknown",
            "message": "Banco não configurado.",
            "limit_mb": limit_mb,
            "error": None,
            "top_tables": [],
        }

    try:
        with engine.connect() as conn:
            used_bytes = int(conn.execute(
                text("SELECT pg_database_size(current_database())")
            ).scalar() or 0)

            top_rows = conn.execute(
                text(
                    """
                    SELECT
                        c.relname AS table_name,
                        pg_total_relation_size(c.oid) AS total_bytes
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind IN ('r', 'm')
                    ORDER BY pg_total_relation_size(c.oid) DESC
                    LIMIT 10
                    """
                )
            ).fetchall()

        used_mb = used_bytes / 1024 / 1024
        remaining_mb = max(limit_mb - used_mb, 0.0)
        pct_used = (used_bytes / limit_bytes * 100) if limit_bytes else 0.0

        if pct_used >= 90:
            status = "danger"
            message = "Uso quase no limite. Tome providências antes do banco entrar em modo somente leitura."
        elif pct_used >= 80:
            status = "critical"
            message = "Uso crítico. Planeje limpeza, arquivamento ou upgrade do projeto."
        elif pct_used >= 60:
            status = "attention"
            message = "Uso elevado. Vale acompanhar a evolução do tamanho do banco."
        else:
            status = "ok"
            message = "Uso dentro da margem segura."

        return {
            "ok": True,
            "status": status,
            "message": message,
            "used_bytes": used_bytes,
            "used_mb": used_mb,
            "limit_bytes": limit_bytes,
            "limit_mb": limit_mb,
            "remaining_mb": remaining_mb,
            "pct_used": pct_used,
            "top_tables": [
                {
                    "table_name": row.table_name,
                    "total_bytes": int(row.total_bytes or 0),
                    "total_mb": int(row.total_bytes or 0) / 1024 / 1024,
                }
                for row in top_rows
            ],
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "unknown",
            "message": "Não foi possível medir o uso do banco.",
            "limit_mb": limit_mb,
            "error": exc.__class__.__name__,
            "top_tables": [],
        }

"""
core/database.py
Engine SQLAlchemy singleton com @st.cache_resource.
Centraliza toda conexão ao banco — nenhum outro módulo deve criar engine próprio.

SEGURANÇA:
- Nunca expor DATABASE_URL ou credenciais na interface.
- Usar pool_pre_ping=True para detectar conexões mortas.
- service_role_key do Supabase jamais deve ser usado aqui.
"""
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


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

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        connect_args={"connect_timeout": 10},
    )


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

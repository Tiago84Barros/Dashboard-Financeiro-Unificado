"""Conexão exclusiva da camada macro com o PostgreSQL Docker local."""

from __future__ import annotations

from sqlalchemy import create_engine, text
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


def get_macro_source():
    """De onde o contexto macro das carteiras lê: Docker local ou o que ele publicou.

    O Docker vem primeiro quando responde -- é a mesma base, só que mais nova.
    Sem ele (produção, ou o container parado), vale o arquivo que a rotina de
    vitrines publica no repositório. Nunca o Supabase: continua sem fallback
    remoto, como ``get_local_macro_engine``.

    Devolve ``None`` quando nenhum dos dois serve; as telas já tratam isso como
    "sem ajuste macro".
    """
    engine = get_local_macro_engine()
    if engine is not None:
        # URL configurada não é banco de pé: um segredo apontando para
        # localhost faria a tela cair no "indisponível" mesmo com o arquivo.
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except Exception:  # noqa: BLE001 - qualquer falha de conexão cai no arquivo
            engine.dispose()
    from core.macro_data.insumos_publicados import carregar_insumos_publicados

    return carregar_insumos_publicados()


def descrever_fonte_macro(fonte) -> str:
    """Rótulo da origem para a legenda da tela."""
    from core.macro_data.insumos_publicados import InsumosMacroPublicados

    if isinstance(fonte, InsumosMacroPublicados):
        return f"Macro publicado em {fonte.gerado_em:%d/%m/%Y}"
    return "Macro Docker local"

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
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

SUPABASE_FREE_DB_LIMIT_MB = 500.0


def _com_driver_psycopg2(url: str):
    """Fixa o psycopg2 em URL Postgres sem driver explícito.

    Sem driver, o SQLAlchemy 2.1 escolhe o psycopg v3, que não está instalado:
    o pipeline caiu com "No module named 'psycopg'" de 01/10 a 03/10/2026.
    """
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    return parsed


def _fii_snapshot_connection_url(url: str):
    """Normaliza o endpoint curto para o modo transacional do Supavisor."""
    parsed = _com_driver_psycopg2(url)
    if (
        parsed.host
        and parsed.host.endswith(".pooler.supabase.com")
        and (parsed.port is None or parsed.port == 5432)
    ):
        parsed = parsed.set(port=6543)
    return parsed


#: Banco onde moram os perfis criados até 14/11/2026 e todas as vitrines.
BANCO_PRINCIPAL = "principal"
#: Banco das contas criadas pelo cadastro aberto da tela de login, desde
#: 14/11/2026. O Supabase principal está perto dos 500 MB do plano Free; cada
#: pessoa nova grava os próprios lançamentos aqui e enxerga o mercado do
#: principal por ``postgres_fdw`` (ver ``scripts/preparar_supabase_novos.py``).
BANCO_NOVOS = "novos"
BANCOS = (BANCO_PRINCIPAL, BANCO_NOVOS)


def banco_da_sessao() -> str:
    """Banco da pessoa logada nesta sessão; o principal fora de sessão.

    Jobs, CLI e testes sem ``ScriptRunContext`` caem sempre no principal, que
    é onde moram as vitrines e o administrador. Sessão sem login também: a
    tela de login lê os dois bancos explicitamente, por ``engine_do_banco``.
    """
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        if get_script_run_ctx(suppress_warning=True) is None:
            return BANCO_PRINCIPAL
        from core.user_context import principal
        banco = principal().get("banco")
    except Exception:
        return BANCO_PRINCIPAL
    return BANCO_NOVOS if banco == BANCO_NOVOS else BANCO_PRINCIPAL


def get_engine():
    """Engine do banco da pessoa logada; o principal fora de sessão.

    Ponto único de roteamento: os ~170 chamadores continuam chamando
    ``get_engine()`` e passam a cair no banco de quem está logado. Quem mora
    no banco ``novos`` lê o mercado pelas tabelas estrangeiras que o
    ``postgres_fdw`` monta lá apontando para o principal, então SQL que cruza
    lançamento com ``assets`` ou ``market.*`` continua sendo uma consulta só.
    Retorna None se o banco da sessão não estiver configurado.
    """
    return engine_do_banco(banco_da_sessao())


def engine_do_banco(banco: str):
    """Engine de um banco nomeado; None se ele não estiver configurado."""
    if banco == BANCO_NOVOS:
        return _engine_novos()
    return _engine_principal()


def novos_configurado() -> bool:
    """True quando o Supabase das contas novas tem URL nos Secrets."""
    from core.config import settings
    return bool(settings.db_url_novos)


def _limpar_engines() -> None:
    _engine_principal.clear()
    _engine_novos.clear()


get_engine.clear = _limpar_engines  # compatível com o antigo cache_resource


@st.cache_resource(show_spinner=False)
def _engine_novos():
    from core.config import settings
    url = settings.db_url_novos
    if not url:
        return None
    # Sem SUPABASE_DB_HOSTADDR: o IP fixo é do pooler do principal.
    return _criar_engine(url, hostaddr=None)


@st.cache_resource(show_spinner=False)
def _engine_principal():
    """
    Cria e cacheia o engine SQLAlchemy do banco principal.
    Retorna None se DATABASE_URL não estiver configurada.
    """
    from core.config import settings

    url = settings.db_url
    if not url:
        return None
    return _criar_engine(url, hostaddr=os.getenv("SUPABASE_DB_HOSTADDR"))


def _criar_engine(url: str, *, hostaddr: str | None):

    is_sqlite = url.startswith("sqlite")
    is_local = "localhost" in url or "127.0.0.1" in url
    kwargs: dict = {"pool_pre_ping": True}
    if not is_sqlite:
        # sslmode=require p/ Supabase; local (staging Docker) não tem SSL.
        connect_args: dict = {"connect_timeout": 10}
        if not is_local:
            connect_args["sslmode"] = "require"
            # Contorno operacional opcional para falha DNS do pooler. O host
            # lógico continua na URL; somente a resolução TCP usa o IP fornecido.
            if hostaddr:
                connect_args["hostaddr"] = hostaddr
        # Streamlit pode manter vários processos/sessões simultâneos. Um pool pequeno,
        # LIFO e reciclável evita esgotar o Supavisor sem sacrificar concorrência curta.
        kwargs.update({"pool_size": 1, "max_overflow": 2, "pool_timeout": 10,
                       "pool_recycle": 300, "pool_use_lifo": True,
                       "connect_args": connect_args})
        return create_engine(_com_driver_psycopg2(url), **kwargs)
    return create_engine(url, **kwargs)


@st.cache_resource(show_spinner=False)
def get_fii_snapshot_read_engine():
    """Engine de leitura curta da vitrine de FIIs.

    A conexão nova por tentativa evita reutilizar sockets degradados do pooler
    e mantém limites independentes das consultas analíticas mais longas.
    """
    from core.config import settings

    url = settings.db_url
    if not url:
        return None

    parsed = _fii_snapshot_connection_url(url)
    if parsed.drivername.startswith("sqlite"):
        return _engine_principal()

    is_local = parsed.host in {"localhost", "127.0.0.1", "::1"}
    if is_local:
        return _engine_principal()

    parsed = parsed.update_query_dict({"sslmode": "require"})
    connect_args: dict = {
        "connect_timeout": 8,
        "options": "-c statement_timeout=20000",
        "keepalives": 1,
        "keepalives_idle": 10,
        "keepalives_interval": 5,
        "keepalives_count": 3,
    }
    if os.getenv("SUPABASE_DB_HOSTADDR"):
        connect_args["hostaddr"] = os.environ["SUPABASE_DB_HOSTADDR"]
    return create_engine(
        parsed,
        future=True,
        poolclass=NullPool,
        connect_args=connect_args,
    )


@st.cache_resource(show_spinner=False)
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


@st.cache_data(ttl=300, show_spinner=False)
def get_database_storage_status() -> dict:
    """
    Mede o uso atual do banco PostgreSQL/Supabase.

    A consulta é somente leitura e não retorna URL, usuário, senha ou OWNER_USER_ID.
    O limite padrão acompanha o plano Free do Supabase, mas pode ser ajustado via
    SUPABASE_DB_LIMIT_MB quando o projeto mudar de plano.

    Mede sempre o principal: o cache não leva o banco da sessão na chave, e é
    o principal que está perto do limite.
    """
    engine = _engine_principal()
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

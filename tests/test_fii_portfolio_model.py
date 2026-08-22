import os
import uuid
from ipaddress import ip_address
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

import core.fii_portfolio_model as portfolio_model

OWNER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_OWNER_ID = "22222222-2222-2222-2222-222222222222"
PARAMS = {
    "methodology_version": "6.5.0",
    "strategy_id": "fii_integrated_robust_optimizer.v6.5",
}

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "supabase_unificado" / "schema"
# 018 depende de profiles (001) e do schema/função market (013), além das
# migrations FII imediatamente anteriores. A lista reproduz a ordem publicada
# em README_EXECUCAO_SQL.md, sem recriar DDL no módulo de produção.
_FII_SCHEMA_MIGRATIONS = (
    "001_core_tables.sql",
    "013_market_brapi_schema.sql",
    "015_market_fiis.sql",
    "016_fiis_cvm.sql",
    "017_fiis_detalhe.sql",
    "018_fiis_hardening.sql",
)


def _is_loopback_postgres_url(url: str) -> bool:
    """Aceita somente o PostgreSQL descartável exposto no processo local."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not parsed.host:
        return False
    if parsed.host.lower() == "localhost":
        return True
    try:
        return ip_address(parsed.host).is_loopback
    except ValueError:
        return False


def _assert_empty_disposable_database(conn) -> None:
    existing = conn.execute(text("""
        SELECT to_regclass('public.profiles') AS profiles,
               to_regclass('public.fii_portfolio_models') AS fii_models,
               to_regclass('public.fii_portfolio_model_items') AS fii_items
    """)).mappings().one()
    if any(existing.values()):
        pytest.fail(
            "FII_TEST_DATABASE_URL deve apontar para PostgreSQL novo e "
            "descartável; o bootstrap não toca banco existente."
        )


def _install_test_only_supabase_prerequisites(conn) -> None:
    """Instala apenas pré-requisitos do Supabase ausentes no postgres oficial.

    Tabelas, índices, RLS e policies continuam vindo exclusivamente das
    migrations versionadas. Este DDL vive no bootstrap de teste, nunca no
    runtime do App 4.
    """
    conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS auth")
    conn.exec_driver_sql("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                CREATE ROLE authenticated NOLOGIN;
            END IF;
        END $$
    """)
    conn.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION auth.uid()
        RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT NULL::uuid $$
    """)


def _apply_fii_schema_migrations(conn) -> None:
    for migration in _FII_SCHEMA_MIGRATIONS:
        # psycopg2 interpreta % inclusive dentro de comentários SQL quando
        # SQLAlchemy lhe passa o mapeamento vazio de parâmetros. As migrations
        # são scripts locais sem parâmetros; execute-as pelo cursor DB-API.
        cursor = conn.connection.driver_connection.cursor()
        try:
            cursor.execute((_SCHEMA_DIR / migration).read_text(encoding="utf-8"))
        finally:
            cursor.close()


@pytest.fixture(scope="session")
def _disposable_postgres_engine():
    """Banco efêmero local; não há fallback para DATABASE_URL da aplicação."""
    url = os.getenv("FII_TEST_DATABASE_URL")
    if not url:
        pytest.skip("FII_TEST_DATABASE_URL não configurada para PostgreSQL descartável")
    if not _is_loopback_postgres_url(url):
        pytest.fail("FII_TEST_DATABASE_URL deve usar host loopback, nunca remoto.")

    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            _assert_empty_disposable_database(conn)
            _install_test_only_supabase_prerequisites(conn)
            _apply_fii_schema_migrations(conn)
            conn.execute(
                text("""
                    INSERT INTO profiles (id, name, email, password_hash)
                    VALUES (:owner, 'Owner sintético', 'owner@example.test', 'hash'),
                           (:other, 'Outro sintético', 'other@example.test', 'hash')
                """),
                {"owner": OWNER_ID, "other": OTHER_OWNER_ID},
            )
        yield engine
    finally:
        engine.dispose()


def _items(second: str = "BBBB11") -> list[dict]:
    return [
        {
            "ticker": "AAAA11", "nome": "FII A", "tipo": "tijolo",
            "segmento": "logística", "weight": .6, "score": 82,
        },
        {
            "ticker": second, "nome": "FII B", "tipo": "papel",
            "segmento": "CRI", "weight": .4, "score": 78,
        },
    ]


@pytest.fixture
def postgres_engine(monkeypatch, _disposable_postgres_engine):
    engine = _disposable_postgres_engine
    # Isola cada caso sem remover profiles nem derrubar tabelas com dependentes.
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM fii_portfolio_models"))
    monkeypatch.setattr(portfolio_model, "get_engine", lambda: engine)
    monkeypatch.setattr(portfolio_model.settings, "OWNER_USER_ID", OWNER_ID)
    portfolio_model.load_active_fii_portfolio_model.clear()
    portfolio_model.list_fii_portfolio_model_versions.clear()
    yield engine
    portfolio_model.load_active_fii_portfolio_model.clear()
    portfolio_model.list_fii_portfolio_model_versions.clear()


def test_validate_model_rejects_duplicate_and_incomplete_weights():
    items = _items()
    weights = {"AAAA11": .6, "BBBB11": .4}
    model = {
        "items": items,
        "params_json": PARAMS,
        "plan_hash": portfolio_model._fii_plan_hash(items, PARAMS, weights),
    }
    assert portfolio_model.validate_fii_portfolio_model(model)["ok"] is True

    invalid = {
        **model,
        "items": [
            {"ticker": "AAAA11", "weight": .7},
            {"ticker": "AAAA11", "weight": .2},
        ],
    }
    result = portfolio_model.validate_fii_portfolio_model(invalid)
    assert result["ok"] is False
    assert "tickers duplicados" in result["reasons"]


def test_transactional_save_reload_replace_and_restore(postgres_engine):
    first_id = portfolio_model.save_fii_portfolio_model(
        _items(), PARAMS, {"trailing_yield_12m": .11}
    )
    first = portfolio_model.load_active_fii_portfolio_model()
    assert str(first["id"]) == first_id
    assert first["integrity"]["ok"] is True

    second_id = portfolio_model.save_fii_portfolio_model(
        _items("CCCC11"), PARAMS, {"trailing_yield_12m": .10}
    )
    second = portfolio_model.load_active_fii_portfolio_model()
    assert str(second["id"]) == second_id
    assert {item["ticker"] for item in second["items"]} == {"AAAA11", "CCCC11"}

    versions = portfolio_model.list_fii_portfolio_model_versions()
    assert [item["status"] for item in versions].count("active") == 1
    assert {str(item["id"]) for item in versions} == {first_id, second_id}

    restored_id = portfolio_model.restore_fii_portfolio_model(first_id)
    restored = portfolio_model.load_active_fii_portfolio_model()
    assert restored_id == first_id
    assert str(restored["id"]) == first_id
    assert restored["integrity"]["ok"] is True
    assert {item["ticker"] for item in restored["items"]} == {"AAAA11", "BBBB11"}


def test_failed_post_write_verification_rolls_back_entire_replacement(
    postgres_engine, monkeypatch,
):
    first_id = portfolio_model.save_fii_portfolio_model(_items(), PARAMS)
    original_validator = portfolio_model.validate_fii_portfolio_model
    monkeypatch.setattr(
        portfolio_model,
        "validate_fii_portfolio_model",
        lambda model: {
            "ok": False,
            "reasons": ["falha sintética"],
            "item_count": len(model.get("items") or []),
        },
    )

    with pytest.raises(RuntimeError, match="falha sintética"):
        portfolio_model.save_fii_portfolio_model(_items("CCCC11"), PARAMS)

    monkeypatch.setattr(
        portfolio_model, "validate_fii_portfolio_model", original_validator
    )
    portfolio_model.load_active_fii_portfolio_model.clear()
    active = portfolio_model.load_active_fii_portfolio_model()
    with postgres_engine.connect() as conn:
        total = conn.execute(
            text("SELECT count(*) FROM fii_portfolio_models")
        ).scalar_one()
    assert str(active["id"]) == first_id
    assert total == 1


def test_restore_rejects_version_from_another_owner(postgres_engine):
    active_id = portfolio_model.save_fii_portfolio_model(_items(), PARAMS)
    foreign_id = str(uuid.uuid4())
    with postgres_engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO fii_portfolio_models
                    (id,user_id,name,status,source,plan_hash,params_json,metrics_json)
                VALUES
                    (:id,:uid,'Outra carteira','archived','test','hash','{}','{}')
            """),
            {"id": foreign_id, "uid": OTHER_OWNER_ID},
        )

    with pytest.raises(ValueError, match="outro usuário"):
        portfolio_model.restore_fii_portfolio_model(foreign_id)
    active = portfolio_model.load_active_fii_portfolio_model()
    assert str(active["id"]) == active_id

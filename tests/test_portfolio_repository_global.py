"""Leitura do modelo ativo e persistencia da alocacao-alvo."""
import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from core.portfolio.models import AssetSnapshot
from core.portfolio.repository import (
    active_model_id,
    load_active_snapshots,
    load_allocation_targets,
    save_allocation_targets,
    save_snapshots,
)

OWNER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE portfolio_asset_snapshots (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, asset_class TEXT NOT NULL,
                model_id TEXT NOT NULL, symbol TEXT NOT NULL, schema_version INTEGER NOT NULL,
                as_of_date TEXT NOT NULL, payload TEXT NOT NULL, payload_digest TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (asset_class, model_id, symbol)
            )
        """))
        conn.execute(text("""
            CREATE TABLE portfolio_allocation_targets (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                total_brl REAL, targets_json TEXT NOT NULL DEFAULT '{}', notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        for tabela in ("b3_portfolio_models", "us_portfolio_models", "fii_portfolio_models"):
            conn.execute(text(f"""
                CREATE TABLE {tabela} (
                    id TEXT PRIMARY KEY, user_id TEXT, status TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """))
    return eng


def _modelo(engine, tabela, model_id, status, created="2026-08-01"):
    with engine.begin() as conn:
        conn.execute(
            text(f"INSERT INTO {tabela} (id, user_id, status, created_at) "
                 f"VALUES (:i, :u, :s, :c)"),
            {"i": model_id, "u": OWNER, "s": status, "c": created},
        )


def _snap(model_id, symbol):
    return AssetSnapshot.from_blocks(
        asset_class="b3", model_id=model_id, symbol=symbol,
        as_of_date=dt.date(2026, 8, 9),
        blocks={"identity": {"symbol": symbol}, "metrics": {"weight": 0.5}},
    )


def test_active_model_id_devolve_o_ativo(engine):
    _modelo(engine, "b3_portfolio_models", "m_old", "archived", "2026-07-01")
    _modelo(engine, "b3_portfolio_models", "m_new", "active", "2026-08-01")
    assert active_model_id("b3", engine=engine, owner_id=OWNER) == "m_new"


def test_active_model_id_sem_modelo_devolve_none(engine):
    assert active_model_id("us", engine=engine, owner_id=OWNER) is None


def test_load_active_snapshots_traz_apenas_o_modelo_ativo(engine):
    _modelo(engine, "b3_portfolio_models", "m_old", "archived", "2026-07-01")
    _modelo(engine, "b3_portfolio_models", "m_new", "active", "2026-08-01")
    save_snapshots([_snap("m_old", "ANTIGA3")], engine=engine, owner_id=OWNER)
    save_snapshots([_snap("m_new", "PETR4")], engine=engine, owner_id=OWNER)

    ativos = load_active_snapshots("b3", engine=engine, owner_id=OWNER)
    assert set(ativos) == {"PETR4"}


def test_load_active_snapshots_sem_modelo_devolve_vazio(engine):
    assert load_active_snapshots("fii", engine=engine, owner_id=OWNER) == {}


def test_alocacao_alvo_round_trip_normaliza_pesos(engine):
    save_allocation_targets({"b3": 50, "us": 30, "fii": 20}, total_brl=100000.0,
                            engine=engine, owner_id=OWNER)
    alvo = load_allocation_targets(engine=engine, owner_id=OWNER)
    assert alvo["targets"] == pytest.approx({"b3": 0.5, "us": 0.3, "fii": 0.2})
    assert sum(alvo["targets"].values()) == pytest.approx(1.0)
    assert alvo["total_brl"] == 100000.0


def test_alocacao_alvo_sem_registro_devolve_estrutura_vazia(engine):
    alvo = load_allocation_targets(engine=engine, owner_id=OWNER)
    assert alvo == {"targets": {}, "total_brl": None, "notes": ""}


def test_salvar_alocacao_arquiva_a_anterior(engine):
    save_allocation_targets({"b3": 100}, engine=engine, owner_id=OWNER)
    save_allocation_targets({"b3": 60, "fii": 40}, engine=engine, owner_id=OWNER)

    alvo = load_allocation_targets(engine=engine, owner_id=OWNER)
    assert alvo["targets"] == pytest.approx({"b3": 0.6, "fii": 0.4})
    assert alvo["total_brl"] is None

    with engine.connect() as conn:
        ativos = conn.execute(
            text("SELECT COUNT(*) FROM portfolio_allocation_targets WHERE status='active'")
        ).scalar()
    assert ativos == 1


def test_alocacao_com_soma_zero_e_rejeitada(engine):
    with pytest.raises(ValueError, match="soma"):
        save_allocation_targets({"b3": 0, "us": 0}, engine=engine, owner_id=OWNER)


def test_alocacao_com_peso_negativo_e_rejeitada(engine):
    with pytest.raises(ValueError, match="b3"):
        save_allocation_targets({"b3": -10, "us": 20}, engine=engine, owner_id=OWNER)


def test_alocacao_com_classe_desconhecida_e_rejeitada(engine):
    with pytest.raises(KeyError, match="cripto"):
        save_allocation_targets({"cripto": 100}, engine=engine, owner_id=OWNER)

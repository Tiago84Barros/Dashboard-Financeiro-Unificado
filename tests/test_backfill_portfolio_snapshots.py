"""Backfill de snapshots das carteiras ja salvas."""
import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from scripts import backfill_portfolio_snapshots as bf

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
            CREATE TABLE b3_portfolio_models (
                id TEXT PRIMARY KEY, user_id TEXT, status TEXT, params_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE b3_portfolio_model_items (
                model_id TEXT, ticker TEXT, nome TEXT, setor TEXT, subsetor TEXT,
                segmento TEXT, weight REAL, score REAL, alpha_selic REAL, alpha_ew REAL,
                rank_score INTEGER, ano_lider INTEGER
            )
        """))
        conn.execute(text("INSERT INTO b3_portfolio_models (id, user_id, status, params_json) "
                          "VALUES ('m01', :u, 'active', '{\"top_n\": 2}')"), {"u": OWNER})
        for tk, nome, peso in [("PETR4", "Petrobras", 0.6), ("VALE3", "Vale", 0.4)]:
            conn.execute(
                text("INSERT INTO b3_portfolio_model_items "
                     "(model_id, ticker, nome, weight, score) VALUES ('m01', :t, :n, :w, 70)"),
                {"t": tk, "n": nome, "w": peso},
            )
    return eng


def test_le_os_itens_do_modelo(engine):
    itens = bf.read_model_items("b3", "m01", engine=engine)
    assert [i["ticker"] for i in itens] == ["PETR4", "VALE3"]
    assert itens[0]["nome"] == "Petrobras"


def test_lista_apenas_o_modelo_ativo_do_dono(engine):
    modelos = bf.active_models("b3", engine=engine, owner_id=OWNER)
    assert [m["id"] for m in modelos] == ["m01"]
    assert modelos[0]["params_json"]["top_n"] == 2


def test_simulacao_nao_grava_nada(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=False, classes=["b3"])
    assert resumo["b3"] == 2

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM portfolio_asset_snapshots")).scalar() == 0


def test_apply_grava_e_marca_backfilled(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=True, classes=["b3"])
    assert resumo["b3"] == 2

    from core.portfolio.repository import load_snapshots
    lidos = load_snapshots("b3", "m01", engine=engine)
    assert set(lidos) == {"PETR4", "VALE3"}
    assert lidos["PETR4"]["provenance"]["backfilled"] is True


def test_classe_sem_carteira_nao_quebra(engine, monkeypatch):
    monkeypatch.setattr(bf, "load_adapter", lambda key: _FakeAdapter)
    resumo = bf.backfill(engine=engine, owner_id=OWNER, apply=False, classes=["b3", "us"])
    assert resumo["us"] == 0


class _FakeAdapter:
    """Adaptador sem acesso a rede: monta payload minimo a partir do item."""

    @staticmethod
    def build_snapshots(items, *, model_id, params, as_of, loaders=None):
        from core.portfolio.models import AssetSnapshot
        return [
            AssetSnapshot.from_blocks(
                asset_class="b3", model_id=model_id, symbol=i["ticker"],
                as_of_date=as_of,
                blocks={"identity": {"symbol": i["ticker"]},
                        "provenance": {"backfilled": True}},
            )
            for i in items
        ]

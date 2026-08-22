"""Repositorio de snapshots: round-trip, retencao e poda de orfaos (SQLite)."""
import datetime as dt

import pytest
from sqlalchemy import create_engine, text

from core.portfolio.models import AssetSnapshot
from core.portfolio.repository import (
    RETENTION_ARCHIVED,
    apply_retention,
    load_snapshots,
    prune_orphans,
    save_snapshots,
)

OWNER = "22222222-2222-2222-2222-222222222222"


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    with eng.begin() as conn:
        conn.execute(text("""
            CREATE TABLE portfolio_asset_snapshots (
                id             TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                asset_class    TEXT NOT NULL,
                model_id       TEXT NOT NULL,
                symbol         TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                as_of_date     TEXT NOT NULL,
                payload        TEXT NOT NULL,
                payload_digest TEXT NOT NULL,
                created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (asset_class, model_id, symbol)
            )
        """))
        conn.execute(text("""
            CREATE TABLE b3_portfolio_models (
                id TEXT PRIMARY KEY, user_id TEXT, status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        # us_ e fii_portfolio_models tambem precisam existir: prune_orphans()
        # itera sorted(SPECS) (b3, fii, us) por determinismo, entao consulta as
        # tres tabelas de modelo mesmo quando so ha snapshots de uma classe. Em
        # producao as tres sempre coexistem (migrations 047-049).
        conn.execute(text("""
            CREATE TABLE us_portfolio_models (
                id TEXT PRIMARY KEY, user_id TEXT, status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE fii_portfolio_models (
                id TEXT PRIMARY KEY, user_id TEXT, status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """))
    return eng


def _modelo(engine, model_id, status="active"):
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO b3_portfolio_models (id, user_id, status, created_at) "
                 "VALUES (:i, :u, :s, :c)"),
            {"i": model_id, "u": OWNER, "s": status, "c": f"2026-08-{int(model_id[-2:]):02d}"},
        )


def _snap(model_id, symbol, dy=1.0):
    return AssetSnapshot.from_blocks(
        asset_class="b3", model_id=model_id, symbol=symbol,
        as_of_date=dt.date(2026, 8, 5),
        blocks={"identity": {"symbol": symbol}, "metrics": {"dy": dy}},
    )


def test_round_trip_preserva_o_payload(engine):
    _modelo(engine, "m01")
    gravados = save_snapshots([_snap("m01", "PETR4"), _snap("m01", "VALE3", dy=2.0)],
                              engine=engine, owner_id=OWNER)
    assert gravados == 2

    lidos = load_snapshots("b3", "m01", engine=engine)
    assert set(lidos) == {"PETR4", "VALE3"}
    assert lidos["VALE3"]["metrics"]["dy"] == 2.0
    assert lidos["PETR4"]["schema_version"] == 1


def test_regravar_o_mesmo_ativo_atualiza_em_vez_de_duplicar(engine):
    _modelo(engine, "m01")
    save_snapshots([_snap("m01", "PETR4", dy=1.0)], engine=engine, owner_id=OWNER)
    save_snapshots([_snap("m01", "PETR4", dy=9.0)], engine=engine, owner_id=OWNER)

    lidos = load_snapshots("b3", "m01", engine=engine)
    assert len(lidos) == 1
    assert lidos["PETR4"]["metrics"]["dy"] == 9.0


def test_load_de_modelo_inexistente_devolve_vazio(engine):
    assert load_snapshots("b3", "nao-existe", engine=engine) == {}


def test_prune_remove_snapshot_de_modelo_apagado(engine):
    _modelo(engine, "m01")
    save_snapshots([_snap("m01", "PETR4")], engine=engine, owner_id=OWNER)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM b3_portfolio_models WHERE id = 'm01'"))

    assert prune_orphans(engine=engine) == 1
    assert load_snapshots("b3", "m01", engine=engine) == {}


def test_prune_nao_remove_snapshot_de_modelo_vivo(engine):
    _modelo(engine, "m01")
    save_snapshots([_snap("m01", "PETR4")], engine=engine, owner_id=OWNER)
    assert prune_orphans(engine=engine) == 0
    assert len(load_snapshots("b3", "m01", engine=engine)) == 1


def test_retencao_mantem_a_ativa_mais_as_n_ultimas_arquivadas(engine):
    _modelo(engine, "m20", status="active")
    save_snapshots([_snap("m20", "PETR4")], engine=engine, owner_id=OWNER)
    for i in range(1, 9):                      # 8 arquivadas, da mais antiga a mais nova
        mid = f"m{i:02d}"
        _modelo(engine, mid, status="archived")
        save_snapshots([_snap(mid, "PETR4")], engine=engine, owner_id=OWNER)

    removidos = apply_retention("b3", engine=engine)
    assert removidos == 8 - RETENTION_ARCHIVED   # 3 arquivadas mais antigas perdem o payload

    assert len(load_snapshots("b3", "m20", engine=engine)) == 1   # ativa preservada
    assert load_snapshots("b3", "m01", engine=engine) == {}       # mais antiga podada
    assert len(load_snapshots("b3", "m08", engine=engine)) == 1   # recente preservada


def test_save_sem_owner_configurado_levanta_erro(engine, monkeypatch):
    # Isola de OWNER_USER_ID ambiente: core.config.settings le de um .env que
    # pode estar configurado na maquina do desenvolvedor (load_dotenv sobe
    # diretorios a partir do cwd), entao o teste forca o cenario "nao
    # configurado" em vez de depender do ambiente.
    from core.config import settings
    monkeypatch.setattr(settings, "OWNER_USER_ID", "")

    _modelo(engine, "m01")
    with pytest.raises(RuntimeError, match="OWNER_USER_ID"):
        save_snapshots([_snap("m01", "PETR4")], engine=engine, owner_id=None)


def test_save_de_lista_vazia_nao_toca_o_banco(engine):
    assert save_snapshots([], engine=engine, owner_id=OWNER) == 0

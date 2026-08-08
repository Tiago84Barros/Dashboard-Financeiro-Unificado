"""Dataclass do snapshot: normalizacao e digest."""
import dataclasses
import datetime as dt

import pytest

from core.portfolio.models import AssetSnapshot
from core.portfolio.snapshots import SCHEMA_VERSION


def _snap(symbol="petr4", asset_class="b3", **kw):
    return AssetSnapshot.from_blocks(
        asset_class=asset_class,
        model_id="11111111-1111-1111-1111-111111111111",
        symbol=symbol,
        as_of_date=dt.date(2026, 8, 5),
        blocks=kw or {"identity": {"symbol": "PETR4"}},
    )


def test_symbol_e_normalizado_para_maiusculo_sem_espaco():
    assert _snap(symbol="  petr4 ").symbol == "PETR4"


def test_asset_class_e_normalizada_para_minuscula():
    assert _snap(asset_class="B3").asset_class == "b3"


def test_schema_version_vem_do_payload():
    assert _snap().schema_version == SCHEMA_VERSION


def test_digest_e_estavel_entre_instancias_iguais():
    assert _snap().digest == _snap().digest


def test_snapshot_e_imutavel():
    snap = _snap()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.symbol = "VALE3"


def test_symbol_vazio_e_rejeitado():
    with pytest.raises(ValueError, match="symbol"):
        _snap(symbol="   ")

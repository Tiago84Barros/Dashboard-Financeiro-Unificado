import types

import pandas as pd

import core.market_read as mr
import core.b3_data as facade


# ── pivot puro ────────────────────────────────────────────────────────────────

def test_pivot_metrics_wide_shape_and_missing_cols():
    long_df = pd.DataFrame([
        {"Ticker": "PETR4", "year": 2025, "metric_name": "ROE", "metric_value": 0.26},
        {"Ticker": "PETR4", "year": 2025, "metric_name": "P/L", "metric_value": 4.8},
        {"Ticker": "PETR4", "year": 2025, "metric_name": "DY", "metric_value": 0.073},
    ])
    wide = mr._pivot_metrics(long_df)
    assert list(wide["Ticker"]) == ["PETR4"]
    assert abs(float(wide.iloc[0]["ROE"]) - 0.26) < 1e-9
    assert abs(float(wide.iloc[0]["P/L"]) - 4.8) < 1e-9
    # todas as colunas canônicas presentes; Liquidez_Corrente ausente -> NaN
    for c in mr._MULT_COLS:
        assert c in wide.columns
    assert pd.isna(wide.iloc[0]["Liquidez_Corrente"])


def test_pivot_metrics_empty():
    wide = mr._pivot_metrics(pd.DataFrame())
    assert wide.empty and "P/L" in wide.columns


# ── facade: feature flag ──────────────────────────────────────────────────────

def _fakes(monkeypatch):
    legacy = types.SimpleNamespace(
        load_setores=lambda *a, **k: "LEGACY_SET",
        load_multiplos_todos=lambda *a, **k: "LEGACY_MT",
        load_demonstracoes=lambda *a, **k: "LEGACY_DEM",
    )
    market = types.SimpleNamespace(
        load_setores=lambda *a, **k: "MARKET_SET",
        load_multiplos_todos=lambda *a, **k: "MARKET_MT",
    )
    monkeypatch.setattr(facade, "_legacy", legacy)
    monkeypatch.setattr(facade, "_market", market)


def test_read_source_default_legacy(monkeypatch):
    monkeypatch.delenv("MARKET_READ_SOURCE", raising=False)
    assert facade.read_source() == "legacy"


def test_read_source_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MARKET_READ_SOURCE", "banana")
    assert facade.read_source() == "legacy"


def test_dispatch_legacy_default(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "legacy")
    assert facade.load_setores() == "LEGACY_SET"


def test_dispatch_market_for_supported(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    assert facade.load_setores() == "MARKET_SET"
    assert facade.load_multiplos_todos() == "MARKET_MT"


def test_dispatch_unsupported_always_legacy(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    # demonstrações não têm paridade em market.* -> sempre legado
    assert facade.load_demonstracoes() == "LEGACY_DEM"


def test_dispatch_compare_returns_legacy(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "compare")
    # compare roda os dois mas retorna o legado (seguro p/ UI)
    assert facade.load_setores() == "LEGACY_SET"


def test_dispatch_market_failure_falls_back(monkeypatch):
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_setores = boom
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    assert facade.load_setores() == "LEGACY_SET"

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
        load_macro_history=lambda *a, **k: "LEGACY_MACRO",
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
    monkeypatch.setenv("MARKET_READ_FORCE", "1")  # ignora porteiro de cobertura
    assert facade.load_setores() == "MARKET_SET"
    assert facade.load_multiplos_todos() == "MARKET_MT"


def test_dispatch_unsupported_always_legacy(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    monkeypatch.setenv("MARKET_READ_FORCE", "1")
    # macro não tem paridade em market.* -> sempre legado
    assert facade.load_macro_history() == "LEGACY_MACRO"


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
    monkeypatch.setenv("MARKET_READ_FORCE", "1")  # ignora porteiro de cobertura
    assert facade.load_setores() == "LEGACY_SET"


# ── porteiro de cobertura (cutover Fase 3) ────────────────────────────────────

def _coverage_fakes(monkeypatch, n_legacy, n_market):
    def _df(n):
        return pd.DataFrame({"Ticker": [f"T{i}" for i in range(n)]})
    legacy = types.SimpleNamespace(
        load_setores=lambda *a, **k: "LEGACY_SET",
        load_multiplos_todos=lambda *a, **k: _df(n_legacy))
    market = types.SimpleNamespace(
        load_setores=lambda *a, **k: "MARKET_SET",
        load_multiplos_todos=lambda *a, **k: _df(n_market))
    monkeypatch.setattr(facade, "_legacy", legacy)
    monkeypatch.setattr(facade, "_market", market)


def test_market_coverage_ratio(monkeypatch):
    _coverage_fakes(monkeypatch, 100, 82)
    monkeypatch.delenv("MARKET_READ_MIN_COVERAGE", raising=False)
    cov = facade.market_coverage()
    assert cov["legacy"] == 100 and cov["market"] == 82
    assert cov["ratio"] == 0.82 and cov["ready"] is False  # 82% < 90%


def test_gate_blocks_market_when_coverage_low(monkeypatch):
    _coverage_fakes(monkeypatch, 100, 50)
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    monkeypatch.delenv("MARKET_READ_FORCE", raising=False)
    # cobertura 50% < 90% -> porteiro mantém no legado
    assert facade.load_setores() == "LEGACY_SET"


def test_gate_allows_market_when_coverage_high(monkeypatch):
    _coverage_fakes(monkeypatch, 100, 96)
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    monkeypatch.delenv("MARKET_READ_FORCE", raising=False)
    assert facade.load_setores() == "MARKET_SET"  # 96% >= 90%


def test_gate_force_override(monkeypatch):
    _coverage_fakes(monkeypatch, 100, 10)
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    monkeypatch.setenv("MARKET_READ_FORCE", "1")  # ignora cobertura baixa
    assert facade.load_setores() == "MARKET_SET"


def test_gate_custom_threshold(monkeypatch):
    _coverage_fakes(monkeypatch, 100, 82)
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    monkeypatch.setenv("MARKET_READ_MIN_COVERAGE", "0.80")  # baixa o limiar
    monkeypatch.delenv("MARKET_READ_FORCE", raising=False)
    assert facade.load_setores() == "MARKET_SET"  # 82% >= 80%


# ── Dados financeiros: fonte ÚNICA = market.* (legado desativado) ─────────────

def test_financeiro_sempre_market_mesmo_em_legacy(monkeypatch):
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "legacy")  # flag legado é IGNORADA
    monkeypatch.delenv("MARKET_READ_FORCE", raising=False)
    assert facade.load_multiplos_todos() == "MARKET_MT"  # nunca "LEGACY_MT"


def test_financeiro_market_erro_retorna_vazio_nao_legado(monkeypatch):
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_multiplos_todos = boom
    monkeypatch.setenv("MARKET_READ_SOURCE", "market")
    out = facade.load_multiplos_todos()
    # vazio (nulo = ausente), JAMAIS cai no legado public.multiplos
    assert hasattr(out, "empty") and out.empty


# ── market_active(): cutover financeiro concluído → sempre True ────────────────

def test_market_active_sempre_true(monkeypatch):
    # Independe da flag/cobertura: financeiro vem só do market → reparos OFF.
    for src in ("legacy", "market", "compare"):
        monkeypatch.setenv("MARKET_READ_SOURCE", src)
        assert facade.market_active() is True

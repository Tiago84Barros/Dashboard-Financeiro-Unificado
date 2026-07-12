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


# ── facade pós-cutover: financeiro/setores SEMPRE market.* ────────────────────
# O gate de cobertura e o _dispatch por flag foram APOSENTADOS (cutover
# concluído; tabelas legadas de fundamentos dropadas). A flag
# MARKET_READ_SOURCE é só informativa (read_source) e não muda a origem.

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


def test_financeiro_sempre_market_para_qualquer_flag(monkeypatch):
    _fakes(monkeypatch)
    for src in ("legacy", "market", "compare"):
        monkeypatch.setenv("MARKET_READ_SOURCE", src)
        assert facade.load_multiplos_todos() == "MARKET_MT"  # nunca "LEGACY_MT"


def test_financeiro_market_erro_retorna_vazio_nao_legado(monkeypatch):
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_multiplos_todos = boom
    out = facade.load_multiplos_todos()
    # vazio (nulo = ausente), JAMAIS cai no legado public.multiplos (dropada)
    assert hasattr(out, "empty") and out.empty


def test_setores_prefere_market(monkeypatch):
    # setores prefere market.* (herança de setor ON->PN pela raiz de 4 letras)
    _fakes(monkeypatch)
    monkeypatch.setenv("MARKET_READ_SOURCE", "legacy")
    assert facade.load_setores() == "MARKET_SET"


def test_setores_fallback_legado_quando_market_falha(monkeypatch):
    _fakes(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("market down")
    facade._market.load_setores = boom
    # setores é REFERÊNCIA (não-financeiro): mantém fallback ao legado
    assert facade.load_setores() == "LEGACY_SET"


def test_macro_segue_no_legado(monkeypatch):
    _fakes(monkeypatch)
    # macro/selic/snapshot são de outros domínios — seguem no legado
    assert facade.load_macro_history() == "LEGACY_MACRO"


def test_market_active_sempre_true(monkeypatch):
    # Cutover concluído: reparos defensivos das telas ficam sempre OFF.
    for src in ("legacy", "market", "compare"):
        monkeypatch.setenv("MARKET_READ_SOURCE", src)
        assert facade.market_active() is True

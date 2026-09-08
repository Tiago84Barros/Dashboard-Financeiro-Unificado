"""Regressões sintéticas dos achados M1 e M2 da revisão macro."""
from datetime import datetime, timezone

import pandas as pd
import pytest

from core.fii_portfolio_v4 import (
    MacroScenario,
    PortfolioPolicy,
    optimize_diligence_portfolio,
)
from core.macro_data.portfolio_context import (
    PortfolioMacroSnapshot,
    historical_macro_weight_path,
)
from tests.test_fii_portfolio_v4 import _candidate


@pytest.mark.parametrize("mode,expected", [("fundamental", 0), ("moderate", 2), ("scenario", 3)])
def test_fii_adjustment_matches_shared_mode_convention(mode, expected):
    rows = [_candidate(i, t) for i, t in enumerate(["tijolo", "papel", "fof", "hibrido"] * 3)]
    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5), policy=PortfolioPolicy(),
        macro_impacts={r["ticker"]: 50 for r in rows}, macro_mode=mode,
    )
    assert result["items"]
    assert all(r["macro_score_adjustment"] == pytest.approx(expected) for r in result["items"])


@pytest.mark.parametrize("symbols", [["A", "OUTSIDE"], ["A"], ["A", "A"]])
def test_history_rejects_changed_composition(monkeypatch, symbols):
    cutoff = datetime(2025, 12, 31, tzinfo=timezone.utc)
    snap = PortfolioMacroSnapshot({"A": 20, "B": -20}, (), cutoff, 2, 2, 1)
    monkeypatch.setattr("core.macro_data.portfolio_context.load_portfolio_macro_snapshot", lambda *a, **kw: snap)
    base = pd.DataFrame({"symbol": ["A", "B"], "tipo": ["tijolo", "papel"],
                         "type_score": [50, 50], "weight": [.5, .5]})

    def rebuild(*args):
        return pd.DataFrame({"symbol": symbols, "weight": 1 / len(symbols),
                             "weight_before_macro": 1 / len(symbols), "macro_impact": 20})

    with pytest.raises(ValueError, match="composição"):
        historical_macro_weight_path(object(), asset_class="fii", holdings=base,
            symbol_column="symbol", sector_column="tipo", score_column="type_score",
            cutoffs=[cutoff], rebuild=rebuild)


def test_fii_rebuild_uses_only_requested_holdings(monkeypatch):
    from core.macro_data.fii_history import rebuild_fii_macro_history

    seen = {}
    def optimizer(rows, scenario, **kwargs):
        seen["symbols"] = {r["ticker"] for r in rows}
        return {"items": rows}
    monkeypatch.setattr("core.macro_data.fii_history.optimize_diligence_portfolio", optimizer)
    base = pd.DataFrame({"symbol": ["A", "B"], "weight": [.6, .4]})
    result = rebuild_fii_macro_history(base, {"A": 20, "B": -20}, "moderate",
                                      scenario=MacroScenario(selic=12, ipca=5),
                                      policy=PortfolioPolicy())
    assert seen["symbols"] == {"A", "B"}
    assert result.symbol.tolist() == ["A", "B"]
    assert base.columns.tolist() == ["symbol", "weight"]


def test_real_fii_history_preserves_universe_and_constraints(monkeypatch):
    from functools import partial

    from core.macro_data.fii_history import rebuild_fii_macro_history

    scenario, policy = MacroScenario(selic=12, ipca=5), PortfolioPolicy()
    rows = [_candidate(i, t) for i, t in enumerate(["tijolo", "papel", "fof", "hibrido"] * 3)]
    initial = optimize_diligence_portfolio(rows, scenario, policy=policy)
    base = pd.DataFrame(initial["items"]).rename(columns={"ticker": "symbol"})
    cutoff = datetime(2025, 12, 31, tzinfo=timezone.utc)
    consulted = []

    def snapshot(*args, **kwargs):
        consulted.append(set(kwargs["assets"]))
        return PortfolioMacroSnapshot(dict.fromkeys(kwargs["assets"], 50), (), cutoff,
                                      len(base), len(base), 1)

    monkeypatch.setattr("core.macro_data.portfolio_context.load_portfolio_macro_snapshot", snapshot)
    result = historical_macro_weight_path(
        object(), asset_class="fii", holdings=base, symbol_column="symbol",
        sector_column="tipo", score_column="type_score", cutoffs=[cutoff], mode="scenario",
        rebuild=partial(rebuild_fii_macro_history, scenario=scenario, policy=policy),
    )
    assert consulted == [set(base.symbol)]
    assert set(result.symbol) == set(base.symbol)
    assert result.weight_contextual.sum() == pytest.approx(1)
    assert result.weight_contextual.max() <= policy.max_asset + 1e-6
    assert ((result.weight_contextual - result.weight_fundamental).abs()
            <= .15 * result.weight_fundamental + 1e-8).all()

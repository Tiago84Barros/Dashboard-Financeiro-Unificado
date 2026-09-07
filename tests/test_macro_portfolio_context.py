from datetime import datetime, timezone

import pandas as pd
import pytest

from core.macro_data.portfolio_context import (
    PortfolioMacroSnapshot,
    aggregate_impact_rows,
    format_portfolio_macro_context,
    historical_macro_weight_path,
    observation_applies_to_asset_class,
)


def test_aggregate_impacts_uses_confidence_and_preserves_missing():
    result = aggregate_impact_rows([
        {"symbol": "AAA", "direction": "positive", "intensity": 80, "confidence": 75},
        {"symbol": "AAA", "direction": "negative", "intensity": 20, "confidence": 25},
        {"symbol": "BBB", "direction": "neutral", "intensity": 90, "confidence": 50},
    ])

    assert result["AAA"] == pytest.approx(55.0)
    assert result["BBB"] == 0.0
    assert "SEM_COBERTURA" not in result


def test_llm_context_exposes_cutoff_coverage_and_limitations():
    snapshot = PortfolioMacroSnapshot(
        impacts={"AAA": 12.5}, details=(),
        as_of=datetime(2026, 8, 31, tzinfo=timezone.utc),
        asset_count=2, covered_assets=1, source_count=4,
        limitations=("parte dos ativos sem mapeamento",),
    )

    context = format_portfolio_macro_context(snapshot)

    assert "2026-08-31" in context
    assert "50.0%" in context
    assert "AAA: impacto agregado=+12.50/100" in context
    assert "LLM apenas explica" in context


def test_snapshot_defaults_to_strict_knowledge_mode():
    snapshot = PortfolioMacroSnapshot(
        impacts={}, details=(), as_of=datetime.now(timezone.utc),
        asset_count=0, covered_assets=0, source_count=0,
    )

    assert snapshot.knowledge_mode == "strict"


def test_geography_is_not_mixed_between_portfolios():
    us_rate = {"country_code": "US", "category": "monetary_policy",
               "provider_code": "FEDFUNDS"}
    br_rate = {"country_code": "BRA", "category": "monetary_policy",
               "provider_code": "selic"}
    brl_fx = {"country_code": "EA20", "category": "currencies",
              "provider_code": "EXR|M.BRL.EUR.SP00.A"}

    assert observation_applies_to_asset_class("us", us_rate)
    assert not observation_applies_to_asset_class("b3", us_rate)
    assert observation_applies_to_asset_class("b3", br_rate)
    assert observation_applies_to_asset_class("fii", brl_fx)


def test_historical_path_is_explicitly_reconstructed(monkeypatch):
    def fake_snapshot(*args, **kwargs):
        assert kwargs["knowledge_mode"] == "reconstructed"
        return PortfolioMacroSnapshot(
            impacts={"AAA": 50.0}, details=(), as_of=kwargs["as_of"],
            asset_count=2, covered_assets=1, source_count=1,
            knowledge_mode="reconstructed",
        )

    monkeypatch.setattr(
        "core.macro_data.portfolio_context.load_portfolio_macro_snapshot",
        fake_snapshot,
    )
    holdings = pd.DataFrame({
        "ticker": ["AAA", "BBB"], "sector": ["X", "Y"],
        "score": [70.0, 60.0], "weight": [.5, .5],
    })
    result = historical_macro_weight_path(
        object(), asset_class="b3", holdings=holdings,
        symbol_column="ticker", sector_column="sector", score_column="score",
        cutoffs=[datetime(2015, 12, 31, tzinfo=timezone.utc)],
    )

    assert set(result["knowledge_mode"]) == {"reconstructed"}
    assert result["weight_contextual"].sum() == pytest.approx(1.0)
    assert result.loc[result["symbol"] == "BBB", "macro_impact"].isna().all()

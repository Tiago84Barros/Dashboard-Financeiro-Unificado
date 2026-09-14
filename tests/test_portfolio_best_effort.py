from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.fii_methodology import MacroScenario
from core.fii_portfolio_v4 import PortfolioPolicy
from core.portfolio_best_effort import allocate_partial
from core.portfolio_review_routes import b3_review, fii_review, us_review
from core.us_portfolio_creation import USPortfolioCreationParams


def test_partial_does_not_renormalize_above_caps():
    result = allocate_partial([{"ticker": "A", "quality": 90}, {"ticker": "B", "quality": 60}],
                              [.10, .10])
    assert result["allocated_weight"] == pytest.approx(.20)
    assert result["unallocated_weight"] == pytest.approx(.80)
    assert max(row["weight"] for row in result["items"]) <= .10


def test_joint_constraints_and_cardinality_preserve_capital():
    result = allocate_partial([{"ticker": str(i), "quality": i} for i in range(20)],
                              [.10]*20, upper_constraints=[(np.ones(20), .35)],
                              max_assets=3, min_weight=.02)
    assert result["allocated_weight"] == pytest.approx(.30)
    assert len(result["items"]) == 3
    assert {row["ticker"] for row in result["items"]} == {"17", "18", "19"}


def test_empty_and_invalid_inputs():
    assert allocate_partial([], [])["unallocated_weight"] == 1
    with pytest.raises(ValueError):
        allocate_partial([{"ticker": "A", "quality": float("nan")}], [.10])


def test_time_limit_keeps_verified_incumbent_without_claiming_optimality(monkeypatch):
    import core.portfolio_best_effort as allocator

    monkeypatch.setattr(allocator, "milp", lambda *_args, **_kwargs:
                        SimpleNamespace(success=False, x=np.array([.1, 1.])))
    result = allocate_partial([{"ticker": "A", "quality": 80}], [.1])
    assert result["items"]
    assert result["solver_status"] == "feasible"
    assert result["unallocated_weight"] == pytest.approx(.9)


def test_fii_17_candidates_have_partial_portfolio_with_shared_credit_exposure():
    from tests.test_fii_portfolio_v4 import _candidate

    kinds = ["tijolo"]*4 + ["papel"]*10 + ["fof"]*2 + ["hibrido"]
    rows = [_candidate(i, kind) for i, kind in enumerate(kinds)]
    for row in rows:
        row["income_recurrence"] = .9
        if row["tipo"] in {"papel", "hibrido"}:
            row["issuers"] = {"shared": 1.0}
    result = fii_review(rows, PortfolioPolicy(max_assets=14, max_asset=.1),
                        MacroScenario(selic=15, ipca=4))
    assert result["items"]
    assert 0 < result["allocated_weight"] < 1
    assert sum(row["weight"] for row in result["items"]
               if row["tipo"] in {"papel", "hibrido"}) <= .1+1e-6
    assert all(row["weight"] <= .1 for row in result["items"])
    assert result["allocated_weight"] + result["unallocated_weight"] == pytest.approx(1)
    assert not result["can_publish"]


def test_fii_unknown_critical_exposure_and_low_confidence_do_not_receive_weight():
    rows = [{"ticker": "UNKNOWN", "tipo": "papel", "type_score": 90, "confidence": .9},
            {"ticker": "LOW", "tipo": "fof", "type_score": 90, "confidence": .1}]
    result = fii_review(rows, PortfolioPolicy(), MacroScenario(selic=15, ipca=4))
    assert not result["items"]
    assert result["unallocated_weight"] == 1


def test_us_industry_not_approved_does_not_hide_safe_individual_candidate():
    frame = pd.DataFrame([
        {"symbol": "SAFE", "entry_score": 45, "entry_status": "Observação",
         "sector_group": "Saúde", "industry_group": "Hospitais"},
        {"symbol": "RISK", "entry_score": 90, "entry_status": "Excluída",
         "sector_group": "Saúde", "industry_group": "Hospitais"}])
    result = us_review(frame, USPortfolioCreationParams(max_weight=.08))
    assert [row["ticker"] for row in result["items"]] == ["SAFE"]
    assert result["unallocated_weight"] == pytest.approx(.92)


def test_b3_historical_segment_gate_is_soft_but_critical_health_is_hard(monkeypatch):
    import core.b3_holdings_health as health

    monkeypatch.setattr(health, "check_holdings", lambda *_args, **_kwargs: [
        SimpleNamespace(ticker="SAFE3", nivel=health.OK),
        SimpleNamespace(ticker="RISK3", nivel=health.CRITICO)])
    results = [{"score_proximo": {"SAFE3": 60, "RISK3": 90}, "setor": "Saúde"}]
    guard = {ticker: {"score_entrada": 50, "status_entrada": "Observação"}
             for ticker in ("SAFE3", "RISK3")}
    result = b3_review(results, pd.DataFrame(), guard, cap=.1, sector_cap=.25,
                       cycle_cap=.4, selic=15)
    assert [row["ticker"] for row in result["items"]] == ["SAFE3"]
    assert result["unallocated_weight"] == pytest.approx(.9)
    assert not b3_review(results, pd.DataFrame(), guard, cap=.1, sector_cap=.25,
                         cycle_cap=.4, selic=15, vetoed={"SAFE3"})["items"]


def test_us_real_creation_route_returns_review_without_changing_original_approval(monkeypatch):
    import core.us_portfolio_creation as creation

    eligible = pd.DataFrame([{"symbol": "SAFE", "entry_score": 45, "entry_status": "Observação",
                              "sector_group": "Saúde", "industry_group": "Hospitais"}])
    audit = pd.DataFrame([{"industry_group": "Hospitais", "status": "Observação",
                           "avg_entry_score": 45}])
    monkeypatch.setattr(creation, "prepare_eligible_universe", lambda *_: (eligible, pd.DataFrame()))
    monkeypatch.setattr(creation, "build_industry_audit", lambda *_: audit)
    result = creation.build_portfolio_creation(eligible)
    assert result["review_portfolio"]["items"][0]["ticker"] == "SAFE"
    assert result["industry_audit"].iloc[0]["status"] == "Observação"
    assert not result["can_publish"]


def test_review_component_renders_partial_weights_and_empty_capital():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string('''
from core.portfolio_best_effort import allocate_partial
from design.portfolio_review import render_portfolio_review
result = allocate_partial([{"ticker": "SYNTH", "quality": 90}], [.1])
render_portfolio_review(result, key="synthetic")
render_portfolio_review(allocate_partial([], []), key="empty")
''').run(timeout=20)
    assert not app.exception
    assert app.metric[0].value == "10.0%"
    assert app.metric[1].value == "90.0%"
    assert app.metric[3].value == "100.0%"
    assert app.dataframe[0].value.iloc[0]["Ativo"] == "SYNTH"

import pytest

from core.fii_methodology import MacroScenario
from core.fii_portfolio_v4 import (PortfolioPolicy, optimize_diligence_portfolio,
                                   portfolio_constraint_violations)


def _candidate(i: int, fii_type: str, complete: bool = True) -> dict:
    row = {
        "ticker": f"F{i:03d}11", "tipo": fii_type, "type_score": 80 - i,
        "confidence": .9, "coverage": .95, "publication_status": "validated",
        "dy_12m": .10, "liquidez_diaria": 3_000_000,
        "manager": f"gestor-{i}", "sector": f"setor-{i}",
    }
    if fii_type in ("tijolo", "hibrido"):
        row.update(tenants={f"locatario-{i}": 1.0}, regions={f"regiao-{i}": 1.0})
    if fii_type in ("papel", "hibrido"):
        row.update(debtors={f"devedor-{i}": 1.0}, issuers={f"emissor-{i}": 1.0},
                   indexers={f"indexador-{i}": 1.0})
    if not complete:
        row.pop("manager")
    return row


def test_optimizer_respects_asset_limit_and_reports_scenarios():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=11, ipca=4, selic_change_12m=-2.5),
        policy=PortfolioPolicy(max_assets=12, max_asset=.15),
    )
    assert result["items"]
    assert max(item["weight"] for item in result["items"]) <= .15001
    assert abs(sum(item["weight"] for item in result["items"]) - 1) < 1e-6
    assert {"selic_alta", "vacancia", "credito"}.issubset(result["scenario_returns"])


def test_default_uncertainty_cap_allows_diligence_portfolio_without_publication():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) | {"confidence": .68,
                                      "publication_status": "diligence_only"}
            for i, fii_type in enumerate(types)]

    result = optimize_diligence_portfolio(rows, MacroScenario(selic=15, ipca=4.5))

    assert result["items"]
    assert not result["can_publish"]
    assert result["weighted_uncertainty"] <= .35 + 1e-6


def test_candidate_preselection_keeps_enough_confidence_for_final_constraint():
    types = ["tijolo", "papel", "fof", "hibrido"] * 4
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows[:12]:
        row["confidence"] = .55
        row["type_score"] += 30

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
        policy=PortfolioPolicy(max_assets=12, max_weighted_uncertainty=.35),
    )

    assert result["items"]
    assert result["weighted_uncertainty"] <= .35 + 1e-6


def test_infeasible_confidence_is_reported_as_missing_data_prerequisite():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [
        _candidate(i, fii_type) | {"confidence": .40}
        for i, fii_type in enumerate(types)
    ]

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
    )

    assert result["items"] == []
    assert result["failure_stage"] == "data_prerequisites"


def test_optimizer_adapts_bands_when_one_type_has_no_candidate():
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(
        ["tijolo", "papel", "fof"] * 4
    )]

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
    )

    assert result["items"]
    assert "hibrido" in result["band_adaptation"]["unavailable_types"]
    assert result["macro_bands"]["hibrido"] == (0.0, 0.0)
    assert max(
        sum(item["weight"] for item in result["items"] if item["tipo"] == fii_type)
        for fii_type in {"tijolo", "papel", "fof"}
    ) <= .70 + 1e-6


def test_optimizer_blocks_with_only_one_eligible_type():
    rows = [_candidate(i, "tijolo") for i in range(12)]

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=4.5),
    )

    assert result["items"] == []
    assert "menos de 2 categorias" in result["blockers"][0]


def test_turnover_penalty_retains_feasible_previous_holdings():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    previous = {row["ticker"]: 1 / len(rows) for row in rows}
    perturbed = [
        row | {"type_score": row["type_score"] + (0.05 if index % 2 else 0)}
        for index, row in enumerate(rows)
    ]

    result = optimize_diligence_portfolio(
        perturbed, MacroScenario(selic=12, ipca=4.5),
        policy=PortfolioPolicy(turnover_penalty=.05),
        previous_weights=previous,
    )

    selected = {item["ticker"] for item in result["items"]}
    assert result["items"]
    assert len(selected.intersection(previous)) >= 8


def test_missing_exposure_coverage_blocks_publication():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows[:8]:
        row.pop("sector")
    result = optimize_diligence_portfolio(rows, MacroScenario(selic=12, ipca=5),
                                          policy=PortfolioPolicy(max_assets=12))
    assert not result["can_publish"]
    assert "sector" in result.get("unresolved_dimensions", [])


def test_preselection_reserves_documented_assets_for_required_coverage():
    types = ["tijolo", "papel", "fof", "hibrido"] * 6
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows[:12]:
        row.pop("sector", None)
        if row["tipo"] in {"papel", "hibrido"}:
            row.pop("issuers", None)
        row["type_score"] += 30

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert result["dimension_coverage"]["sector"]["coverage"] >= .80
    assert result["dimension_coverage"]["issuer"]["coverage"] >= .80
    assert all(item["weight"] >= .02 - 1e-6 for item in result["items"])


def test_sector_coverage_applies_only_to_property_funds():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        if row["tipo"] in {"papel", "fof"}:
            row.pop("sector", None)

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert result["dimension_coverage"]["sector"]["coverage"] == 1.0


def test_manager_without_historical_identity_is_conditional_and_explicit():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        row.pop("manager", None)

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert "manager" in result["unresolved_dimensions"]
    assert "manager" not in result["unresolved_critical_dimensions"]


def test_structurally_unavailable_tenant_identity_is_explicit_but_not_blocking():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        row.pop("tenants", None)

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
    )

    assert result["items"]
    assert result["can_publish"]
    assert "tenant" in result["unresolved_dimensions"]
    assert "tenant" not in result["unresolved_critical_dimensions"]


def test_optimizer_finds_feasible_seed_when_equal_weights_break_illiquid_cap():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    for row in rows:
        if row["tipo"] == "hibrido":
            row["liquidez_diaria"] = 100_000
    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12, max_illiquid=.10),
    )
    assert result["items"]
    illiquid_weight = sum(item["weight"] for item in result["items"]
                           if item["liquidez_diaria"] < 1_000_000)
    assert illiquid_weight <= .10001


def test_candidate_reservation_diversifies_sector_when_band_exceeds_sector_cap():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    paper = [row for row in rows if row["tipo"] == "papel"]
    paper[0]["sector"] = "CRI"
    paper[1]["sector"] = "CRI"
    paper[2]["sector"] = "Agro"
    paper[2]["type_score"] = 1
    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=15, ipca=4, selic_change_12m=1),
        policy=PortfolioPolicy(max_assets=12, max_sector=.25),
    )
    assert result["items"]
    assert any(item["ticker"] == paper[2]["ticker"] for item in result["items"])


def test_optimizer_uses_observed_correlation_with_explicit_coverage():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    tickers = [row["ticker"] for row in rows]
    correlation = {
        ticker: {other: (1.0 if ticker == other else .35) for other in tickers}
        for ticker in tickers
    }

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12),
        correlation_matrix=correlation, correlation_penalty=.12,
    )

    assert result["items"]
    assert result["correlation_risk"] is not None
    assert result["correlation_info"]["coverage"] == 1.0
    assert result["correlation_penalty"] == .12


def test_final_portfolio_is_revalidated_after_weight_normalization():
    policy = PortfolioPolicy(max_asset=.15, max_assets=12)
    bands = {"tijolo": (.25, .55), "papel": (.20, .50),
             "fof": (.05, .20), "hibrido": (.05, .20)}
    items = [_candidate(i, fii_type) | {"weight": weight}
             for i, (fii_type, weight) in enumerate([
                 ("tijolo", .30), ("tijolo", .20),
                 ("papel", .20), ("papel", .10),
                 ("fof", .10), ("hibrido", .10),
             ])]

    violations = portfolio_constraint_violations(items, bands, policy)

    assert "peso individual acima do limite" in violations


def test_final_portfolio_rejects_dust_positions_below_economic_minimum():
    policy = PortfolioPolicy(min_asset_weight=.02)
    bands = {"tijolo": (0.0, 1.0), "papel": (0.0, 1.0),
             "fof": (0.0, 1.0), "hibrido": (0.0, 1.0)}
    items = [
        _candidate(0, "tijolo") | {"weight": .99},
        _candidate(1, "papel") | {"weight": .01},
    ]

    violations = portfolio_constraint_violations(items, bands, policy)

    assert "peso individual abaixo do mínimo econômico" in violations


def test_low_correlation_coverage_blocks_publication_when_penalty_is_enabled():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type) for i, fii_type in enumerate(types)]
    correlation = {rows[0]["ticker"]: {rows[1]["ticker"]: .4}}

    result = optimize_diligence_portfolio(
        rows, MacroScenario(selic=12, ipca=5),
        policy=PortfolioPolicy(max_assets=12, min_correlation_coverage=.80),
        correlation_matrix=correlation, correlation_penalty=.12,
    )

    assert result["items"]
    assert not result["can_publish"]
    assert any("cobertura de correlação" in reason for reason in result["blockers"])
    assert result["trailing_yield_12m"] == result["expected_yield"]


@pytest.mark.parametrize("macro", [
    MacroScenario(selic=15, ipca=4.5, selic_change_12m=1.5),
    MacroScenario(selic=9, ipca=3.5, selic_change_12m=-3.0),
    MacroScenario(selic=13, ipca=7.0, selic_change_12m=0.5),
])
def test_portfolio_matrix_remains_feasible_across_macro_regimes(macro):
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [
        _candidate(i, fii_type) | {
            "pvp": .9, "duration_anos": 3.5, "leverage": .08,
            "vacancia_fisica": .06, "delinquency": .01, "ltv": .55,
        }
        for i, fii_type in enumerate(types)
    ]

    result = optimize_diligence_portfolio(
        rows, macro, policy=PortfolioPolicy(max_assets=12, max_asset=.15),
    )

    assert result["items"]
    assert result["constraint_violations"] == []
    assert abs(sum(item["weight"] for item in result["items"]) - 1) < 1e-6
    assert 0 < result["effective_assets"] <= 12

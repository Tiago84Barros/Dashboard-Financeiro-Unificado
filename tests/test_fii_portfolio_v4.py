from core.fii_methodology import MacroScenario
from core.fii_portfolio_v4 import PortfolioPolicy, optimize_diligence_portfolio


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


def test_missing_exposure_coverage_blocks_publication():
    types = ["tijolo", "papel", "fof", "hibrido"] * 3
    rows = [_candidate(i, fii_type, complete=i > 4) for i, fii_type in enumerate(types)]
    result = optimize_diligence_portfolio(rows, MacroScenario(selic=12, ipca=5),
                                          policy=PortfolioPolicy(max_assets=12))
    assert not result["can_publish"]
    assert "manager" in result.get("unresolved_dimensions", [])


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

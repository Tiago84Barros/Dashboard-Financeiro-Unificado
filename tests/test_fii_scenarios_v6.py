from core.fii_scenarios import asset_scenario_return


def test_asset_specific_credit_risk_worsens_credit_scenario():
    safe = {"tipo": "papel", "confidence": .9, "ltv": .45,
            "delinquency": 0, "issuance_concentration": .1}
    risky = {"tipo": "papel", "confidence": .5, "ltv": .9,
             "delinquency": .1, "issuance_concentration": .7}
    assert asset_scenario_return(risky, "credito") < asset_scenario_return(safe, "credito")


def test_unknown_data_has_ambiguity_penalty():
    known = {"tipo": "tijolo", "confidence": 1.0}
    unknown = {"tipo": "tijolo", "confidence": .2}
    assert asset_scenario_return(unknown, "vacancia") < asset_scenario_return(known, "vacancia")

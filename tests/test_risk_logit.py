import pandas as pd

from core.risk_logit import distress_risk_score


def test_distress_risk_score_increases_with_red_flags():
    df = pd.DataFrame([
        {
            "ROE": 0.18,
            "ROIC": 0.12,
            "Margem_Liquida": 0.16,
            "Endividamento_Total": 1.2,
            "Liquidez_Corrente": 1.8,
            "P/VP": 2.0,
            "P_FCO": 12.0,
        },
        {
            "ROE": -0.25,
            "ROIC": -0.04,
            "Margem_Liquida": -0.18,
            "Endividamento_Total": 10.0,
            "Liquidez_Corrente": 0.25,
            "P/VP": 18.0,
            "P_FCO": 90.0,
        },
    ])

    result = distress_risk_score(df)

    assert result.loc[1, "risk_probability"] > result.loc[0, "risk_probability"]
    assert result.loc[1, "r_penalty"] > result.loc[0, "r_penalty"]
    assert result.loc[1, "risk_driver"] != "none"

import pandas as pd

from core.data_reconciliacao import (
    batch_multiplos_reconciliados,
    clean_multiplos_history_batch,
    infer_zero_as_missing_fields,
)


def test_batch_reconciliation_treats_mass_zero_as_missing_and_uses_web():
    rows = []
    for i in range(21):
        rows.append({
            "Ticker": f"TST{i:02d}3",
            "DY": 0.0,
            "Payout": 0.0,
            "ROE": 250.0 if i == 0 else 0.12,
            "P/VP": 1.5,
        })
    df_base = pd.DataFrame(rows)

    fund_data = {
        "TST003": {"dy": 6.5, "roe": 15.0, "pvp": 1.8},
    }
    status_data = {
        "TST003": {"Payout": 0.45},
    }

    reconciled, audit, summary = batch_multiplos_reconciliados(
        tuple(df_base["Ticker"]),
        df_base=df_base,
        include_status=True,
        fund_data=fund_data,
        status_data=status_data,
    )

    row = reconciled.set_index("Ticker").loc["TST003"]
    assert round(float(row["DY"]), 4) == 0.065
    assert round(float(row["ROE"]), 4) == 0.15
    assert round(float(row["Payout"]), 4) == 0.45
    assert "DY" in summary["campos_zero_suspeito"]
    assert "Payout" in summary["campos_zero_suspeito"]
    assert set(audit[audit["Ticker"].eq("TST003")]["Indicador"]) >= {"DY", "ROE", "Payout"}


def test_clean_history_batch_nulls_outliers():
    hist = {
        "TEST3": pd.DataFrame({
            "Data": ["2023-12-31", "2024-12-31"],
            "ROIC": [0.12, 999.0],
            "Margem_Liquida": [0.10, 200.0],
        })
    }

    cleaned, audit = clean_multiplos_history_batch(hist)

    assert pd.isna(cleaned["TEST3"].loc[1, "ROIC"])
    assert pd.isna(cleaned["TEST3"].loc[1, "Margem_Liquida"])
    assert int(audit["Ocorrencias"].sum()) == 2


def test_zero_suspect_requires_universe_pattern():
    df = pd.DataFrame({
        "Ticker": ["AAA3", "BBB3", "CCC3"],
        "Payout": [0.0, 0.0, 0.2],
    })

    assert infer_zero_as_missing_fields(df) == set()


def test_batch_reconciliation_inferrs_zero_pattern_before_portfolio_slice():
    df_base = pd.DataFrame([
        {"Ticker": f"TST{i:02d}3", "Payout": 0.0, "DY": 0.0, "P/L": 10.0}
        for i in range(25)
    ])

    reconciled, _audit, summary = batch_multiplos_reconciliados(
        ("TST003", "TST013"),
        df_base=df_base,
        fund_data={},
        status_data={},
    )

    assert "Payout" in summary["campos_zero_suspeito"]
    assert "DY" in summary["campos_zero_suspeito"]
    assert reconciled["Payout"].isna().all()
    assert reconciled["DY"].isna().all()

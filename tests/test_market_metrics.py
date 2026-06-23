import data_pipeline.market.metrics as mx


_F = {
    "revenue": 1000.0, "net_income": 200.0, "ebit": 300.0, "ebitda": 350.0,
    "total_assets": 2000.0, "equity": 1000.0, "cash": 100.0,
    "gross_debt": 500.0, "net_debt": 400.0, "fco": 250.0,
    "market_cap": 2000.0,
    "div_ttm": 3.0, "price": 100.0, "eps": 10.0,  # base POR AÇÃO p/ DY e Payout
    "current_assets": 600.0, "current_liabilities": 400.0,  # Liquidez = 1.5
}


def test_compute_snapshot_core_ratios():
    s = {k: v for k, (v, _m) in mx.compute_snapshot(_F).items()}
    assert abs(s["Margem_Liquida"] - 0.20) < 1e-6      # 200/1000
    assert abs(s["Margem_Operacional"] - 0.30) < 1e-6  # 300/1000
    assert abs(s["ROE"] - 0.20) < 1e-6                 # 200/1000
    assert abs(s["ROA"] - 0.10) < 1e-6                 # 200/2000
    assert abs(s["ROIC"] - 300 / 1400) < 1e-6          # 300/(1000+500-100)
    assert abs(s["Endividamento_Total"] - 0.50) < 1e-6 # 500/1000
    assert abs(s["Liquidez_Corrente"] - 1.50) < 1e-6   # 600/400
    assert abs(s["P/L"] - 10.0) < 1e-6                 # 2000/200
    assert abs(s["P/VP"] - 2.0) < 1e-6                 # 2000/1000
    assert abs(s["EV_EBIT"] - 8.0) < 1e-6              # (2000+400)/300
    assert abs(s["P_FCO"] - 8.0) < 1e-6                # 2000/250
    assert abs(s["DY"] - 0.03) < 1e-6                  # 3/100 (por ação)
    assert abs(s["Payout"] - 0.30) < 1e-6             # 3/10  (dps/LPA)


def test_compute_snapshot_drops_out_of_range():
    # margem impossível (>100%) e divisão por zero → omitidos
    s = mx.compute_snapshot({"revenue": 10.0, "net_income": 50.0,   # margem 500%
                             "equity": 0.0, "market_cap": 100.0})
    assert "Margem_Liquida" not in s   # 5.0 fora da faixa [-1,1]
    assert "ROE" not in s              # div por zero


def test_compute_snapshot_partial_inputs():
    # só com receita e lucro → calcula só o possível, sem quebrar
    s = mx.compute_snapshot({"revenue": 100.0, "net_income": 10.0})
    assert abs(s["Margem_Liquida"][0] - 0.10) < 1e-6
    assert "ROE" not in s and "P/L" not in s


def test_to_metric_rows_shape():
    rows = mx.to_metric_rows("PETR4", mx.compute_snapshot(_F))
    assert all(r["ticker"] == "PETR4" and r["period"] == "ttm" for r in rows)
    assert all(r["source"] == "market.compute" for r in rows)
    names = {r["metric_name"] for r in rows}
    assert {"ROE", "P/L", "Margem_Liquida"} <= names


def test_to_metric_rows_annual_low_conf():
    rows = mx.to_metric_rows("PETR4", mx.compute_snapshot(_F),
                             period="annual", year=2024, low_conf=mx.ANNUAL_APPROX)
    by = {r["metric_name"]: r for r in rows}
    assert all(r["period"] == "annual" and r["year"] == 2024 for r in rows)
    # valuation aproximado: confiança menor + sufixo ~aprox no método
    assert by["P/L"]["confidence_score"] == 60.0
    assert by["P/L"]["calculation_method"].endswith("~aprox")
    # fundamentais exatos: confiança cheia, sem sufixo
    assert by["ROE"]["confidence_score"] == 85.0
    assert "~aprox" not in by["ROE"]["calculation_method"]

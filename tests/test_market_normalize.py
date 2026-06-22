from datetime import date, datetime, timezone

import data_pipeline.market.normalize as nz


def _ts(y, m, d):
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


# Payload Pro sintético (results[0]) com módulos.
_Q = {
    "symbol": "PETR4", "longName": "Petroleo Brasileiro SA", "currency": "BRL",
    "logourl": "https://icons.brapi.dev/icons/PETR4.svg",
    "marketCap": 5.0e11, "priceEarnings": 5.0, "earningsPerShare": 8.0,
    "summaryProfile": {"sector": "Energy", "industry": "Oil & Gas",
                       "website": "https://petrobras.com.br", "longBusinessSummary": "Petróleo."},
    "defaultKeyStatistics": {"priceToBook": 1.4, "enterpriseToEbitda": 3.1, "trailingEps": 8.0},
    "financialData": {"returnOnEquity": 0.24, "profitMargins": 0.21},
    "historicalDataPrice": [
        {"date": _ts(2024, 12, 30), "open": 30.0, "high": 31.0, "low": 29.5, "close": 30.5,
         "adjustedClose": 30.0, "volume": 1000},
        {"date": _ts(2025, 12, 30), "open": 40.0, "high": 41.0, "low": 39.0, "close": 40.0,
         "adjustedClose": 40.0, "volume": 2000},
    ],
    "incomeStatementHistory": [
        {"endDate": "2025-12-31", "totalRevenue": 500.0, "grossProfit": 200.0,
         "ebit": 150.0, "ebitda": 180.0, "netIncome": 100.0},
    ],
    "incomeStatementHistoryQuarterly": [
        {"endDate": "2025-09-30", "totalRevenue": 130.0, "netIncome": 25.0},
    ],
    "balanceSheetHistory": [
        {"endDate": "2025-12-31", "totalAssets": 1000.0, "totalLiab": 600.0,
         "totalStockholderEquity": 400.0, "cash": 80.0, "totalDebt": 300.0},
    ],
    "cashflowHistory": [
        {"endDate": "2025-12-31", "totalCashFromOperatingActivities": 200.0,
         "capitalExpenditures": -50.0},
    ],
    "dividendsData": {"cashDividends": [
        {"paymentDate": "2025-06-20T03:00:00.000Z", "lastDatePrior": "2025-06-01T03:00:00.000Z",
         "rate": 3.0, "label": "JCP"},
    ]},
}


def test_company_and_asset():
    c = nz.company_row(_Q)
    assert c["name"].startswith("Petroleo") and c["sector"] == "Energy"
    assert c["logo_url"].endswith("PETR4.svg")
    a = nz.asset_row(_Q)
    assert a["ticker"] == "PETR4" and a["currency"] == "BRL" and a["is_active"] is True


def test_price_rows():
    rows = nz.price_rows(_Q)
    assert len(rows) == 2
    assert rows[0]["date"] == date(2024, 12, 30) and rows[0]["adjusted_close"] == 30.0
    assert rows[1]["volume"] == 2000


def test_income_rows_annual_and_quarterly():
    rows = nz.income_rows(_Q)
    ann = [r for r in rows if r["period"] == "annual"][0]
    assert ann["year"] == 2025 and ann["quarter"] == 0 and ann["revenue"] == 500.0
    q = [r for r in rows if r["period"] == "quarterly"][0]
    assert q["year"] == 2025 and q["quarter"] == 3 and q["net_income"] == 25.0


def test_balance_and_cashflow():
    b = nz.balance_rows(_Q)[0]
    assert b["total_assets"] == 1000.0 and b["equity"] == 400.0 and b["gross_debt"] == 300.0
    cf = nz.cashflow_rows(_Q)[0]
    assert cf["operating_cash_flow"] == 200.0 and cf["capex"] == -50.0
    assert cf["free_cash_flow"] == 150.0   # 200 + (-50)


def test_dividend_rows():
    d = nz.dividend_rows(_Q)[0]
    assert d["amount"] == 3.0 and d["type"] == "JCP"
    assert d["payment_date"] == date(2025, 6, 20)


def test_metric_rows():
    by = {r["metric_name"]: r["metric_value"] for r in nz.metric_rows(_Q)}
    assert by["P/L"] == 5.0 and by["P/VP"] == 1.4 and by["EV/EBITDA"] == 3.1
    assert by["ROE"] == 0.24 and by["marketCap"] == 5.0e11


def test_normalize_all_keys():
    out = nz.normalize_all(_Q)
    assert set(out) >= {"companies", "assets", "historical_prices", "income_statements",
                        "balance_sheets", "cash_flow_statements", "dividends", "calculated_metrics"}
    assert len(out["companies"]) == 1 and len(out["historical_prices"]) == 2

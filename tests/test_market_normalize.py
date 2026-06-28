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
    "defaultKeyStatistics": {"priceToBook": 1.4, "enterpriseToEbitda": 3.1, "trailingEps": 8.0,
                             "dividendYield": 0.069},
    "financialData": {"returnOnEquity": 0.24, "profitMargins": 0.21,
                      "returnOnAssets": 0.09, "operatingMargins": 0.29},
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


def test_income_eps_scale_and_fallback():
    # basicEarningsPerCommonShare vem em milireais (LPA*1000) -> /1000
    q = {"symbol": "PETR4", "incomeStatementHistory": [
        {"endDate": "2025-12-31", "netIncome": 100.0, "basicEarningsPerCommonShare": 8540}]}
    assert abs(nz.income_rows(q)[0]["eps"] - 8.54) < 1e-9
    # sem o campo escalado, cai p/ earningsPerShare (já em reais)
    q2 = {"symbol": "X", "incomeStatementHistory": [
        {"endDate": "2025-12-31", "netIncome": 10.0, "earningsPerShare": 1.25}]}
    assert nz.income_rows(q2)[0]["eps"] == 1.25
    # zero/ausente -> None (ex.: VALE reporta beps=0)
    q3 = {"symbol": "VALE3", "incomeStatementHistory": [
        {"endDate": "2025-12-31", "netIncome": 50.0, "basicEarningsPerCommonShare": 0}]}
    assert nz.income_rows(q3)[0]["eps"] is None


def test_balance_and_cashflow():
    b = nz.balance_rows(_Q)[0]
    assert b["total_assets"] == 1000.0 and b["equity"] == 400.0 and b["gross_debt"] == 300.0
    cf = nz.cashflow_rows(_Q)[0]
    assert cf["operating_cash_flow"] == 200.0 and cf["capex"] == -50.0
    assert cf["free_cash_flow"] == 150.0   # 200 + (-50)


def test_balance_b3_real_shape():
    # forma real da BRAPI p/ B3: equity vem em 'shareholdersEquity' (não
    # 'stockholdersEquity'); sem totalDebt/netDebt → soma empréstimos e deriva net.
    q = {"symbol": "PETR4", "balanceSheetHistory": [{
        "endDate": "2025-12-31", "totalAssets": 1223.0, "totalLiab": 805.0,
        "totalStockholderEquity": None, "shareholdersEquity": 417.0,
        "cash": 35.0, "totalDebt": None, "shortLongTermDebt": None, "netDebt": None,
        "loansAndFinancing": 67.0, "longTermLoansAndFinancing": 317.0,
        "debentures": 0, "longTermDebentures": 0,
        "totalCurrentAssets": 140.0, "totalCurrentLiabilities": None,
        "currentLiabilities": 198.0,
    }]}
    b = nz.balance_rows(q)[0]
    assert b["equity"] == 417.0
    assert b["gross_debt"] == 384.0          # 67 + 317 (+ debêntures 0)
    assert b["net_debt"] == 384.0 - 35.0     # gross_debt - cash
    assert b["current_assets"] == 140.0
    assert b["current_liabilities"] == 198.0  # fallback p/ currentLiabilities


def test_dividend_rows():
    d = nz.dividend_rows(_Q)[0]
    assert d["amount"] == 3.0 and d["type"] == "JCP"
    assert d["payment_date"] == date(2025, 6, 20)


def test_dividend_rows_saneamento():
    # regressão: pular amount<=0 e anular payment_date impossível (9999/futuro
    # distante) p/ o event_date gerado (COALESCE(payment_date, ex_date)) cair no ex.
    from datetime import datetime, timezone
    ano_fut = datetime.now(timezone.utc).year + 3
    q = {"symbol": "TST3", "dividendsData": {"cashDividends": [
        {"rate": 1.5, "paymentDate": "2026-08-20", "lastDatePrior": "2026-07-10"},   # ok
        {"rate": 0.0, "paymentDate": "2026-08-20", "lastDatePrior": "2026-07-10"},   # zero -> fora
        {"rate": -1, "paymentDate": "2026-08-20", "lastDatePrior": "2026-07-10"},    # negativo -> fora
        {"rate": 2.0, "paymentDate": "9999-12-31", "lastDatePrior": "2025-12-30"},   # 9999 -> payment None
        {"rate": 0.5, "paymentDate": f"{ano_fut}-12-31", "lastDatePrior": "2025-12-18"},  # futuro -> payment None
    ]}}
    rows = nz.dividend_rows(q)
    assert len(rows) == 3                                  # zero e negativo eliminados
    assert all(r["amount"] > 0 for r in rows)
    nove = next(r for r in rows if r["amount"] == 2.0)
    assert nove["payment_date"] is None and nove["ex_date"] == date(2025, 12, 30)
    fut = next(r for r in rows if r["amount"] == 0.5)
    assert fut["payment_date"] is None                    # event_date cairá no ex_date


def test_metric_rows():
    by = {r["metric_name"]: r["metric_value"] for r in nz.metric_rows(_Q)}
    assert by["P/L"] == 5.0 and by["P/VP"] == 1.4 and by["EV/EBITDA"] == 3.1
    assert by["ROE"] == 0.24 and by["marketCap"] == 5.0e11
    # trailing da brapi (consenso) — usado p/ corrigir distorção do último ano
    assert by["ROA"] == 0.09 and by["Margem_Operacional"] == 0.29
    assert by["DY"] == 0.069


def test_normalize_all_keys():
    out = nz.normalize_all(_Q)
    assert set(out) >= {"companies", "assets", "historical_prices", "income_statements",
                        "balance_sheets", "cash_flow_statements", "dividends", "calculated_metrics"}
    assert len(out["companies"]) == 1 and len(out["historical_prices"]) == 2

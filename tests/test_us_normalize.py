"""Testes da normalização Empresas Americanas (unidades, temporalidade, sem zero-fill)."""
from datetime import date

import data_pipeline.us.normalize as nz


def test_to_float_distingue_ausente_de_zero():
    assert nz.to_float(None) is None
    assert nz.to_float("") is None
    assert nz.to_float("None") is None
    assert nz.to_float("NaN") is None
    assert nz.to_float(0) == 0.0          # zero explícito preservado
    assert nz.to_float("0") == 0.0
    assert nz.to_float("1,234.5") == 1234.5
    assert nz.to_float(True) is None      # bool não é número financeiro


def test_scale_to_absolute():
    assert nz.scale_to_absolute(5, "millions") == 5_000_000
    assert nz.scale_to_absolute(5, "thousands") == 5_000
    assert nz.scale_to_absolute(5, "absolute") == 5
    assert nz.scale_to_absolute(None, "millions") is None


def test_normalize_percent_auto():
    assert nz.normalize_percent(0.23) == 0.23        # já ratio
    assert nz.normalize_percent(23) == 0.23          # 23% → 0.23
    assert nz.normalize_percent(None) is None
    assert nz.normalize_percent(50, "pct") == 0.5
    assert nz.normalize_percent(0.5, "ratio") == 0.5


def test_parse_date_e_available_date():
    assert nz.parse_date("2024-03-31") == date(2024, 3, 31)
    assert nz.parse_date("2024-03-31 10:00:00") == date(2024, 3, 31)
    assert nz.parse_date("") is None
    # available_at prefere acceptedDate (data em que o filing ficou público)
    row = {"date": "2024-03-31", "fillingDate": "2024-05-01",
           "acceptedDate": "2024-05-02 16:30:00"}
    assert nz.available_date(row) == date(2024, 5, 2)
    # sem acceptedDate cai para filling
    assert nz.available_date({"date": "2024-03-31", "fillingDate": "2024-05-01"}) == date(2024, 5, 1)


def test_parse_period():
    assert nz.parse_period({"period": "FY", "calendarYear": "2023"}) == ("annual", 2023, 0)
    assert nz.parse_period({"period": "Q3", "calendarYear": "2023"}) == ("quarterly", 2023, 3)
    # sem calendarYear usa o ano da data
    assert nz.parse_period({"period": "FY", "date": "2022-12-31"}) == ("annual", 2022, 0)


def test_map_income_statement_pit_e_sem_zero():
    row = {"date": "2023-12-31", "calendarYear": "2023", "period": "FY",
           "acceptedDate": "2024-02-01 16:00:00", "reportedCurrency": "USD",
           "revenue": 1000, "netIncome": 0, "grossProfit": None}
    out = nz.map_income_statement(row)
    assert out["period"] == "annual" and out["fiscal_year"] == 2023
    assert out["available_at"] == date(2024, 2, 1)
    assert out["revenue"] == 1000.0
    assert out["net_income"] == 0.0        # zero explícito preservado
    assert out["gross_profit"] is None     # ausente NÃO vira zero
    assert out["currency"] == "USD"
    assert isinstance(out["content_hash"], str) and len(out["content_hash"]) == 64


def test_map_balance_e_cashflow():
    b = nz.map_balance_sheet({"date": "2023-12-31", "period": "FY",
                              "calendarYear": "2023", "totalAssets": 500,
                              "totalLiabilities": 300, "totalStockholdersEquity": 200})
    assert b["total_assets"] == 500 and b["total_equity"] == 200
    cf = nz.map_cash_flow({"date": "2023-12-31", "period": "FY", "calendarYear": "2023",
                           "operatingCashFlow": 100, "capitalExpenditure": -30,
                           "freeCashFlow": 70})
    assert cf["operating_cash_flow"] == 100 and cf["capex"] == -30 and cf["free_cash_flow"] == 70


def test_map_profile_classifica_reit():
    reit = nz.map_profile({"symbol": "O", "companyName": "Realty Income",
                           "sector": "Real Estate", "industry": "REIT - Retail",
                           "cik": "0000726728"})
    assert reit["security_type"] == "reit" and reit["is_reit"] is True
    common = nz.map_profile({"symbol": "aapl", "companyName": "Apple",
                             "sector": "Technology", "industry": "Consumer Electronics"})
    assert common["symbol"] == "AAPL" and common["security_type"] == "common"

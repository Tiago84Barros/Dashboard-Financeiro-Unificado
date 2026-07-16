"""Testes de identidade/reconciliação Empresas Americanas."""
from datetime import date

import data_pipeline.us.identity as idy


def test_normalize_cik_zero_pad():
    assert idy.normalize_cik("320193") == "0000320193"
    assert idy.normalize_cik("0000320193") == "0000320193"
    assert idy.normalize_cik("CIK0000320193") == "0000320193"
    assert idy.normalize_cik(None) is None
    assert idy.normalize_cik("") is None


def test_normalize_symbol_classe():
    assert idy.normalize_symbol("brk.b") == "BRK-B"
    assert idy.normalize_symbol("  aapl ") == "AAPL"
    assert idy.normalize_symbol(None) is None
    assert idy.symbols_equivalent("BRK.B", "BRK-B") is True


def test_detect_symbol_divergence():
    assert idy.detect_symbol_divergence("AAPL", "AAPL") is None
    assert idy.detect_symbol_divergence("AAPL", "") is None
    div = idy.detect_symbol_divergence("AAPL", "MSFT")
    assert div and div["requested"] == "AAPL" and div["returned"] == "MSFT"


def test_is_operating_company():
    assert idy.is_operating_company("common") is True
    assert idy.is_operating_company("reit") is True
    assert idy.is_operating_company("etf") is False
    assert idy.is_operating_company("spac") is False


def test_eligible_for_analysis():
    ok, reason = idy.eligible_for_analysis(
        {"security_type": "common", "ipo_date": date(2010, 1, 1)},
        current_year=2026)
    assert ok is True and reason is None

    bad, why = idy.eligible_for_analysis({"security_type": "etf"})
    assert bad is False and "não-operacional" in why

    adr_off, why2 = idy.eligible_for_analysis(
        {"security_type": "adr"}, include_adr=False)
    assert adr_off is False

    no_stmt, why3 = idy.eligible_for_analysis(
        {"security_type": "common"}, has_statements=False)
    assert no_stmt is False and "demonstra" in why3

    young, why4 = idy.eligible_for_analysis(
        {"security_type": "common", "ipo_date": date(2025, 1, 1)},
        current_year=2026, min_history_years=2)
    assert young is False and "histórico" in why4


def test_resolve_current_symbol_segue_cadeia():
    aliases = [
        {"old_symbol": "FB", "new_symbol": "META", "reason": "rename"},
        {"old_symbol": "GOOG_OLD", "new_symbol": "GOOGL", "reason": "rename"},
    ]
    assert idy.resolve_current_symbol("FB", aliases) == "META"
    assert idy.resolve_current_symbol("AAPL", aliases) == "AAPL"  # sem alias → ele mesmo

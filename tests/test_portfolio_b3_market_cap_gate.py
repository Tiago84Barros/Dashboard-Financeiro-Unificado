import pytest

from views.portfolio_b3 import _market_cap_coverage, _ticker_key


def test_ticker_key_normalizes_b3_suffix_and_case():
    assert _ticker_key(" petr4.sa ") == "PETR4"
    assert _ticker_key("VALE3") == "VALE3"


def test_market_cap_coverage_distinguishes_missing_data_from_small_caps():
    caps = {
        "PETR4": 500_000_000_000.0,
        "VALE3": 250_000_000_000.0,
        # Abaixo do piso ainda é dado válido e conta na cobertura.
        "SMAL3": 900_000_000.0,
    }

    covered, total, ratio = _market_cap_coverage(
        ["PETR4.SA", "VALE3", "SMAL3", "AUSENTE3"], caps
    )

    assert (covered, total) == (3, 4)
    assert ratio == pytest.approx(0.75)


def test_market_cap_coverage_deduplicates_normalized_tickers():
    covered, total, ratio = _market_cap_coverage(
        ["PETR4", "PETR4.SA", "VALE3"], {"PETR4": 1.0}
    )

    assert (covered, total) == (1, 2)
    assert ratio == pytest.approx(0.5)

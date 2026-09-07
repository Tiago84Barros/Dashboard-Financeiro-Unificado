import pytest

from core.macro_data.domestic_bridge import _normalized_value


def test_domestic_bridge_normalizes_only_known_decimal_selic():
    assert _normalized_value("selic", 0.1375) == pytest.approx(13.75)
    assert _normalized_value("selic", 13.75) == 13.75
    assert _normalized_value("ipca", 4.5) == 4.5
    assert _normalized_value("ipca", None) is None
    assert _normalized_value("pib", float("nan")) is None

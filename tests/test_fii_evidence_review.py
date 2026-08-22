
import pytest

from core.fii_evidence_review import _numeric_value


def test_numeric_value_accepts_json_scalar_and_wrapped_value():
    assert _numeric_value(0.075) == pytest.approx(0.075)
    assert _numeric_value({"value": "4.2"}) == pytest.approx(4.2)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "not-a-number"])
def test_numeric_value_rejects_non_finite_or_invalid_values(value):
    with pytest.raises((TypeError, ValueError)):
        _numeric_value(value)

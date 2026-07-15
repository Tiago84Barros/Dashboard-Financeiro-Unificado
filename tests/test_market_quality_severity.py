import pytest

from data_pipeline.market.repository import _normalize_quality_severity


@pytest.mark.parametrize(
    ("provided", "expected"),
    [
        ("info", "info"),
        ("warn", "warn"),
        ("critical", "critical"),
        ("warning", "warn"),
        ("error", "critical"),
        (" WARNING ", "warn"),
    ],
)
def test_normalize_quality_severity(provided, expected):
    assert _normalize_quality_severity(provided) == expected


def test_normalize_quality_severity_rejects_unknown_value():
    with pytest.raises(ValueError, match="severity invalida"):
        _normalize_quality_severity("high")

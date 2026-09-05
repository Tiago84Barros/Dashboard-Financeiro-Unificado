from core.macro_data.sector_policy import validated_sector_exposures


def test_initial_sector_policy_is_bounded_and_explicit():
    rows = validated_sector_exposures()
    assert rows
    assert all(-1 <= sensitivity <= 1 and 0 <= confidence <= 1 for _, _, _, sensitivity, confidence, _ in rows)
    assert all(sector and factor and channel for _, sector, factor, _, _, channel in rows)

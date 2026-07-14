import pytest

from core.fii_confidence import beta_posterior, calibration_factor


def test_beta_posterior_is_conservative_and_improves_with_reviews():
    prior = beta_posterior(0, 0, 0)
    strong = beta_posterior(30, 1, 1)
    assert prior["reviewed"] == 0
    assert strong["lower_bound"] > prior["lower_bound"]
    assert strong["posterior_mean"] > .85


def test_calibration_is_neutral_without_reviews_and_penalizes_observed_error():
    assert calibration_factor(.8, reviewed=0, posterior_mean=.5, lower_bound=.2) == pytest.approx(.8)
    calibrated = calibration_factor(.8, reviewed=30, posterior_mean=.7, lower_bound=.5)
    assert calibrated < .8
    assert calibrated > 0

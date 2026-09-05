import pandas as pd
import pytest

from core.macro_data.portfolio_tilt import (
    MacroTiltConfig,
    apply_macro_scores,
    apply_macro_tilt,
)


def _holdings():
    return pd.DataFrame({"symbol": ["A", "B", "C"], "score": [70.0, 60.0, 50.0], "weight": [0.5, 0.3, 0.2]})


def test_fundamental_mode_preserves_weights_and_marks_missing_coverage():
    result = apply_macro_tilt(_holdings(), {"A": 80}, symbol_column="symbol", score_column="score", mode="fundamental")
    assert result["weight"].tolist() == pytest.approx([0.5, 0.3, 0.2])
    assert result["macro_covered"].tolist() == [True, False, False]
    assert pd.isna(result.loc[1, "macro_impact"])


def test_moderate_tilt_is_normalized_bounded_and_turnover_limited():
    result = apply_macro_tilt(
        _holdings(), {"A": 100, "B": -100, "C": 100},
        symbol_column="symbol", score_column="score",
        config=MacroTiltConfig(max_score_adjustment=10, max_relative_weight_tilt=1, max_turnover=0.03),
    )
    assert result["weight"].sum() == pytest.approx(1.0)
    assert result.attrs["macro_turnover"] <= 0.0300001
    assert result["macro_score_adjustment"].abs().max() <= 10


def test_invalid_weights_are_rejected():
    broken = _holdings()
    broken.loc[0, "weight"] = -0.1
    with pytest.raises(ValueError, match="pesos inválidos"):
        apply_macro_tilt(broken, {}, symbol_column="symbol", score_column="score")


def test_score_context_keeps_missing_as_missing_and_does_not_create_weights():
    frame = pd.DataFrame({"symbol": ["A", "B"], "score": [70.0, 60.0]})

    result = apply_macro_scores(
        frame, {"A": 50.0}, symbol_column="symbol", score_column="score"
    )

    assert result.loc[0, "contextual_score"] == 75.0
    assert result.loc[1, "contextual_score"] == 60.0
    assert pd.isna(result.loc[1, "macro_impact"])
    assert "weight" not in result

from datetime import datetime, timezone

import pandas as pd

from data_pipeline.utils.date_utils import fmt_datetime_br


def test_fmt_datetime_br_formats_valid_timestamp():
    assert fmt_datetime_br(datetime(2026, 8, 2, 18, tzinfo=timezone.utc)) == (
        "02/08/2026 15:00"
    )


def test_fmt_datetime_br_treats_dataframe_missing_sentinels_as_never():
    assert fmt_datetime_br(float("nan")) == "Nunca"
    assert fmt_datetime_br(pd.NaT) == "Nunca"

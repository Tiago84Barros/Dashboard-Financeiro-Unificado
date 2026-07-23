from scripts.publish_b3_vintages_from_local import COLS, DDL


def test_b3_vintage_snapshot_is_idempotent_and_private():
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_vintage_artifact" in DDL
    assert "ENABLE ROW LEVEL SECURITY" in DDL
    assert "REVOKE ALL" in DDL
    assert "TRUNCATE" not in DDL and "DROP TABLE" not in DDL
    assert "availability_quality" in COLS

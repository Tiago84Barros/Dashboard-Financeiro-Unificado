from scripts.compact_remote_brapi_raw import COMPACTION_SQL


def test_remote_raw_compaction_recreates_a_secure_empty_audit_table():
    upper = COMPACTION_SQL.upper()
    assert "DROP TABLE MARKET.BRAPI_RAW_PAYLOADS CASCADE" in upper
    assert "CREATE TABLE MARKET.BRAPI_RAW_PAYLOADS" in upper
    assert "ENABLE ROW LEVEL SECURITY" in upper
    assert "REVOKE ALL" in upper
    assert "TRUNCATE" not in upper

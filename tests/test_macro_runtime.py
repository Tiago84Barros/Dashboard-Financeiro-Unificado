from datetime import datetime, timezone

from core.macro_data.models import ProviderHealth
from core.macro_data.runtime import (
    checkpoint,
    finish_run,
    record_health,
    try_acquire_lock,
)


class _Result:
    def __init__(self, value=True):
        self.value = value

    def scalar(self):
        return self.value


class _Conn:
    class engine:
        class dialect:
            name = "postgresql"

    def __init__(self, lock=True):
        self.lock = lock
        self.calls = []

    def execute(self, query, params):
        self.calls.append((str(query), params))
        return _Result(self.lock)


def test_lock_is_non_blocking_and_bound_to_a_constant_name():
    conn = _Conn(lock=False)
    assert try_acquire_lock(conn) is False
    sql, params = conn.calls[0]
    assert "pg_try_advisory_lock" in sql
    assert params["name"] == "app4-macro-international-update"


def test_checkpoint_and_health_do_not_store_sensitive_payloads():
    conn = _Conn()
    checkpoint(
        conn,
        run_id=3,
        provider="world_bank",
        status="completed",
        cursor_value="NY.GDP.MKTP.KD.ZG:BRA",
        records_inserted=2,
    )
    record_health(
        conn,
        ProviderHealth(
            "world_bank", True, "ok", datetime(2026, 9, 2, tzinfo=timezone.utc)
        ),
        3,
    )
    assert "macro_ingestion_checkpoints" in conn.calls[0][0]
    assert "macro_provider_health_checks" in conn.calls[1][0]
    assert "api_key" not in conn.calls[1][0].lower()


def test_successful_run_uses_completed_status_allowed_by_local_schema():
    conn = _Conn()
    finish_run(conn, 3, "success")
    assert conn.calls[0][1]["status"] == "completed"

"""Estado operacional da ingestão macro no PostgreSQL Docker local."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from core.macro_data.models import ProviderHealth

LOCK_NAME = "app4-macro-international-update"


def try_acquire_lock(conn) -> bool:
    """Evita coleta duplicada sem esperar nem usar lock fora do banco local."""
    dialect = conn.engine.dialect.name
    if dialect != "postgresql":
        return True
    return bool(
        conn.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:name, 0))"),
            {"name": LOCK_NAME},
        ).scalar()
    )


def release_lock(conn) -> None:
    if conn.engine.dialect.name == "postgresql":
        conn.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:name, 0))"),
            {"name": LOCK_NAME},
        )


def start_run(conn, run_key: str) -> int:
    row = conn.execute(
        text("""
        INSERT INTO macro_ingestion_runs (run_key, status)
        VALUES (:run_key, 'running')
        RETURNING id
    """),
        {"run_key": run_key},
    ).scalar_one()
    return int(row)


def checkpoint(
    conn,
    *,
    run_id: int,
    provider: str,
    status: str,
    cursor_value: str | None = None,
    records_inserted: int = 0,
    records_failed: int = 0,
    error_type: str | None = None,
) -> None:
    conn.execute(
        text("""
        INSERT INTO macro_ingestion_checkpoints
          (run_id, provider, cursor_value, status, records_inserted, records_failed, error_type)
        VALUES (:run_id,:provider,:cursor_value,:status,:records_inserted,:records_failed,:error_type)
        ON CONFLICT (run_id, provider) DO UPDATE SET
          cursor_value=EXCLUDED.cursor_value, status=EXCLUDED.status,
          records_inserted=EXCLUDED.records_inserted, records_failed=EXCLUDED.records_failed,
          error_type=EXCLUDED.error_type, updated_at=NOW()
    """),
        {
            "run_id": run_id,
            "provider": provider,
            "cursor_value": cursor_value,
            "status": status,
            "records_inserted": records_inserted,
            "records_failed": records_failed,
            "error_type": error_type,
        },
    )


def record_health(conn, health: ProviderHealth, run_id: int) -> None:
    conn.execute(
        text("""
        INSERT INTO macro_provider_health_checks (provider, available, detail, checked_at, run_id)
        VALUES (:provider,:available,:detail,:checked_at,:run_id)
    """),
        {
            "provider": health.provider_id,
            "available": health.available,
            "detail": health.detail[:250],
            "checked_at": health.checked_at.astimezone(timezone.utc),
            "run_id": run_id,
        },
    )


def finish_run(conn, run_id: int, status: str, note: str | None = None) -> None:
    persisted_status = {"success": "completed"}.get(status, status)
    conn.execute(
        text("""
        UPDATE macro_ingestion_runs
           SET status=:status, finished_at=:finished_at, note=:note
         WHERE id=:run_id
    """),
        {
            "run_id": run_id,
            "status": persisted_status,
            "finished_at": datetime.now(timezone.utc),
            "note": (note or "")[:500] or None,
        },
    )

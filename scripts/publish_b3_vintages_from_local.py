"""Publica no Supabase o histórico PIT compacto de métricas B3 do warehouse."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from psycopg2.extras import execute_values
from sqlalchemy import text

from core.config import settings
from scripts.publish_fii_selection_from_local import _warehouse_url
from scripts.publish_us_snapshot import _engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DDL = """
CREATE TABLE IF NOT EXISTS market.calculated_metric_vintages (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    period TEXT NOT NULL,
    year INTEGER NOT NULL,
    quarter INTEGER NOT NULL DEFAULT 0,
    metric_name TEXT NOT NULL,
    metric_value NUMERIC,
    calculation_method TEXT,
    source TEXT,
    confidence_score NUMERIC,
    available_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    availability_quality TEXT NOT NULL
        CHECK (availability_quality IN ('published_at','first_seen_proxy','migration_baseline'))
);
CREATE INDEX IF NOT EXISTS idx_metric_vintages_lookup
    ON market.calculated_metric_vintages
       (ticker,period,year,quarter,metric_name,available_at,recorded_at);
CREATE INDEX IF NOT EXISTS idx_metric_vintages_available
    ON market.calculated_metric_vintages (available_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_metric_vintage_artifact
    ON market.calculated_metric_vintages
       (ticker,period,year,quarter,metric_name,available_at,recorded_at);
ALTER TABLE market.calculated_metric_vintages ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE market.calculated_metric_vintages FROM PUBLIC, anon, authenticated;
"""

COLS = (
    "ticker", "period", "year", "quarter", "metric_name", "metric_value",
    "calculation_method", "source", "confidence_score", "available_at",
    "recorded_at", "availability_quality",
)


def publish() -> dict:
    if not settings.db_url:
        raise RuntimeError("Supabase não configurado")
    source = _engine(_warehouse_url())
    target = _engine(settings.db_url)
    with target.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout='300s'"))
        conn.exec_driver_sql(DDL)
    with source.connect() as conn:
        local_count = int(conn.execute(text(
            "SELECT count(*) FROM market.calculated_metric_vintages"
        )).scalar_one())
    insert_sql = f"""
        INSERT INTO market.calculated_metric_vintages ({','.join(COLS)}) VALUES %s
        ON CONFLICT (ticker,period,year,quarter,metric_name,available_at,recorded_at)
        DO NOTHING
    """
    cursor_id = 0
    processed = 0
    while True:
        with source.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT id,{','.join(COLS)} FROM market.calculated_metric_vintages
                WHERE id>:cursor ORDER BY id LIMIT 1000
            """), {"cursor": cursor_id}).all()
        if not rows:
            break
        values = [tuple(row[1:]) for row in rows]
        last_error = None
        for attempt in range(1, 6):
            try:
                with target.begin() as conn:
                    raw = conn.connection.cursor()
                    try:
                        execute_values(raw, insert_sql, values, page_size=250)
                    finally:
                        raw.close()
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(2 * attempt)
        if last_error is not None:
            raise last_error
        cursor_id = int(rows[-1][0])
        processed += len(rows)
        if processed % 10000 == 0:
            print(f"vintages B3 {processed}/{local_count}", flush=True)

    # Publica também a evidência de readiness mais recente, sem duplicar hash.
    with source.connect() as conn:
        readiness = conn.execute(text("""
            SELECT observed_at,universe_definition,snapshot_json,artifact_hash
            FROM market.b3_data_readiness_snapshots ORDER BY observed_at DESC LIMIT 1
        """)).mappings().first()
    if readiness:
        with target.begin() as conn:
            exists = conn.execute(text("""
                SELECT 1 FROM market.b3_data_readiness_snapshots
                WHERE artifact_hash=:artifact_hash
            """), dict(readiness)).scalar()
            if not exists:
                conn.execute(text("""
                    INSERT INTO market.b3_data_readiness_snapshots
                    (observed_at,universe_definition,snapshot_json,artifact_hash)
                    VALUES (:observed_at,:universe_definition,CAST(:snapshot_json AS jsonb),:artifact_hash)
                """), {**dict(readiness), "snapshot_json": json.dumps(
                    readiness["snapshot_json"], ensure_ascii=False, default=str
                )})
    with target.connect() as conn:
        remote_count = int(conn.execute(text(
            "SELECT count(*) FROM market.calculated_metric_vintages"
        )).scalar_one())
    source.dispose()
    target.dispose()
    if remote_count < local_count:
        raise RuntimeError(f"publicação incompleta: local={local_count}, remoto={remote_count}")
    return {"local_rows": local_count, "processed": processed,
            "remote_rows": remote_count, "readiness_published": bool(readiness)}


if __name__ == "__main__":
    print(json.dumps(publish(), ensure_ascii=False, sort_keys=True))

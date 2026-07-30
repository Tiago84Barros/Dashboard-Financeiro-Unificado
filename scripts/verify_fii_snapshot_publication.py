"""Verificação read-only do snapshot FII e da validação servidos pelo App4."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from core.config import settings
    from core.fii_methodology import METHODOLOGY_VERSION

    parsed = make_url(settings.db_url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    parsed = parsed.update_query_dict({"sslmode": "require"})
    engine = create_engine(
        parsed, future=True, poolclass=NullPool,
        connect_args={"connect_timeout": 15, "options": "-c statement_timeout=60000"},
    )
    with engine.connect() as conn:
        snapshot = conn.execute(text("""
            SELECT count(*) AS rows, min(as_of_date) AS min_as_of,
                   max(as_of_date) AS max_as_of,
                   count(DISTINCT payload_sha256) AS distinct_hashes,
                   sum(pg_column_size(payload_json)) AS payload_bytes,
                   round(avg((coverage_json->>'coverage_pct')::numeric), 2)
                       AS coverage_mean_pct,
                   jsonb_object_agg(schema_version, version_rows) AS versions
            FROM (
                SELECT *, count(*) OVER (PARTITION BY schema_version) AS version_rows
                FROM market.fii_selection_inputs
            ) data
        """)).mappings().one()
        lookthrough = conn.execute(text("""
            SELECT dimension,
                   count(*) FILTER (
                       WHERE COALESCE((details->>'applicable')::boolean, false)
                   ) AS applicable,
                   count(*) FILTER (
                       WHERE COALESCE((details->>'observed')::boolean, false)
                   ) AS observed
            FROM market.fii_selection_inputs
            CROSS JOIN LATERAL jsonb_each(
                COALESCE(coverage_json->'lookthrough', '{}'::jsonb)
            ) AS exposure(dimension, details)
            GROUP BY dimension
            ORDER BY dimension
        """)).mappings().all()
        validation = conn.execute(text("""
            SELECT status,as_of_date,metrics_json->>'strategy_id' AS strategy_id,
                   metrics_json->'backtest'->>'periods' AS periods,
                   jsonb_array_length(blockers_json) AS blocker_count
            FROM market.fii_validation_runs
            WHERE methodology_version=:version
            ORDER BY COALESCE(finished_at,started_at) DESC LIMIT 1
        """), {"version": METHODOLOGY_VERSION}).mappings().first()
        methodology = conn.execute(text("""
            SELECT methodology_version,formula_version,status
            FROM market.fii_methodology_versions
            WHERE methodology_version=:version
        """), {"version": METHODOLOGY_VERSION}).mappings().first()
    engine.dispose()
    print(json.dumps({
        "snapshot": dict(snapshot),
        "lookthrough": {row["dimension"]: {
            "applicable": int(row["applicable"]),
            "observed": int(row["observed"]),
            "coverage": (
                int(row["observed"]) / int(row["applicable"])
                if int(row["applicable"]) else None
            ),
        } for row in lookthrough},
        "validation": dict(validation) if validation else None,
        "methodology": dict(methodology) if methodology else None,
    }, ensure_ascii=False, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

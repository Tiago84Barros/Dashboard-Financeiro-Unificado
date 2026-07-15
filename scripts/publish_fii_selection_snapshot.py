"""Publica a vitrine compacta de inputs de seleção de FIIs.

Uso:
    python scripts/publish_fii_selection_snapshot.py --source-url <local> --target-url <supabase>

O source deve ser o warehouse local. O target é o banco do Supabase usado pelo
App 4. O script não publica tabelas históricas, apenas um payload por ticker.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCHEMA_VERSION = "fii_selection_inputs.v1"
VINTAGE = "local_warehouse_pit_snapshot_v1"
SOURCE = (
    "local_warehouse:market.fiis+fii_metric_observations+"
    "fii_exposures+fii_parser_calibrations"
)


def _engine(url: str):
    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    if parsed.host and parsed.host not in {"localhost", "127.0.0.1", "::1"}:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return create_engine(parsed, pool_pre_ping=True, future=True)


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (dt.datetime, dt.date, dt.time, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return _jsonable(value.item())
        except (TypeError, ValueError):
            pass
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _canonical(payload: dict) -> tuple[str, str]:
    encoded = json.dumps(
        _jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _coverage(payload: dict) -> dict[str, Any]:
    required = (
        "ticker", "tipo", "dy_12m", "pvp", "liquidez_diaria", "history_months",
        "max_drawdown", "vacancia_fisica", "property_count", "region_count",
    )
    present = [key for key in required if payload.get(key) is not None]
    return {
        "required_fields": len(required),
        "present_fields": len(present),
        "coverage_pct": round(100 * len(present) / len(required), 2),
        "present": present,
        "missing": [key for key in required if key not in present],
    }


def _ensure_schema(engine) -> None:
    sql = (ROOT / "supabase_unificado" / "schema" / "039_fii_selection_inputs_snapshot.sql").read_text(
        encoding="utf-8"
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def build_rows(df: pd.DataFrame, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    if df.empty:
        raise ValueError("warehouse retornou zero inputs de FII")
    if "ticker" not in df.columns:
        raise ValueError("payload sem coluna ticker")
    work = df.copy()
    work["ticker"] = work["ticker"].astype(str).str.strip().str.upper().str.replace(
        ".SA", "", regex=False
    )
    if work["ticker"].duplicated().any():
        duplicated = sorted(work.loc[work["ticker"].duplicated(), "ticker"].unique())
        raise ValueError(f"tickers duplicados no snapshot: {duplicated[:10]}")
    stamp = now or dt.datetime.now(dt.timezone.utc)
    rows: list[dict[str, Any]] = []
    for record in work.to_dict("records"):
        payload = _jsonable(record)
        _, digest = _canonical(payload)
        dates = []
        for key in ("cvm_ref_date", "vacancia_ref_date", "updated_at"):
            value = payload.get(key)
            if value:
                try:
                    dates.append(pd.Timestamp(value).date())
                except (TypeError, ValueError):
                    pass
        rows.append({
            "ticker": payload["ticker"],
            "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "as_of_date": stamp.date(),
            "available_at": stamp,
            "knowledge_at": stamp,
            "reference_date": max(dates) if dates else None,
            "vintage": VINTAGE,
            "source": SOURCE,
            "quality_status": "published",
            "schema_version": SCHEMA_VERSION,
            "generated_at": stamp,
            "payload_sha256": digest,
            "coverage_json": json.dumps(_coverage(payload), ensure_ascii=False, sort_keys=True),
        })
    return rows


def publish(source_url: str, target_url: str, dry_run: bool = False) -> dict[str, Any]:
    os.environ.update({
        "DATABASE_URL": source_url,
        "SUPABASE_UNIFICADO_URL": source_url,
        "SUPABASE_DB_URL": source_url,
    })
    # Importa depois de apontar a configuração para o warehouse local.
    from core.market_read import load_fii_methodology_inputs

    source = load_fii_methodology_inputs()
    rows = build_rows(source)
    report: dict[str, Any] = {
        "source_rows": len(source),
        "published_rows": len(rows),
        "schema_version": SCHEMA_VERSION,
        "dry_run": dry_run,
    }
    if dry_run:
        report["coverage_mean_pct"] = round(
            sum(json.loads(row["coverage_json"])["coverage_pct"] for row in rows) / len(rows), 2
        )
        return report

    target = _engine(target_url)
    _ensure_schema(target)
    stage_sql = """
        CREATE TEMP TABLE fii_selection_inputs_stage
        (LIKE market.fii_selection_inputs INCLUDING DEFAULTS)
        ON COMMIT DROP
    """
    insert_sql = """
        INSERT INTO fii_selection_inputs_stage
        (ticker, payload_json, as_of_date, available_at, knowledge_at,
         reference_date, vintage, source, quality_status, schema_version,
         generated_at, payload_sha256, coverage_json)
        VALUES (:ticker, CAST(:payload_json AS jsonb), :as_of_date, :available_at,
                :knowledge_at, :reference_date, :vintage, :source,
                :quality_status, :schema_version, :generated_at,
                :payload_sha256, CAST(:coverage_json AS jsonb))
    """
    with target.begin() as conn:
        conn.exec_driver_sql(stage_sql)
        conn.execute(text(insert_sql), rows)
        conn.execute(text("TRUNCATE market.fii_selection_inputs"))
        conn.execute(text("INSERT INTO market.fii_selection_inputs SELECT * FROM fii_selection_inputs_stage"))
        count = conn.execute(text("SELECT count(*) FROM market.fii_selection_inputs")).scalar_one()
    target.dispose()
    report["target_rows"] = int(count)
    if count != len(rows):
        raise RuntimeError(f"publicação incompleta: esperado {len(rows)}, obtido {count}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=os.getenv("WAREHOUSE_DB_URL"))
    parser.add_argument("--target-url", default=os.getenv("TARGET_DB_URL"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.source_url or (not args.target_url and not args.dry_run):
        parser.error("informe --source-url e --target-url (ou use WAREHOUSE_DB_URL/TARGET_DB_URL)")
    report = publish(args.source_url, args.target_url, dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

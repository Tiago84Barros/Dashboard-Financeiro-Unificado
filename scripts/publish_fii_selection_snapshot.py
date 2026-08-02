"""Publica a vitrine compacta de inputs de seleção de FIIs.

Uso:
    python scripts/publish_fii_selection_snapshot.py --source-url <local> --target-url <supabase>

O source deve ser o warehouse local. O target é o banco do Supabase usado pelo
App 4. O script não publica tabelas históricas, apenas um payload por ticker.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
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
SCHEMA_VERSION = "fii_selection_inputs.v2"
VINTAGE = "local_warehouse_pit_snapshot_v1"
SOURCE = (
    "local_warehouse:market.fiis+fii_metric_observations+"
    "fii_exposures+fii_parser_calibrations"
)
LOCAL_ARTIFACT_PATH = ROOT / "data" / "public" / "fii_selection_snapshot_v2.json.gz"


def _engine(url: str):
    from sqlalchemy.pool import NullPool

    parsed = make_url(url)
    if parsed.drivername in {"postgresql", "postgres"}:
        parsed = parsed.set(drivername="postgresql+psycopg2")
    is_remote = bool(parsed.host and parsed.host not in {"localhost", "127.0.0.1", "::1"})
    connect_args: dict[str, Any] = {"connect_timeout": 15}
    if is_remote:
        parsed = parsed.update_query_dict({"sslmode": "require"})
        connect_args.update(
            options="-c statement_timeout=120000",
            keepalives=1,
            keepalives_idle=10,
            keepalives_interval=5,
            keepalives_count=3,
        )
        if os.getenv("SUPABASE_DB_HOSTADDR"):
            connect_args["hostaddr"] = os.environ["SUPABASE_DB_HOSTADDR"]
    kwargs: dict[str, Any] = {"future": True, "connect_args": connect_args}
    if is_remote:
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_pre_ping"] = True
    return create_engine(parsed, **kwargs)


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


def _compact_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Remove redundância do snapshot sem perder a proveniência usada pelo motor."""
    payload = _jsonable(record)
    metadata = payload.get("metric_metadata")
    if not isinstance(metadata, dict):
        return payload
    from core.fii_methodology import COMMON_METRICS, TYPE_METRICS

    fii_type = str(payload.get("tipo") or "").strip().lower()
    definitions = (*COMMON_METRICS, *TYPE_METRICS.get(fii_type, ()))
    relevant_metrics = {definition.key for definition in definitions}
    for definition in definitions:
        relevant_metrics.update(definition.fallback_keys)
    compact: dict[str, list[Any]] = {}
    for metric, details in metadata.items():
        if str(metric) not in relevant_metrics or not isinstance(details, dict):
            continue
        # Ordem estável: available_at, source_quality, reference_date, source.
        # O leitor expande a lista para o contrato nominal usado pelo motor.
        values = [
            details.get("available_at"),
            details.get("source_quality"),
            details.get("reference_date"),
            details.get("source"),
        ]
        if any(value is not None for value in values):
            compact[str(metric)] = values
    payload["metric_metadata"] = compact
    return payload


def _coverage(payload: dict) -> dict[str, Any]:
    from core.fii_lookthrough import summarize_lookthrough_coverage

    required = (
        "ticker", "tipo", "dy_12m", "pvp", "liquidez_diaria", "history_months",
        "max_drawdown", "vacancia_fisica", "property_count", "region_count",
    )
    present = [key for key in required if payload.get(key) is not None]
    lookthrough = summarize_lookthrough_coverage([payload])
    return {
        "required_fields": len(required),
        "present_fields": len(present),
        "coverage_pct": round(100 * len(present) / len(required), 2),
        "present": present,
        "missing": [key for key in required if key not in present],
        "lookthrough": {
            dimension: {
                "applicable": details["applicable_count"] == 1,
                "observed": details["observed_count"] == 1,
                "required": details["required"],
            }
            for dimension, details in lookthrough["dimensions"].items()
        },
    }


def _schema_ready(engine) -> bool:
    required = {
        "ticker", "payload_json", "as_of_date", "available_at", "knowledge_at",
        "reference_date", "vintage", "source", "quality_status",
        "schema_version", "generated_at", "payload_sha256", "coverage_json",
    }
    with engine.connect() as conn:
        columns = set(conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='market' AND table_name='fii_selection_inputs'
        """)).scalars())
    return required.issubset(columns)


def _schema_ready_on_connection(conn) -> bool:
    required = {
        "ticker", "payload_json", "as_of_date", "available_at", "knowledge_at",
        "reference_date", "vintage", "source", "quality_status",
        "schema_version", "generated_at", "payload_sha256", "coverage_json",
    }
    columns = set(conn.execute(text("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='market' AND table_name='fii_selection_inputs'
    """)).scalars())
    return required.issubset(columns)


def _ensure_schema(engine) -> None:
    if _schema_ready(engine):
        return
    sql = (ROOT / "supabase_unificado" / "schema" / "039_fii_selection_inputs_snapshot.sql").read_text(
        encoding="utf-8"
    )
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '120s'"))
        conn.exec_driver_sql(sql)


def _ensure_target_methodology(conn) -> None:
    """Cria a versão referenciada pela validação antes de inserir a FK."""
    from core.fii_methodology import (
        FORMULA_VERSION,
        METHODOLOGY_VERSION,
        methodology_manifest,
    )

    conn.execute(text("""
        INSERT INTO market.fii_methodology_versions
            (methodology_version,formula_version,manifest_json,status)
        VALUES (:version,:formula,CAST(:manifest AS jsonb),'validation')
        ON CONFLICT (methodology_version) DO UPDATE SET
            formula_version=EXCLUDED.formula_version,
            manifest_json=EXCLUDED.manifest_json,
            status=CASE WHEN market.fii_methodology_versions.status='passed'
                        THEN 'passed' ELSE EXCLUDED.status END
    """), {
        "version": METHODOLOGY_VERSION,
        "formula": FORMULA_VERSION,
        "manifest": json.dumps(methodology_manifest(), ensure_ascii=False),
    })


def _sync_methodology_status(conn, validation: dict[str, Any]) -> None:
    """Promove a versão somente quando o backtest publicado foi aprovado."""
    if validation.get("status") != "passed":
        return
    conn.execute(text("""
        UPDATE market.fii_methodology_versions
        SET status='passed'
        WHERE methodology_version=:methodology_version
    """), validation)


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
        payload = _compact_payload(record)
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


def _latest_validation(source_engine) -> dict[str, Any] | None:
    with source_engine.connect() as conn:
        row = conn.execute(text("""
            SELECT methodology_version,as_of_date,status,metrics_json,blockers_json,
                   started_at,finished_at
            FROM market.fii_validation_runs
            ORDER BY COALESCE(finished_at,started_at) DESC LIMIT 1
        """)).mappings().first()
    if not row:
        return None
    result = dict(row)
    metrics = _jsonable(result.get("metrics_json") or {})
    backtest = metrics.get("backtest") if isinstance(metrics, dict) else None
    if isinstance(backtest, dict):
        observations = backtest.pop("observations", [])
        backtest["observation_count"] = len(observations) if isinstance(observations, list) else 0
    result["metrics_json"] = metrics
    result["blockers_json"] = _jsonable(result.get("blockers_json") or [])
    return result


def _publication_preflight(
    *,
    validation: dict[str, Any] | None,
    lookthrough: dict[str, Any],
    row_count: int,
) -> list[str]:
    """Falha fechado antes de qualquer escrita no destino remoto."""
    from core.fii_portfolio_v4 import LIVE_PORTFOLIO_STRATEGY_ID
    from core.fii_validation import ValidationThresholds

    blockers: list[str] = []
    if row_count <= 0:
        blockers.append("snapshot local vazio")
    if not validation or validation.get("status") != "passed":
        blockers.append("validação PIT local não aprovada")
    else:
        metrics = validation.get("metrics_json") or {}
        backtest = metrics.get("backtest") or {}
        strategy_id = metrics.get("strategy_id") or backtest.get("strategy_id")
        if strategy_id != LIVE_PORTFOLIO_STRATEGY_ID:
            blockers.append("validação PIT não corresponde ao otimizador vigente")
        try:
            periods = int(backtest.get("periods") or metrics.get("periods") or 0)
        except (TypeError, ValueError):
            periods = 0
        if periods < ValidationThresholds().min_periods:
            blockers.append("histórico PIT abaixo do mínimo metodológico")
        if validation.get("blockers_json"):
            blockers.append("validação PIT contém bloqueadores")
    if not lookthrough.get("required_ready"):
        blockers.append("cobertura look-through obrigatória insuficiente")
    return blockers


def _replace_target_snapshot(conn) -> int:
    """Atualiza a vitrine sem exigir o lock exclusivo de um TRUNCATE."""
    conn.execute(text("""
        INSERT INTO market.fii_selection_inputs (
            ticker, payload_json, as_of_date, available_at, knowledge_at,
            reference_date, vintage, source, quality_status, schema_version,
            generated_at, payload_sha256, coverage_json
        )
        SELECT
            ticker, payload_json, as_of_date, available_at, knowledge_at,
            reference_date, vintage, source, quality_status, schema_version,
            generated_at, payload_sha256, coverage_json
        FROM fii_selection_inputs_stage
        ON CONFLICT (ticker) DO UPDATE SET
            payload_json=EXCLUDED.payload_json,
            as_of_date=EXCLUDED.as_of_date,
            available_at=EXCLUDED.available_at,
            knowledge_at=EXCLUDED.knowledge_at,
            reference_date=EXCLUDED.reference_date,
            vintage=EXCLUDED.vintage,
            source=EXCLUDED.source,
            quality_status=EXCLUDED.quality_status,
            schema_version=EXCLUDED.schema_version,
            generated_at=EXCLUDED.generated_at,
            payload_sha256=EXCLUDED.payload_sha256,
            coverage_json=EXCLUDED.coverage_json
    """))
    conn.execute(text("""
        DELETE FROM market.fii_selection_inputs AS target
        WHERE NOT EXISTS (
            SELECT 1 FROM fii_selection_inputs_stage AS stage
            WHERE stage.ticker=target.ticker
        )
    """))
    return int(conn.execute(
        text("SELECT count(*) FROM market.fii_selection_inputs")
    ).scalar_one())


def _insert_stage_rows(conn, rows: list[dict[str, Any]]) -> None:
    """Insere o lote em uma única ida ao banco, evitando 394 round-trips."""
    rows_json = json.dumps(_jsonable(rows), ensure_ascii=False, separators=(",", ":"))
    conn.execute(text("""
        INSERT INTO fii_selection_inputs_stage
        (ticker, payload_json, as_of_date, available_at, knowledge_at,
         reference_date, vintage, source, quality_status, schema_version,
         generated_at, payload_sha256, coverage_json)
        SELECT ticker, payload_json::jsonb, as_of_date, available_at, knowledge_at,
               reference_date, vintage, source, quality_status, schema_version,
               generated_at, payload_sha256, coverage_json::jsonb
        FROM jsonb_to_recordset(CAST(:rows_json AS jsonb)) AS batch(
            ticker text, payload_json text, as_of_date date,
            available_at timestamptz, knowledge_at timestamptz,
            reference_date date, vintage text, source text, quality_status text,
            schema_version text, generated_at timestamptz, payload_sha256 text,
            coverage_json text
        )
    """), {"rows_json": rows_json})


def _write_local_snapshot_artifact(
    rows: list[dict[str, Any]], path: Path = LOCAL_ARTIFACT_PATH,
) -> Path:
    """Grava fallback público verificável, sem credenciais nem dados pessoais."""
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rows": _jsonable(rows),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=9) as handle:
        json.dump(
            artifact, handle, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )
    os.replace(temporary, path)
    return path


def publish(source_url: str, target_url: str, dry_run: bool = False) -> dict[str, Any]:
    os.environ.update({
        "DATABASE_URL": source_url,
        "SUPABASE_UNIFICADO_URL": source_url,
        "SUPABASE_DB_URL": source_url,
    })
    # Importa depois de apontar a configuração para o warehouse local. Também
    # atualiza/limpa singletons já importados, tornando a função segura quando
    # chamada programaticamente no mesmo processo que leu a configuração remota.
    from core.config import settings
    previous_urls = (
        settings.SUPABASE_UNIFICADO_URL, settings.DATABASE_URL, settings.SUPABASE_DB_URL,
    )
    settings.SUPABASE_UNIFICADO_URL = source_url
    settings.DATABASE_URL = source_url
    settings.SUPABASE_DB_URL = source_url
    from core.database import get_engine, get_session_factory
    get_engine.clear()
    get_session_factory.clear()
    from core.market_read import load_fii_methodology_inputs
    load_fii_methodology_inputs.clear()

    try:
        # O warehouse pode conter a vitrine da execução anterior. A publicação
        # sempre reconstrói os inputs das tabelas-base.
        source = load_fii_methodology_inputs(prefer_snapshot=False)
    finally:
        (settings.SUPABASE_UNIFICADO_URL, settings.DATABASE_URL,
         settings.SUPABASE_DB_URL) = previous_urls
        get_engine.clear()
        get_session_factory.clear()
        load_fii_methodology_inputs.clear()
    rows = build_rows(source)
    source_engine = _engine(source_url)
    validation = _latest_validation(source_engine)
    source_engine.dispose()
    from core.fii_lookthrough import summarize_lookthrough_coverage

    lookthrough = summarize_lookthrough_coverage(source.to_dict("records"))
    preflight_blockers = _publication_preflight(
        validation=validation,
        lookthrough=lookthrough,
        row_count=len(rows),
    )
    report: dict[str, Any] = {
        "source_rows": len(source),
        "published_rows": len(rows),
        "schema_version": SCHEMA_VERSION,
        "dry_run": dry_run,
        "payload_json_bytes": sum(
            len(row["payload_json"].encode("utf-8")) for row in rows
        ),
        "validation_status": validation.get("status") if validation else "unavailable",
        "lookthrough": lookthrough,
        "publication_ready": not preflight_blockers,
        "preflight_blockers": preflight_blockers,
    }
    if dry_run:
        report["coverage_mean_pct"] = round(
            sum(json.loads(row["coverage_json"])["coverage_pct"] for row in rows) / len(rows), 2
        )
        return report
    if preflight_blockers:
        raise RuntimeError(
            "publicação bloqueada no preflight: " + "; ".join(preflight_blockers)
        )

    target = _engine(target_url)
    stage_sql = """
        CREATE TEMP TABLE fii_selection_inputs_stage
        (LIKE market.fii_selection_inputs INCLUDING DEFAULTS)
        ON COMMIT DROP
    """
    with target.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '120s'"))
        if not _schema_ready_on_connection(conn):
            schema_sql = (
                ROOT / "supabase_unificado" / "schema"
                / "039_fii_selection_inputs_snapshot.sql"
            ).read_text(encoding="utf-8")
            conn.exec_driver_sql(schema_sql)
        _ensure_target_methodology(conn)
        conn.exec_driver_sql(stage_sql)
        _insert_stage_rows(conn, rows)
        count = _replace_target_snapshot(conn)
        validation_table = conn.execute(
            text("SELECT to_regclass('market.fii_validation_runs')")
        ).scalar()
        if validation and validation_table:
            conn.execute(text("""
                DELETE FROM market.fii_validation_runs
                WHERE methodology_version=:methodology_version
            """), validation)
            conn.execute(text("""
                INSERT INTO market.fii_validation_runs (
                    methodology_version,as_of_date,status,metrics_json,blockers_json,
                    started_at,finished_at
                ) VALUES (
                    :methodology_version,:as_of_date,:status,CAST(:metrics_json AS jsonb),
                    CAST(:blockers_json AS jsonb),:started_at,:finished_at
                )
            """), {
                **validation,
                "metrics_json": json.dumps(validation["metrics_json"], ensure_ascii=False),
                "blockers_json": json.dumps(validation["blockers_json"], ensure_ascii=False),
            })
            _sync_methodology_status(conn, validation)
    target.dispose()
    report["target_rows"] = int(count)
    report["validation_published"] = bool(validation and validation_table)
    if count != len(rows):
        raise RuntimeError(f"publicação incompleta: esperado {len(rows)}, obtido {count}")
    try:
        artifact_path = _write_local_snapshot_artifact(rows)
        report["local_artifact"] = str(artifact_path.relative_to(ROOT))
        report["local_artifact_bytes"] = artifact_path.stat().st_size
    except OSError as exc:
        report["local_artifact_error"] = type(exc).__name__
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

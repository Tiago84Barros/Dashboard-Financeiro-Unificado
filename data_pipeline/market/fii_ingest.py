"""
data_pipeline/market/fii_ingest.py
Ingestão de FIIs (BRAPI Pro) -> market.fiis.

Fluxo: lista de fundos (type=fund, por volume) -> para cada, busca cotação +
rendimentos + perfil -> filtra ETF pelo setor (fii.is_fii) -> computa métricas
(DY 12m, P/VP, liquidez) -> rankeia -> upsert em market.fiis. Salva o payload
bruto p/ permitir re-ranking sem rede (reprocess).
"""
from __future__ import annotations

import json
import logging
import os
import re
import hashlib
import time
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import text

import core.brapi as brapi
from data_pipeline.market import fii as fz
from data_pipeline.market import repository as repo
from data_pipeline.quality import scheduler as sched

logger = logging.getLogger(__name__)


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _fetch_fii_v2_batch(endpoint: str, symbols: list[str],
                        params: dict | None = None) -> brapi.FiiApiResponse:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return brapi.fetch_fii_v2_all_pages(
                endpoint, symbols, params=params, timeout=30)
        except brapi.BrapiRateLimited as exc:
            last_error = exc
            if attempt < 2:
                wait = min(float(exc.retry_after or (2 ** attempt)), 30.0)
                time.sleep(wait)
        except Exception:
            raise
    assert last_error is not None
    raise last_error


def _engine():
    from data_pipeline.utils.db_utils import get_pipeline_engine
    return get_pipeline_engine()


def _schema_ready(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='market' AND table_name='fiis')")).scalar())


def _score_metadata_ready(conn) -> bool:
    return bool(conn.execute(text(
        "SELECT COUNT(*) = 3 FROM information_schema.columns "
        "WHERE table_schema='market' AND table_name='fiis' "
        "AND column_name IN ('score_version','score_calculated_at','metrics_fetched_at')"
    )).scalar())


def snapshot_methodology_v4() -> dict:
    """Calcula e persiste snapshots v4 sem substituir o score legado de market.fiis."""
    from core.fii_methodology import (FORMULA_VERSION, METHODOLOGY_VERSION,
                                     methodology_manifest, score_fiis_by_type)
    engine = _engine()
    result = {"status": "blocked", "fundos": 0, "gravados": 0, "blockers": []}
    if engine is None:
        result["blockers"].append("banco indisponível")
        return result
    with engine.connect() as conn:
        ready = bool(conn.execute(text(
            "SELECT to_regclass('market.fii_score_snapshots') IS NOT NULL"
        )).scalar())
        if not ready:
            result["blockers"].append("migração 023_fii_methodology_v4.sql pendente")
            return result
        validation = conn.execute(text("""
            SELECT status FROM market.fii_validation_runs
            WHERE methodology_version=:version ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1
        """), {"version": METHODOLOGY_VERSION}).scalar() or "unvalidated"
        base = conn.execute(text("""
            WITH current_universe AS (
                SELECT DISTINCT ON (ticker) ticker, active_status
                FROM market.fii_universe_history
                WHERE knowledge_at <= now()
                ORDER BY ticker, knowledge_at DESC, reference_date DESC
            )
            SELECT f.ticker, f.name, f.tipo, COALESCE(f.segmento_cvm, f.segmento) AS sector,
                   f.dy_12m, f.pvp, f.liquidez_diaria, f.updated_at,
                   GREATEST(0, EXTRACT(YEAR FROM age(max(h.date), min(h.date))) * 12
                     + EXTRACT(MONTH FROM age(max(h.date), min(h.date)))) AS history_months
            FROM market.fiis f JOIN current_universe u USING (ticker)
            LEFT JOIN market.historical_prices h ON h.ticker=f.ticker
            WHERE u.active_status IN ('listed','active')
              AND f.ticker ~ '^[A-Z]{4}11$' AND f.price > 0
              AND f.tipo IN ('tijolo','papel','fof','hibrido')
            GROUP BY f.ticker, f.name, f.tipo, f.segmento_cvm, f.segmento, f.dy_12m,
                     f.pvp, f.liquidez_diaria, f.updated_at
        """)).mappings().all()
        observations = conn.execute(text("""
            SELECT DISTINCT ON (ticker, metric_name) ticker, metric_name, value_numeric,
                   value_text, value_json, reference_date, available_at, knowledge_at,
                   availability_quality, vintage, source
            FROM market.fii_metric_observations
            WHERE knowledge_at <= now()
              AND quality_status IN ('observed','accepted')
            ORDER BY ticker, metric_name, knowledge_at DESC, reference_date DESC, observed_at DESC
        """)).mappings().all()
    by_ticker: dict[str, list[dict]] = {}
    for observation in observations:
        by_ticker.setdefault(str(observation["ticker"]), []).append(dict(observation))
    inputs: list[dict] = []
    for base_row in base:
        row = dict(base_row)
        row["metric_metadata"] = {
            "dy_12m": {"available_at": row.get("updated_at"), "source": "brapi"},
            "liquidez_diaria": {"available_at": row.get("updated_at"), "source": "brapi"},
            "pvp": {"available_at": row.get("updated_at"), "source": "cvm_vpa+brapi_quote"},
        }
        for observation in by_ticker.get(str(row["ticker"]), []):
            key = str(observation["metric_name"])
            value = observation.get("value_numeric")
            if value is None:
                value = observation.get("value_text")
            if value is None:
                value = observation.get("value_json")
            row[key] = value
            row["metric_metadata"][key] = {
                "reference_date": observation.get("reference_date"),
                "available_at": observation.get("available_at"),
                "knowledge_at": observation.get("knowledge_at"),
                "availability_quality": observation.get("availability_quality"),
                "vintage": observation.get("vintage"),
                "source": observation.get("source"),
                "source_quality": {
                    "verified_publication": .95,
                    "first_observed_proxy": .80,
                    "retrospective_backfill": .55,
                    "migration_baseline": .20,
                }.get(str(observation.get("availability_quality") or ""), .50),
            }
        inputs.append(row)
    scored = score_fiis_by_type(inputs, validation_status="passed" if validation == "passed" else "unvalidated")
    now = datetime.now(timezone.utc)
    snapshots = [{
        "ticker": row["ticker"], "reference_date": row["as_of_date"], "available_at": now,
        "methodology_version": METHODOLOGY_VERSION, "formula_version": FORMULA_VERSION,
        "fii_type": row["tipo"], "type_score": row["type_score"],
        "confidence": row["confidence"], "coverage": row["coverage"],
        "components_json": json.dumps(row["components"], ensure_ascii=False, default=str),
        "inputs_json": json.dumps(row["score_inputs"], ensure_ascii=False, default=str),
        "missing_metrics_json": json.dumps(row["missing_metrics"], ensure_ascii=False),
        "publication_status": row["publication_status"],
        "publication_reasons_json": json.dumps(row["publication_reasons"], ensure_ascii=False),
    } for row in scored]
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE market.fii_methodology_versions SET manifest_json=CAST(:manifest AS jsonb)
            WHERE methodology_version=:version
        """), {"manifest": json.dumps(methodology_manifest(), ensure_ascii=False),
                 "version": METHODOLOGY_VERSION})
        result["gravados"] = repo.upsert(conn, "fii_score_snapshots", snapshots)
    result.update(status="saved", fundos=len(scored))
    return result


def reconcile_brapi_cvm() -> dict:
    """Materializa conflitos mensais entre a normalização Brapi e a CVM."""
    engine = _engine()
    if engine is None:
        return {"status": "failed", "issues": 0}
    with engine.begin() as conn:
        if not conn.execute(text(
                "SELECT to_regclass('market.fii_reconciliation_issues') IS NOT NULL")).scalar():
            return {"status": "skipped", "issues": 0}
        result = conn.execute(text("""
            WITH brapi AS (
                SELECT DISTINCT ON (ticker, metric_name, date_trunc('month', reference_date))
                       ticker, metric_name, date_trunc('month', reference_date)::date ref_month,
                       value_numeric::numeric value
                FROM market.fii_metric_observations
                WHERE source='brapi_fii_v2'
                  AND quality_status IN ('observed','accepted')
                  AND metric_name IN ('nav_per_share','equity','total_investors','vacancia_fisica')
                ORDER BY ticker, metric_name, date_trunc('month', reference_date),
                         knowledge_at DESC, observed_at DESC
            ), cvm AS (
                SELECT m.ticker, m.ref_month,
                       x.metric_name, x.value
                FROM market.fii_metrics_monthly m
                CROSS JOIN LATERAL (VALUES
                    ('nav_per_share', m.vpa::numeric),
                    ('equity', m.patrimonio_liquido::numeric),
                    ('total_investors', m.num_cotistas::numeric)
                ) x(metric_name, value)
                WHERE x.value IS NOT NULL
            ), compared AS (
                SELECT b.ticker, b.metric_name, b.ref_month, b.value brapi_value,
                       c.value cvm_value,
                       abs(b.value-c.value) absolute_difference,
                       abs(b.value-c.value)/NULLIF(abs(c.value),0) relative_difference,
                       CASE b.metric_name WHEN 'total_investors' THEN .05 ELSE .02 END tolerance
                FROM brapi b JOIN cvm c USING (ticker, metric_name, ref_month)
            )
            INSERT INTO market.fii_reconciliation_issues (
                ticker, metric_name, reference_date, left_source, left_value,
                right_source, right_value, absolute_difference,
                relative_difference, tolerance, status
            )
            SELECT ticker, metric_name, ref_month, 'brapi_fii_v2',
                   to_jsonb(brapi_value), 'cvm_informe_mensal', to_jsonb(cvm_value),
                   absolute_difference, relative_difference, tolerance, 'open'
            FROM compared WHERE relative_difference > tolerance
            ON CONFLICT (ticker, metric_name, reference_date, left_source, right_source)
            DO UPDATE SET left_value=EXCLUDED.left_value, right_value=EXCLUDED.right_value,
                          absolute_difference=EXCLUDED.absolute_difference,
                          relative_difference=EXCLUDED.relative_difference,
                          tolerance=EXCLUDED.tolerance,
                          status=CASE
                            WHEN market.fii_reconciliation_issues.status IN
                                 ('accepted','resolved','rejected')
                            THEN market.fii_reconciliation_issues.status
                            ELSE 'open' END,
                          detected_at=now()
        """))
        # Para métricas regulatórias, a CVM é a fonte autoritativa. O valor da
        # Brapi permanece imutável e rastreável, mas não pode alimentar scores
        # enquanto divergir além da tolerância documentada.
        conn.execute(text("""
            UPDATE market.fii_metric_observations o
               SET quality_status='suspect',
                   metadata_json=o.metadata_json || jsonb_build_object(
                       'quarantine_reason','brapi_cvm_conflict',
                       'authoritative_source','cvm_informe_mensal')
              FROM market.fii_reconciliation_issues i
             WHERE i.status='open'
               AND i.left_source='brapi_fii_v2'
               AND o.source='brapi_fii_v2'
               AND o.ticker=i.ticker AND o.metric_name=i.metric_name
               AND date_trunc('month', o.reference_date)::date=i.reference_date
        """))
        conn.execute(text("""
            UPDATE market.fii_reconciliation_issues
               SET status='accepted', resolved_at=now(),
                   resolution_json=jsonb_build_object(
                       'action','quarantine_left_source',
                       'authoritative_source','cvm_informe_mensal',
                       'automatic_rule','cvm_regulatory_metric_precedence_v1')
             WHERE status='open' AND left_source='brapi_fii_v2'
               AND right_source='cvm_informe_mensal'
        """))
        open_count = conn.execute(text("""
            SELECT count(*) FROM market.fii_reconciliation_issues WHERE status='open'
        """)).scalar()
    return {"status": "calculated", "issues": max(int(result.rowcount or 0), 0),
            "open": int(open_count or 0)}


def audit_methodology_v4_data() -> dict:
    """Audita integridade PIT, duplicidade, stale data e somas de exposições."""
    engine = _engine()
    report = {"status": "blocked", "checks": {}, "blockers": []}
    if engine is None:
        report["blockers"].append("banco indisponível")
        return report
    report["reconciliation"] = reconcile_brapi_cvm()
    with engine.connect() as conn:
        if not conn.execute(text("SELECT to_regclass('market.fii_metric_observations') IS NOT NULL")).scalar():
            report["blockers"].append("migração 023_fii_methodology_v4.sql pendente")
            return report
        has_024 = bool(conn.execute(text("""
            SELECT EXISTS (
              SELECT 1 FROM information_schema.columns
              WHERE table_schema='market' AND table_name='fii_metric_observations'
                AND column_name='knowledge_at'
            )
        """)).scalar())
        queries = {
            "metric_observations": "SELECT count(*) FROM market.fii_metric_observations",
            "exposures": "SELECT count(*) FROM market.fii_exposures",
            "score_snapshots": "SELECT count(*) FROM market.fii_score_snapshots",
            "universe_history": "SELECT count(*) FROM market.fii_universe_history",
            "validation_runs": "SELECT count(*) FROM market.fii_validation_runs",
            "open_reconciliation_issues": "SELECT count(*) FROM market.fii_reconciliation_issues WHERE status='open'",
            "future_available_at": "SELECT count(*) FROM market.fii_metric_observations WHERE available_at > now()",
            "reference_after_available": "SELECT count(*) FROM market.fii_metric_observations WHERE reference_date > available_at::date",
            "stale_observations_180d": "SELECT count(*) FROM market.fii_metric_observations WHERE available_at < now() - interval '180 days'",
            "suspect_or_rejected": "SELECT count(*) FROM market.fii_metric_observations WHERE quality_status IN ('suspect','rejected')",
            "unknown_fii_type": """
                WITH latest AS (
                    SELECT DISTINCT ON (ticker) ticker, active_status
                    FROM market.fii_universe_history
                    WHERE ticker ~ '^[A-Z]{4}11$'
                    ORDER BY ticker, knowledge_at DESC, reference_date DESC
                )
                SELECT count(*) FROM market.fiis f JOIN latest u USING (ticker)
                WHERE u.active_status IN ('listed','active')
                  AND (f.tipo IS NULL OR f.tipo NOT IN ('tijolo','papel','fof','hibrido'))
            """,
            "active_fii_universe": """
                WITH latest AS (
                    SELECT DISTINCT ON (ticker) ticker, active_status
                    FROM market.fii_universe_history
                    WHERE ticker ~ '^[A-Z]{4}11$'
                    ORDER BY ticker, knowledge_at DESC, reference_date DESC
                )
                SELECT count(*) FROM latest
                WHERE active_status IN ('listed','active')
            """,
            "invalid_exposure_sums": """
                SELECT count(*) FROM (
                    SELECT ticker, exposure_type, reference_date, available_at, sum(exposure_weight) total
                    FROM market.fii_exposures GROUP BY 1,2,3,4
                    HAVING sum(exposure_weight) > 1.01 OR sum(exposure_weight) < 0.99
                ) x
            """,
            "duplicate_metrics": """
                SELECT count(*) FROM (
                    SELECT ticker, metric_name, reference_date, available_at, vintage, source
                    FROM market.fii_metric_observations GROUP BY 1,2,3,4,5,6 HAVING count(*) > 1
                ) x
            """,
        }
        if has_024:
            queries.update({
                "future_knowledge_at": "SELECT count(*) FROM market.fii_metric_observations WHERE knowledge_at > now()",
                "missing_temporal_quality": """
                    SELECT count(*) FROM market.fii_metric_observations
                    WHERE knowledge_at IS NULL OR availability_quality IS NULL
                """,
                "history_mislabeled_as_pit": """
                    SELECT count(*) FROM market.fii_metric_observations
                    WHERE metadata_json->>'endpoint' LIKE '%/history'
                      AND availability_quality <> 'retrospective_backfill'
                """,
                "invalid_metric_ranges": """
                    SELECT count(*) FROM market.fii_metric_observations
                    WHERE quality_status <> 'rejected' AND (
                       (metric_name IN ('vacancia_fisica','property_delinquency','leverage',
                                           'holdings_overlap','income_recurrence',
                                           'portfolio_income_recurrence')
                           AND (value_numeric < 0 OR value_numeric > 1))
                       OR (metric_name IN ('dy_12m','dy_1m')
                           AND (value_numeric < 0 OR value_numeric > 0.60))
                       OR (metric_name='pvp' AND (value_numeric <= 0 OR value_numeric > 10))
                    )
                """,
                "missing_raw_lineage": """
                    SELECT count(*) FROM market.fii_metric_observations
                    WHERE source LIKE 'brapi_fii_v2%' AND raw_payload_id IS NULL
                      AND metadata_json->>'endpoint' NOT LIKE 'derived/%'
                """,
            })
        for name, sql in queries.items():
            report["checks"][name] = int(conn.execute(text(sql)).scalar() or 0)
    critical_zero = ("future_available_at", "future_knowledge_at", "reference_after_available",
                     "invalid_exposure_sums", "duplicate_metrics",
                     "missing_temporal_quality", "history_mislabeled_as_pit",
                     "invalid_metric_ranges", "missing_raw_lineage",
                     "open_reconciliation_issues")
    for check in critical_zero:
        if report["checks"].get(check):
            report["blockers"].append(f"{check}: {report['checks'][check]}")
    unknown = report["checks"].get("unknown_fii_type", 0)
    active = report["checks"].get("active_fii_universe", 0)
    unknown_rate = unknown / active if active else 1.0
    report["checks"]["unknown_fii_type_rate_bps"] = round(unknown_rate * 10_000)
    if unknown_rate > .05:
        report["blockers"].append(
            f"unknown_fii_type: {unknown}/{active} ({unknown_rate:.1%})")
    if not report["checks"].get("metric_observations"):
        report["blockers"].append("nenhuma observação detalhada v4")
    if not report["checks"].get("exposures"):
        report["blockers"].append("nenhuma exposição look-through")
    if not report["checks"].get("universe_history"):
        report["blockers"].append("universo histórico vazio")
    report["status"] = "passed" if not report["blockers"] else "blocked"
    with engine.begin() as conn:
        if conn.execute(text(
                "SELECT to_regclass('market.fii_quality_runs') IS NOT NULL")).scalar():
            run_id = conn.execute(text("""
                INSERT INTO market.fii_quality_runs (run_type, status, finished_at, summary_json)
                VALUES ('methodology_audit', :status, now(), CAST(:summary AS jsonb))
                RETURNING id
            """), {"status": report["status"],
                     "summary": json.dumps(report, ensure_ascii=False, default=str)}).scalar()
            rows = []
            for name, value in report["checks"].items():
                informational = {"metric_observations", "exposures", "score_snapshots",
                                 "universe_history", "validation_runs", "active_fii_universe",
                                 "suspect_or_rejected", "unknown_fii_type",
                                 "unknown_fii_type_rate_bps", "stale_observations_180d"}
                failed = name not in informational and int(value or 0) > 0
                rows.append({"run": run_id, "rule": name, "status": "failed" if failed else "passed",
                             "severity": "critical" if failed and name in critical_zero else "info",
                             "value": json.dumps({"count": int(value or 0)}),
                             "message": f"{name}: {int(value or 0)}"})
            conn.execute(text("""
                INSERT INTO market.fii_quality_results
                    (run_id, rule_code, entity_type, severity, status, observed_value, message)
                SELECT run_id, rule_code, 'dataset', severity, status,
                       CAST(observed_value AS jsonb), message
                FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
                    run_id bigint, rule_code text, severity text, status text,
                    observed_value text, message text)
            """), {"rows": json.dumps([{
                "run_id": row["run"], "rule_code": row["rule"],
                "severity": row["severity"], "status": row["status"],
                "observed_value": row["value"], "message": row["message"],
            } for row in rows], ensure_ascii=False)})
    return report


def record_validation_readiness(audit: dict) -> dict:
    """Persiste o gate formal; nunca aprova sem backtest PIT completo."""
    from core.fii_methodology import METHODOLOGY_VERSION
    engine = _engine()
    blockers = list(audit.get("blockers") or [])
    blockers.append("backtest PIT walk-forward ainda não executado")
    if engine is None:
        return {"status": "failed", "blockers": blockers + ["banco indisponível"]}
    with engine.begin() as conn:
        if not conn.execute(text(
                "SELECT to_regclass('market.fii_validation_runs') IS NOT NULL")).scalar():
            return {"status": "failed", "blockers": blockers + ["migração 023 pendente"]}
        run_id = conn.execute(text("""
            INSERT INTO market.fii_validation_runs (
                methodology_version, as_of_date, status, metrics_json,
                blockers_json, finished_at
            ) VALUES (:version, current_date, 'blocked', CAST(:metrics AS jsonb),
                      CAST(:blockers AS jsonb), now()) RETURNING id
        """), {"version": METHODOLOGY_VERSION,
                 "metrics": json.dumps({"data_audit": audit}, ensure_ascii=False, default=str),
                 "blockers": json.dumps(blockers, ensure_ascii=False)}).scalar()
    return {"id": int(run_id), "status": "blocked", "blockers": blockers}


def _release_items(endpoint: str, payload: dict) -> list[dict]:
    key = {
        "list": "fiis", "indicators": "fiis", "indicators/history": "history", "reports": "reports",
        "properties": "fiis", "properties/history": "history", "portfolio": "fiis",
        "portfolio/history": "history", "dividends": "dividends",
        "annual-reports": "reports", "financials": "financials",
    }.get(endpoint)
    return list(payload.get(key) or []) if key else []


def _register_source_releases(conn, endpoint: str, raw_id: int | None,
                              payload: dict, collected_at: datetime) -> int:
    """Registra versões lógicas da fonte sem transformar backfill em PIT."""
    if raw_id is None or not conn.execute(text(
            "SELECT to_regclass('market.fii_source_releases') IS NOT NULL")).scalar():
        return 0
    incoming: list[dict] = []
    for item in _release_items(endpoint, payload):
        ticker = str(item.get("symbol") or "").upper()
        if not ticker:
            continue
        fields = item.get("fields") or {}
        reference = (item.get("asOfDate") or item.get("referenceDate") or
                     fields.get("Data_Referencia") or item.get("paymentDate") or
                     item.get("approvedOn"))
        version = fields.get("Versao") or item.get("version") or 1
        discriminator = ""
        if endpoint == "dividends":
            discriminator = "|".join(str(item.get(key) or "") for key in (
                "label", "rate", "approvedOn", "paymentDate"))
        natural_key = f"{ticker}|{reference or 'na'}|v{version}"
        if discriminator:
            natural_key = f"{natural_key}|{discriminator}"
        content = json.dumps(item, ensure_ascii=False, default=str,
                             sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        published_raw = fields.get("Data_Entrega")
        published_at = None
        if published_raw:
            try:
                published_at = datetime.fromisoformat(str(published_raw).replace("Z", "+00:00"))
            except ValueError:
                try:
                    published_at = datetime.strptime(str(published_raw)[:10], "%Y-%m-%d").replace(
                        hour=23, minute=59, second=59, tzinfo=timezone.utc)
                except ValueError:
                    published_at = None
        is_backfill = endpoint.endswith("/history") or endpoint in {"reports", "dividends"}
        quality = ("verified_publication" if published_at else
                   "retrospective_backfill" if is_backfill else "first_observed_proxy")
        knowledge_at = published_at if published_at else collected_at
        incoming.append({
            "natural_key": natural_key,
            "reference_date": str(reference)[:10] if reference else None,
            "source_published_at": published_at,
            "first_observed_at": collected_at,
            "knowledge_at": knowledge_at,
            "availability_quality": quality,
            "raw_payload_id": raw_id,
            "content_sha256": content_hash,
            "metadata_json": {"symbol": ticker, "source_version": version},
        })
    if not incoming:
        return 0
    result = conn.execute(text("""
        WITH incoming AS (
            SELECT * FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS i(
                natural_key text, reference_date date,
                source_published_at timestamptz, first_observed_at timestamptz,
                knowledge_at timestamptz, availability_quality text,
                raw_payload_id bigint, content_sha256 text, metadata_json jsonb
            )
        ), deduped AS (
            SELECT DISTINCT ON (natural_key, content_sha256) *
            FROM incoming ORDER BY natural_key, content_sha256
        ), latest AS (
            SELECT DISTINCT ON (r.natural_key)
                   r.natural_key, r.id, r.revision_no
            FROM market.fii_source_releases r JOIN deduped i USING (natural_key)
            WHERE r.provider='brapi.dev' AND r.endpoint=:endpoint
            ORDER BY r.natural_key, r.revision_no DESC
        ), ranked AS (
            SELECT i.*, ROW_NUMBER() OVER (
                       PARTITION BY i.natural_key ORDER BY i.content_sha256) AS revision_offset
            FROM deduped i
        )
        INSERT INTO market.fii_source_releases (
            provider, endpoint, natural_key, reference_date,
            source_published_at, first_observed_at, knowledge_at,
            availability_quality, revision_no, supersedes_id,
            raw_payload_id, content_sha256, metadata_json
        )
        SELECT 'brapi.dev', :endpoint, i.natural_key, i.reference_date,
               i.source_published_at, i.first_observed_at, i.knowledge_at,
               i.availability_quality,
               COALESCE(l.revision_no,0)+i.revision_offset, l.id,
               i.raw_payload_id, i.content_sha256, i.metadata_json
        FROM ranked i LEFT JOIN latest l USING (natural_key)
        ON CONFLICT (provider, endpoint, natural_key, content_sha256) DO NOTHING
    """), {"endpoint": endpoint,
             "rows": json.dumps(incoming, ensure_ascii=False, default=str)})
    return max(int(result.rowcount or 0), 0)


def _persist_document_discoveries(conn, documents: list[dict]) -> int:
    if not documents or not conn.execute(text(
            "SELECT to_regclass('market.fii_documents') IS NOT NULL")).scalar():
        return 0
    inserted = 0
    for doc in documents:
        result = conn.execute(text("""
            INSERT INTO market.fii_documents (
                ticker, document_type, natural_key, reference_date,
                source_published_at, first_observed_at, source_url
            ) VALUES (:ticker, :kind, :natural, :reference, :published, :observed, :url)
            ON CONFLICT (document_type, natural_key) DO UPDATE SET
                source_published_at=COALESCE(EXCLUDED.source_published_at,
                                             market.fii_documents.source_published_at),
                source_url=EXCLUDED.source_url
        """), {"ticker": doc.get("ticker"), "kind": doc.get("document_type"),
                 "natural": doc.get("natural_key"), "reference": doc.get("reference_date"),
                 "published": doc.get("source_published_at"),
                 "observed": doc.get("first_observed_at"), "url": doc.get("source_url")})
        inserted += max(int(result.rowcount or 0), 0)
    return inserted


def _persist_fii_v2_payload(conn, endpoint: str, symbols: list[str],
                            response: brapi.FiiApiResponse) -> dict:
    """Persiste payload e sua normalização na mesma transação."""
    from data_pipeline.market import fii_v2
    payload = response.payload
    raw_id = repo.save_raw_payload(
        conn, None, f"fii_v2_{endpoint.replace('/', '_')}", payload,
        request_params=response.params, response_headers=response.headers,
        http_status=response.status_code, collected_at=response.collected_at)
    canonical_collected_at = response.collected_at
    if raw_id is not None:
        stored_at = conn.execute(text("""
            SELECT COALESCE((to_jsonb(p)->>'collected_at')::timestamptz, fetched_at)
            FROM market.brapi_raw_payloads p WHERE id=:id
        """), {"id": raw_id}).scalar()
        if stored_at is not None:
            canonical_collected_at = stored_at
    # Recoletas semanticamente iguais reutilizam o mesmo instante de primeira
    # observação; ``requestedAt`` é volátil e não pode criar vintages artificiais.
    payload = dict(payload)
    payload["requestedAt"] = canonical_collected_at.isoformat()
    counts = {"payloads": 1, "metricas": 0, "exposicoes": 0, "imoveis": 0,
              "dividendos": 0, "fiis_atualizados": 0, "releases": 0,
              "documentos": 0, "linhagem": 0}
    observations: list[dict] = []
    exposures: list[dict] = []
    if endpoint in {"list", "indicators"}:
        normalized = fii_v2.normalize_indicators(payload, raw_id)
        counts["fiis_atualizados"] = repo.upsert(conn, "fiis", normalized["fii_updates"])
        observations.extend(normalized["observations"])
        exposures.extend(normalized.get("exposures") or [])
    elif endpoint == "indicators/history":
        observations.extend(fii_v2.normalize_indicator_history(payload, raw_id))
    elif endpoint == "reports":
        observations.extend(fii_v2.normalize_reports(payload, raw_id))
    elif endpoint == "properties":
        normalized = fii_v2.normalize_properties(payload, raw_id)
        observations.extend(normalized["observations"])
        counts["imoveis"] = repo.upsert(conn, "fii_imoveis", normalized["properties"])
        exposures.extend(normalized.get("exposures") or [])
    elif endpoint == "properties/history":
        observations.extend(fii_v2.normalize_property_history(payload, raw_id))
    elif endpoint == "portfolio":
        normalized = fii_v2.normalize_portfolio(payload, raw_id)
        observations.extend(normalized["observations"])
        exposures.extend(normalized["exposures"])
        for inference in normalized["type_inferences"]:
            conn.execute(text("""
                UPDATE market.fiis SET tipo=:tipo
                WHERE ticker=:ticker AND (tipo IS NULL OR tipo NOT IN ('tijolo','papel','fof','hibrido'))
            """), {"ticker": inference["ticker"], "tipo": inference["tipo"]})
    elif endpoint == "portfolio/history":
        normalized = fii_v2.normalize_portfolio_history(payload, raw_id)
        observations.extend(normalized["observations"])
        exposures.extend(normalized["exposures"])
    elif endpoint == "annual-reports":
        normalized = fii_v2.normalize_annual_reports(payload, raw_id)
        observations.extend(normalized["observations"])
        counts["fiis_atualizados"] = repo.upsert(conn, "fiis", normalized["fii_updates"])
    elif endpoint == "financials":
        normalized = fii_v2.normalize_financials(payload, raw_id)
        observations.extend(normalized["observations"])
        counts["documentos"] = _persist_document_discoveries(conn, normalized["documents"])
    elif endpoint == "dividends":
        rows = fii_v2.normalize_dividends(payload)
        # A FK de dividendos exige o ativo, mesmo no primeiro run dedicado.
        repo.upsert(conn, "assets", [{"ticker": ticker,
                                      "asset_type": "fii", "exchange": "B3",
                                      "currency": "BRL", "is_active": True}
                                     for ticker in symbols])
        counts["dividendos"] = repo.upsert(conn, "dividends", rows)
    if observations:
        counts["metricas"] = repo.upsert(conn, "fii_metric_observations", observations)
    if exposures:
        counts["exposicoes"] = repo.upsert(conn, "fii_exposures", exposures)
    counts["releases"] = _register_source_releases(
        conn, endpoint, raw_id, payload, canonical_collected_at)
    counts["linhagem"] = repo.record_lineage_for_raw_payload(conn, raw_id)
    return counts


def _derive_income_observations(conn) -> int:
    from data_pipeline.market import fii_v2
    rows = conn.execute(text("""
        SELECT ticker, date_trunc('month', event_date)::date AS month,
               sum(amount)::float AS amount
        FROM market.dividends
        WHERE event_date IS NOT NULL
          AND upper(COALESCE(type,'')) NOT LIKE '%AMORT%'
          AND ticker IN (SELECT ticker FROM market.fiis)
        GROUP BY ticker, date_trunc('month', event_date)::date
    """)).fetchall()
    monthly: dict[str, dict[date, float]] = defaultdict(dict)
    for ticker, month, amount in rows:
        monthly[str(ticker)][month] = float(amount or 0)
    observations = fii_v2.income_metrics_from_monthly(monthly, as_of=datetime.now(timezone.utc).date())
    return repo.upsert(conn, "fii_metric_observations", observations)


def _latest_exposure_rows(conn, exposure_type: str) -> list[dict]:
    return [dict(row) for row in conn.execute(text("""
        WITH latest_ref AS (
            SELECT ticker, exposure_type, max(reference_date) AS reference_date
            FROM market.fii_exposures WHERE exposure_type=:kind GROUP BY 1,2
        ), latest_at AS (
            SELECT e.ticker, e.exposure_type, e.reference_date, max(e.available_at) AS available_at
            FROM market.fii_exposures e JOIN latest_ref r USING (ticker, exposure_type, reference_date)
            GROUP BY 1,2,3
        )
        SELECT e.ticker, e.exposure_name, e.exposure_weight
        FROM market.fii_exposures e JOIN latest_at l
          USING (ticker, exposure_type, reference_date, available_at)
        WHERE e.exposure_type=:kind
    """), {"kind": exposure_type}).mappings().all()]


def _derive_fof_observations(conn) -> int:
    """Deriva overlap, liquidez look-through e dupla taxa sem inventar ticker."""
    from data_pipeline.market.fii_sources import metric_observation
    holdings: dict[str, dict[str, float]] = defaultdict(dict)
    for row in _latest_exposure_rows(conn, "holding"):
        holdings[row["ticker"]][row["exposure_name"]] = float(row["exposure_weight"])
    if not holdings:
        return 0
    fund_rows = conn.execute(text("""
        SELECT ticker, regexp_replace(COALESCE(cnpj,''),'\\D','','g') AS cnpj,
               liquidez_diaria::float AS liquidity
        FROM market.fiis
    """)).mappings().all()
    by_cnpj = {row["cnpj"]: dict(row) for row in fund_rows if row["cnpj"]}
    fee_rows = conn.execute(text("""
        SELECT DISTINCT ON (ticker) ticker, value_numeric::float AS fee
        FROM market.fii_metric_observations
        WHERE metric_name='admin_fee_rate_annual' AND quality_status <> 'rejected'
        ORDER BY ticker, available_at DESC, reference_date DESC, observed_at DESC
    """)).mappings().all()
    fee_by_ticker = {row["ticker"]: float(row["fee"]) for row in fee_rows if row["fee"] is not None}
    today = datetime.now(timezone.utc)
    observations: list[dict] = []
    for ticker, weights in holdings.items():
        peers = [other for name, other in holdings.items() if name != ticker]
        overlap = max((sum(min(weight, peer.get(asset, 0.0)) for asset, weight in weights.items())
                       for peer in peers), default=None)
        matched_liquidity = 0.0
        liquidity_weight = 0.0
        underlying_fee = 0.0
        fee_weight = 0.0
        for cnpj, weight in weights.items():
            underlying = by_cnpj.get(re.sub(r"\D", "", cnpj))
            if not underlying:
                continue
            liquidity = underlying.get("liquidity")
            if liquidity is not None and float(liquidity) >= 0:
                matched_liquidity += weight * float(liquidity)
                liquidity_weight += weight
            fee = fee_by_ticker.get(underlying["ticker"])
            if fee is not None:
                underlying_fee += weight * fee
                fee_weight += weight
        own_fee = fee_by_ticker.get(ticker)
        metrics = [
            ("holdings_overlap", overlap, {"formula": "max_pairwise_sum_min_weights"}),
            ("invested_portfolio_liquidity",
             matched_liquidity / liquidity_weight if liquidity_weight >= .60 else None,
             {"lookthrough_coverage": liquidity_weight}),
            ("double_fee_burden",
             own_fee + underlying_fee / fee_weight if own_fee is not None and fee_weight >= .60 else None,
             {"lookthrough_coverage": fee_weight, "formula": "own_fee+weighted_underlying_fees"}),
        ]
        for metric, value, metadata in metrics:
            if value is None:
                continue
            observations.append(metric_observation(
                ticker=ticker, metric_name=metric, value=value,
                reference_date=today.date(), available_at=today,
                source="brapi_fii_v2_derived", vintage=f"derived:{today.date()}",
                metadata=metadata,
            ))
    return repo.upsert(conn, "fii_metric_observations", observations)


def _refresh_pro_universe(engine, *, limit: int | None = None) -> dict:
    """Descobre o universo corrente pela listagem Pro e cria snapshot prospectivo."""
    response = _fetch_fii_v2_batch("list", [], params={"limit": 100})
    response.payload["fiis"] = [
        row for row in (response.payload.get("fiis") or [])
        if re.fullmatch(r"[A-Z]{4}11", str(row.get("symbol") or "").upper())
        and float(row.get("price") or 0) > 0
    ]
    if limit:
        response.payload["fiis"] = response.payload["fiis"][:max(int(limit), 0)]
    symbols = sorted({str(row.get("symbol") or "").upper()
                      for row in response.payload.get("fiis") or [] if row.get("symbol")})
    with engine.begin() as conn:
        counts = _persist_fii_v2_payload(conn, "list", symbols, response)
        if conn.execute(text(
                "SELECT to_regclass('market.fii_universe_history') IS NOT NULL")).scalar():
            today = response.collected_at.date()
            listed_rows = [{
                "ticker": ticker, "reference_date": today,
                "available_at": response.collected_at, "knowledge_at": response.collected_at,
                "availability_quality": "first_observed_proxy", "active_status": "listed",
                "successor_ticker": None, "source": "brapi_fii_v2_list",
                "metadata_json": json.dumps({"prospective_snapshot": True}, ensure_ascii=False),
            } for ticker in symbols]
            counts["universo"] = repo.upsert(conn, "fii_universe_history", listed_rows)
    return {"symbols": symbols, **counts}


def reclassify_fii_types_from_cache() -> dict:
    """Reaplica a taxonomia por tipo usando payloads brutos, sem rede."""
    from data_pipeline.market import fii_v2
    engine = _engine()
    if engine is None:
        return {"status": "failed", "updated": 0}
    authoritative: dict[str, str] = {}
    fallback: dict[str, str] = {}
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT endpoint, payload_json
            FROM market.brapi_raw_payloads
            WHERE endpoint IN ('fii_v2_indicators','fii_v2_portfolio')
              AND request_status='success' AND payload_json IS NOT NULL
            ORDER BY collected_at, id
        """)).mappings().all()
    for row in rows:
        payload = dict(row["payload_json"] or {})
        if row["endpoint"] == "fii_v2_indicators":
            normalized = fii_v2.normalize_indicators(payload)
            for item in normalized["fii_updates"]:
                if item.get("ticker") and item.get("tipo"):
                    authoritative[str(item["ticker"])] = str(item["tipo"])
        else:
            normalized = fii_v2.normalize_portfolio(payload)
            for item in normalized["type_inferences"]:
                if item.get("ticker") and item.get("tipo"):
                    fallback[str(item["ticker"])] = str(item["tipo"])
    classifications = {**fallback, **authoritative}
    if not classifications:
        return {"status": "empty", "updated": 0}
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE market.fiis f SET tipo=x.tipo
            FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(ticker text, tipo text)
            WHERE f.ticker=x.ticker AND x.tipo IN ('tijolo','papel','fof','hibrido')
              AND f.tipo IS DISTINCT FROM x.tipo
        """), {"rows": json.dumps([{"ticker": ticker, "tipo": fii_type}
                                     for ticker, fii_type in classifications.items()])})
    return {"status": "updated", "updated": max(int(result.rowcount or 0), 0),
            "classified": len(classifications), "authoritative": len(authoritative),
            "fallback": len(fallback)}


def ingest_v2_details(limit: int | None = None, tickers: list[str] | None = None,
                      delay: float = .10) -> dict:
    """Ingere endpoints Pro por categoria e recalcula a Lista de Diligência."""
    engine = _engine()
    progress = {"fundos": 0, "requisicoes": 0, "payloads": 0, "metricas": 0,
                "exposicoes": 0, "imoveis": 0, "dividendos": 0,
                "fiis_atualizados": 0, "releases": 0, "documentos": 0,
                "linhagem": 0, "erros": 0, "por_endpoint": {}}
    if engine is None:
        return {**progress, "erros": -1}
    with engine.connect() as conn:
        if not conn.execute(text(
            "SELECT to_regclass('market.fii_metric_observations') IS NOT NULL"
        )).scalar():
            return {**progress, "erros": -1, "blocker": "migração 023 pendente"}
    try:
        universe_result = _refresh_pro_universe(engine, limit=limit)
        universe = universe_result.pop("symbols")
        progress["universo"] = universe_result
    except brapi.BrapiAuthError as exc:
        logger.error("Acesso Brapi Pro inválido: %s", exc)
        return {**progress, "erros": -1,
                "blocker": "BRAPI_TOKEN inválido, inativo ou sem acesso ao plano Pro"}
    except Exception as exc:
        logger.warning("Listagem Pro indisponível; usando universo persistido: %s", exc)
        with engine.connect() as conn:
            universe = [str(row[0]) for row in conn.execute(text(
                "SELECT ticker FROM market.fiis ORDER BY ticker"
            )).fetchall()]
    if tickers:
        requested = {str(ticker).upper().replace(".SA", "") for ticker in tickers}
        universe = [ticker for ticker in universe if ticker in requested]
    if limit and "universo" not in progress:
        universe = universe[:max(int(limit), 0)]
    progress["fundos"] = len(universe)
    if not universe:
        return progress

    endpoint_plan = [
        ("indicators", universe, 20),
        ("indicators/history", universe, 20),
        ("reports", universe, 20),
        ("dividends", universe, 20),
    ]
    # Classificação é atualizada primeiro; os endpoints estruturais seguintes
    # podem então ser roteados por tipo sem generalizar tijolo/papel/FoF.
    for endpoint, symbols, batch_size in endpoint_plan[:1]:
        _run_fii_v2_endpoint(engine, endpoint, symbols, batch_size, delay, progress)
    # Portfolio também funciona como classificador de fallback quando o campo
    # segmentType vem nulo (composição unívoca: imóvel, CRI ou cotas de FII).
    _run_fii_v2_endpoint(engine, "portfolio", universe, 5, delay, progress)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT ticker, tipo FROM market.fiis WHERE ticker = ANY(:tickers)"),
                            {"tickers": universe}).fetchall()
    by_type: dict[str, list[str]] = defaultdict(list)
    for ticker, fii_type in rows:
        by_type[str(fii_type or "unknown")].append(str(ticker))
    typed = [ticker for kind in ("tijolo", "papel", "fof", "hibrido") for ticker in by_type.get(kind, [])]
    structural_plan = [
        ("indicators/history", typed, 20, None),
        ("reports", typed, 20, {"allVersions": "true", "limit": 100}),
        ("dividends", typed, 20, None),
        ("portfolio/history", typed, 10, None),
        ("properties", by_type.get("tijolo", []) + by_type.get("hibrido", []), 10, None),
        ("properties/history", by_type.get("tijolo", []) + by_type.get("hibrido", []), 20, None),
        ("annual-reports", typed, 20,
         {"include": "risk,admin,governance,properties", "limit": 100}),
        ("financials", typed, 20, {"limit": 100}),
    ]
    for endpoint, symbols, batch_size, params in structural_plan:
        _run_fii_v2_endpoint(engine, endpoint, symbols, batch_size, delay, progress,
                             params=params)
    with engine.begin() as conn:
        progress["metricas"] += _derive_income_observations(conn)
        progress["metricas"] += _derive_fof_observations(conn)
    progress["audit"] = audit_methodology_v4_data()
    progress["validation"] = record_validation_readiness(progress["audit"])
    progress["snapshot"] = snapshot_methodology_v4()
    return progress


def _run_fii_v2_endpoint(engine, endpoint: str, symbols: list[str], batch_size: int,
                         delay: float, progress: dict,
                         params: dict | None = None) -> None:
    endpoint_counts = {"requisicoes": 0, "metricas": 0, "exposicoes": 0,
                       "imoveis": 0, "dividendos": 0, "releases": 0,
                       "documentos": 0, "linhagem": 0, "cache_hits": 0,
                       "erros": 0}
    batches = _batches(symbols, batch_size)
    raw_endpoint = f"fii_v2_{endpoint.replace('/', '_')}"
    with engine.connect() as conn:
        cached_requests = [dict(row[0] or {}) for row in conn.execute(text("""
            SELECT DISTINCT request_params_json
            FROM market.brapi_raw_payloads
            WHERE endpoint=:endpoint AND request_status='success'
              AND collected_at >= now() - interval '6 hours'
              AND request_params_json ? 'symbols'
        """), {"endpoint": raw_endpoint}).fetchall()]
    for index, batch in enumerate(batches):
        cache_hit = any(
            str(item.get("symbols") or "") == ",".join(batch)
            and all(str(item.get(key)) == str(value) for key, value in (params or {}).items())
            for item in cached_requests
        )
        if cache_hit:
            endpoint_counts["cache_hits"] += 1
            continue
        try:
            response = _fetch_fii_v2_batch(endpoint, batch, params=params)
            with engine.begin() as conn:
                counts = _persist_fii_v2_payload(conn, endpoint, batch, response)
            progress["requisicoes"] += 1
            endpoint_counts["requisicoes"] += 1
            for key, value in counts.items():
                if key in progress:
                    progress[key] += int(value or 0)
                if key in endpoint_counts:
                    endpoint_counts[key] += int(value or 0)
        except Exception as exc:
            logger.warning("FII v2 %s lote %s: %s", endpoint, batch, exc)
            progress["erros"] += 1
            endpoint_counts["erros"] += 1
            try:
                with engine.begin() as conn:
                    repo.save_raw_payload(conn, None, f"fii_v2_{endpoint.replace('/', '_')}",
                                          None, status="failed", error=exc,
                                          request_params={"symbols": batch, **(params or {})})
            except Exception:
                pass
        if index < len(batches) - 1 and delay > 0:
            sched.sleep_jittered(base=delay, jitter=.25)
    progress["por_endpoint"][endpoint] = endpoint_counts


def _row(m: dict, *, include_score: bool = True, metadata_ready: bool = False,
         calculated_at=None) -> dict:
    price = m.get("price")
    pvp = m.get("pvp")
    dy = m.get("dy_12m")
    price = price if price is not None and price > 0 else None
    pvp = pvp if pvp is not None and pvp > 0 else None
    dy = dy if dy is not None and 0 <= dy <= 1 else None
    row = {"ticker": m["ticker"], "cnpj": m.get("cnpj"), "name": m.get("name"),
           "segmento": m.get("segmento"), "price": price, "pvp": pvp,
           "dy_12m": dy, "liquidez_diaria": m.get("liquidez_diaria")}
    if include_score:
        row["score"] = m.get("score")
        if metadata_ready:
            row["score_version"] = fz.SCORE_VERSION
            row["score_calculated_at"] = calculated_at
    if metadata_ready:
        row["metrics_fetched_at"] = calculated_at
    return row


def _extract_quote(payload) -> dict | None:
    p = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(p, dict):
        return None
    quote = (p.get("results") or [p])[0]
    return quote if isinstance(quote, dict) else None


def _latest_fii_payloads(conn) -> list[tuple[str, dict]]:
    """Último payload realmente completo de FII, ignorando cotações genéricas."""
    rows = conn.execute(text(
        "SELECT ticker, payload_json FROM market.brapi_raw_payloads "
        "WHERE endpoint IN ('quote_fii_full', 'quote') "
        "AND request_status='success' AND payload_json IS NOT NULL "
        "ORDER BY ticker, "
        "CASE WHEN endpoint='quote_fii_full' THEN 0 ELSE 1 END, id DESC"
    )).fetchall()
    selected: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for ticker, payload in rows:
        if ticker in seen:
            continue
        try:
            quote = _extract_quote(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if quote and fz.is_fii(quote):
            selected.append((ticker, quote))
            seen.add(ticker)
    return selected


def _ranking_coverage_ok(found: int, expected: int) -> bool:
    minimum = float(os.getenv("FII_RANK_MIN_COVERAGE", "0.85"))
    return expected > 0 and found / expected >= minimum


def ingest(limit: int | None = None, tickers: list[str] | None = None,
           weights: dict | None = None) -> dict:
    """Coleta FIIs (rede), classifica/computa/rankeia e grava em market.fiis."""
    engine = _engine()
    repo.reset_db_cols_cache()  # migração 020 pode ter sido aplicada com processo vivo
    prog = {"candidatos": 0, "fiis": 0, "etfs_ignorados": 0, "erros": 0,
            "gravados": 0, "ranking_aplicado": False, "cobertura": 0.0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            logger.error("market.fiis ausente — rode 015_market_fiis.sql.")
            return {**prog, "erros": -1}
        metadata_ready = _score_metadata_ready(conn)
        # VPA do último enrich_cvm (roda DEPOIS de `fiis` no cron) — no run
        # diário, é o VPA de ontem: correto (VPA é mensal). 1º run: vazio →
        # pvp_efetivo cai no priceToBook da brapi.
        vpa_map = _vpa_map(conn)

    full_run = not tickers and limit is None
    if tickers:
        universe = [t.upper().replace(".SA", "") for t in tickers]
    else:
        try:
            universe = [t for t in brapi.fetch_fund_list() if t.endswith("11")]
        except Exception as exc:
            logger.error("fetch_fund_list: %s", exc)
            return {**prog, "erros": -1}
    if limit:
        universe = universe[:limit]
    prog["candidatos"] = len(universe)

    ref = datetime.now(timezone.utc).date()
    delay = float(os.getenv("MARKET_DELAY", "1.0"))
    metrics: list[dict] = []
    for i, tk in enumerate(universe):
        try:
            quote = sched.with_backoff(
                lambda: brapi.fetch_quote_full(tk),
                retries=3, base=float(os.getenv("MARKET_BACKOFF", "4.0")),
                on_block=brapi.is_rate_limited)
            if not quote:
                prog["erros"] += 1
            else:
                with engine.begin() as conn:
                    repo.save_raw_payload(
                        conn, tk, "quote_fii_full", quote, status="success"
                    )
                m = fz.compute_fii(quote, ref)
                if m is None:
                    prog["etfs_ignorados"] += 1
                else:
                    metrics.append(m)
                    prog["fiis"] += 1
        except Exception as exc:
            logger.warning("fii %s: %s", tk, exc)
            prog["erros"] += 1
        if i < len(universe) - 1:
            sched.sleep_jittered(base=delay)

    if metrics:
        # P/VP efetivo (fix auditoria FII 2026-07): preço ÷ VPA CVM quando
        # disponível (VPA do último enrich_cvm) — fonte oficial em vez do
        # priceToBook da brapi. O valor derivado é gravado em market.fiis.pvp;
        # o priceToBook bruto permanece recuperável nos payloads.
        metrics = [
            {**m, "pvp": fz.pvp_efetivo(m.get("price"),
                                        vpa_map.get(m["ticker"]),
                                        m.get("pvp"))}
            for m in metrics
        ]
        calculated_at = datetime.now(timezone.utc)
        successful = prog["fiis"] + prog["etfs_ignorados"]
        prog["cobertura"] = round(successful / max(len(universe), 1), 4)
        apply_ranking = full_run and _ranking_coverage_ok(successful, len(universe))
        ranked_by = {}
        if apply_ranking:
            ranked_by = {
                r["ticker"]: r for r in fz.rank_fiis(metrics, weights=weights)
            }
            prog["ranking_aplicado"] = True
        rows = [
            _row(
                ranked_by.get(m["ticker"], m),
                include_score=apply_ranking,
                metadata_ready=metadata_ready,
                calculated_at=calculated_at,
            )
            for m in metrics
        ]
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", rows)
            if not apply_ranking:
                repo.log_quality(
                    conn, table_name="fiis", field_name="score",
                    issue_type="ranking_preservado_cobertura_insuficiente",
                    old_value=f"{successful}/{len(universe)}",
                    severity="warning", source="brapi.dev",
                )
            prog["snapshot_mensal"] = _snapshot_score_mensal(
                conn, ranked_by, ref) if apply_ranking else 0
    logger.info("market/fii ingest: %s", prog)
    return prog


def _vpa_map(conn) -> dict[str, float]:
    """VPA CVM por ticker — p/ P/VP efetivo no ranking (auditoria FII 2026-07)."""
    try:
        return {str(t).upper(): float(v) for t, v in conn.execute(text(
            "SELECT ticker, vpa FROM market.fiis "
            "WHERE vpa IS NOT NULL AND vpa > 0")).fetchall()}
    except Exception as exc:
        logger.warning("_vpa_map: %s — ranking segue com priceToBook brapi", exc)
        return {}


def _snapshot_score_mensal(conn, ranked_by: dict, ref) -> int:
    """Snapshot mensal point-in-time do score e seus inputs (migração 020).

    Grava score/price/dy_12m/pvp/liquidez em market.fii_metrics_monthly
    (chave ticker+ref_month; runs no mesmo mês sobrescrevem — 'último
    cálculo do mês'). Sem isso a metodologia FII nunca poderá ser validada
    (rank-IC exige inputs históricos). Colunas CVM da mesma linha são
    preservadas (o upsert só atualiza as colunas presentes).
    """
    if not ranked_by:
        return 0
    if "score" not in repo._db_cols(conn, "fii_metrics_monthly"):
        logger.info("snapshot mensal de score pulado — migração 020 pendente")
        return 0
    ref_month = ref.replace(day=1)
    snap = [{
        "ticker": r["ticker"], "ref_month": ref_month,
        "score": r.get("score"), "score_version": r.get("score_version"),
        "price": r.get("price"), "dy_12m": r.get("dy_12m"),
        "pvp": r.get("pvp"), "liquidez_diaria": r.get("liquidez_diaria"),
    } for r in ranked_by.values()]
    return repo.upsert(conn, "fii_metrics_monthly", snap)


def ingest_benchmark(ticker: str = "XFIX11") -> dict:
    """
    Persiste o histórico do benchmark de FIIs em market.historical_prices.

    A brapi NÃO serve histórico do índice IFIX puro (símbolo "IFIX" devolve só a
    cotação spot). Usamos o ETF **XFIX11** (Trend ETF IFIX Fundo de Índice), que
    replica o IFIX e tem `adjustedClose` (retorno total) com ~69 meses de série —
    proxy correto do IFIX para comparar com a carteira no backtest.
    """
    from data_pipeline.market import normalize as nz
    engine = _engine()
    prog = {"ticker": ticker, "precos": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    try:
        quote = brapi.fetch_quote(ticker, range_="max", interval="1mo",
                                  dividends=False, fundamental=False)
    except Exception as exc:
        logger.warning("ingest_benchmark %s: %s", ticker, exc)
        return {**prog, "erros": -1}
    if not quote:
        return {**prog, "erros": -1}
    with engine.begin() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        repo.save_raw_payload(conn, ticker, "quote", quote, status="success")
        # asset_type 'other' (o CHECK não tem 'index'); benchmark é lido por ticker.
        repo.upsert(conn, "assets", [{
            "ticker": ticker, "company_id": None, "asset_type": "other",
            "exchange": "B3", "currency": "BRL", "is_active": True}])
        prog["precos"] = repo.upsert(conn, "historical_prices", nz.price_rows(quote))
    logger.info("market/ingest_benchmark: %s", prog)
    return prog


def backfill_series() -> dict:
    """
    Persiste as SÉRIES históricas dos FIIs (preços + rendimentos) em
    market.historical_prices / market.dividends, a partir dos payloads brutos já
    salvos (SEM rede). Cria as linhas em market.assets (asset_type='fii') —
    pré-requisito do backtest da carteira. Idempotente.
    """
    from data_pipeline.market import normalize as nz
    engine = _engine()
    prog = {"fiis": 0, "precos": 0, "dividendos": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        fiis = [r[0] for r in conn.execute(text("SELECT ticker FROM market.fiis")).fetchall()]
        payloads = dict(_latest_fii_payloads(conn))
    for tk in fiis:
        try:
            quote = payloads.get(tk)
            if not quote:
                continue
            with engine.begin() as conn:
                # asset FII (company_id nulo; FK das séries aponta p/ assets.ticker)
                repo.upsert(conn, "assets", [{
                    "ticker": tk, "company_id": None, "asset_type": "fii",
                    "exchange": "B3", "currency": "BRL", "is_active": True}])
                prog["precos"] += repo.upsert(conn, "historical_prices", nz.price_rows(quote))
                prog["dividendos"] += repo.upsert(conn, "dividends", nz.dividend_rows(quote))
                prog["fiis"] += 1
        except Exception as exc:
            logger.warning("fii backfill_series %s: %s", tk, exc)
            prog["erros"] += 1
    logger.info("market/fii backfill_series: %s", prog)
    return prog


def enrich_cvm(year: int | None = None) -> dict:
    """
    Enriquece market.fiis com o Informe Mensal de FIIs da CVM (join por CNPJ):
    segmento real, tipo (tijolo/papel/fof/híbrido), patrimônio, VPA, nº cotistas
    e composição de ativos. Requer que o ingest da brapi já tenha gravado o CNPJ.
    """
    import core.cvm_fii as cvm
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"ano": year, "anos_consultados": [], "fiis_no_banco": 0,
            "casados": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    year = year or datetime.now(timezone.utc).year
    # Une o ano anterior ao atual. O arquivo do ano corrente pode existir ainda
    # sem conter todos os fundos; registros mais novos sobrescrevem os antigos.
    by_cnpj: dict[str, dict] = {}
    for candidate_year in (year - 1, year):
        data = cvm.fetch_informe(candidate_year)
        if not data:
            continue
        prog["anos_consultados"].append(candidate_year)
        by_cnpj.update(cvm.parse_informe(data, candidate_year))
    if not by_cnpj:
        prog["erros"] = -1
        return prog
    prog["ano"] = max(prog["anos_consultados"])
    # tickers do banco com CNPJ
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT ticker, cnpj FROM market.fiis WHERE cnpj IS NOT NULL")).fetchall()
    prog["fiis_no_banco"] = len(rows)
    out = []
    for ticker, cnpj in rows:
        rec = by_cnpj.get(cvm.only_digits(cnpj))
        if not rec:
            continue
        prog["casados"] += 1
        out.append({
            "ticker": ticker, "isin": rec.get("isin"), "segmento_cvm": rec.get("segmento"),
            "tipo": rec.get("tipo"), "tipo_gestao": rec.get("tipo_gestao"),
            "patrimonio_liquido": rec.get("patrimonio_liquido"), "vpa": rec.get("vpa"),
            "num_cotistas": int(rec["num_cotistas"]) if rec.get("num_cotistas") else None,
            "pct_imoveis": rec.get("pct_imoveis"), "pct_papel": rec.get("pct_papel"),
            "pct_caixa": rec.get("pct_caixa"), "pct_fundos": rec.get("pct_fundos"),
            "cvm_ref_date": rec.get("ref_date"),
        })
    if out:
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", out)
    logger.info("market/fii enrich_cvm: %s", prog)
    return prog


def backfill_metrics_monthly(years: int = 3) -> dict:
    """
    Série MENSAL de fundamentos do FII (VPA/PL/cotistas/DY/composição) a partir do
    Informe Mensal da CVM dos últimos `years` anos -> market.fii_metrics_monthly.
    Casa por CNPJ com market.fiis (só grava tickers conhecidos). VPA mensal + preço
    bruto (market.historical_prices) dão o P/VP histórico na leitura. Idempotente.
    """
    import core.cvm_fii as cvm
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"anos": [], "fiis_no_banco": 0, "linhas": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text(
            "SELECT ticker, cnpj FROM market.fiis WHERE cnpj IS NOT NULL")).fetchall()
    tickers_by_cnpj: dict[str, str] = {cvm.only_digits(c): t for t, c in rows}
    prog["fiis_no_banco"] = len(tickers_by_cnpj)
    if not tickers_by_cnpj:
        return prog

    cur_year = datetime.now(timezone.utc).year
    out: list[dict] = []
    for year in range(cur_year, cur_year - max(1, years), -1):
        data = cvm.fetch_informe(year)
        if not data:
            continue
        prog["anos"].append(year)
        by_cnpj = cvm.parse_informe_monthly(data, year)
        for cnpj, series in by_cnpj.items():
            tk = tickers_by_cnpj.get(cnpj)
            if not tk:
                continue
            for rec in series:
                out.append({"ticker": tk, **rec})
    prog["linhas"] = len(out)
    if out:
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fii_metrics_monthly", out)
    logger.info("market/fii backfill_metrics_monthly: %s", prog)
    return prog


def enrich_vacancia() -> dict:
    """
    Recalcula a vacância do fundo a partir dos imóveis já coletados
    (market.fii_imoveis), ponderada pela área, e grava em market.fiis.vacancia.
    SEM rede — rode após `fiis-imoveis`. Útil para refrescar o agregado.
    """
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"fiis_com_imoveis": 0, "com_vacancia": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text("""
            SELECT ticker,
                   CASE WHEN SUM(area_m2) FILTER (WHERE vacancia IS NOT NULL) > 0
                        THEN SUM(vacancia * area_m2) FILTER (WHERE vacancia IS NOT NULL AND area_m2 > 0)
                             / NULLIF(SUM(area_m2) FILTER (WHERE vacancia IS NOT NULL AND area_m2 > 0), 0)
                        ELSE AVG(vacancia) END AS vac
            FROM market.fii_imoveis GROUP BY ticker
        """)).fetchall()
    prog["fiis_com_imoveis"] = len(rows)
    ref = datetime.now(timezone.utc).date().isoformat()
    out = [{"ticker": tk, "vacancia": round(float(vac), 4), "vacancia_ref_date": ref}
           for tk, vac in rows if vac is not None]
    prog["com_vacancia"] = len(out)
    if out:
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", out)
    logger.info("market/fii enrich_vacancia: %s", prog)
    return prog


def ingest_imoveis() -> dict:
    """
    Carteira de imóveis (scraping Status Invest) p/ FIIs de tijolo/híbrido ->
    market.fii_imoveis. Grava num_imoveis e a vacância do fundo (média ponderada
    pela área) em market.fiis. Best-effort: cobertura varia por fundo.
    """
    import core.fii_imoveis as fim
    from datetime import datetime, timezone
    engine = _engine()
    prog = {"fiis": 0, "com_imoveis": 0, "imoveis": 0, "gravados": 0, "erros": 0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        rows = conn.execute(text(
            "SELECT ticker FROM market.fiis "
            "WHERE tipo IN ('tijolo', 'hibrido')")).fetchall()
    tickers = [r[0] for r in rows]
    prog["fiis"] = len(tickers)
    ref = datetime.now(timezone.utc).date().isoformat()
    delay = float(os.getenv("MARKET_DELAY", "1.0"))
    for i, tk in enumerate(tickers):
        try:
            imoveis = fim.fetch_fii_imoveis(tk)
            if imoveis:
                vac = fim.vacancia_media(imoveis)
                with engine.begin() as conn:
                    rows_db = [{"ticker": tk, **im} for im in imoveis]
                    prog["gravados"] += repo.upsert(conn, "fii_imoveis", rows_db)
                    fii_upd = {"ticker": tk, "num_imoveis": len(imoveis)}
                    if vac is not None:
                        fii_upd.update(vacancia=vac, vacancia_ref_date=ref)
                    repo.upsert(conn, "fiis", [fii_upd])
                prog["com_imoveis"] += 1
                prog["imoveis"] += len(imoveis)
        except Exception as exc:
            logger.warning("imoveis %s: %s", tk, exc)
            prog["erros"] += 1
        if i < len(tickers) - 1:
            sched.sleep_jittered(base=delay)
    logger.info("market/fii ingest_imoveis: %s", prog)
    return prog


def reprocess(weights: dict | None = None) -> dict:
    """Re-rankeia a partir dos payloads brutos já salvos (SEM rede)."""
    engine = _engine()
    repo.reset_db_cols_cache()  # migração 020 pode ter sido aplicada com processo vivo
    prog = {"candidatos": 0, "fiis": 0, "etfs_ignorados": 0, "erros": 0,
            "gravados": 0, "ranking_aplicado": False, "cobertura": 0.0}
    if engine is None:
        return {**prog, "erros": -1}
    with engine.connect() as conn:
        if not _schema_ready(conn):
            return {**prog, "erros": -1}
        metadata_ready = _score_metadata_ready(conn)
        expected = int(conn.execute(text("SELECT COUNT(*) FROM market.fiis")).scalar() or 0)
        rows = _latest_fii_payloads(conn)
        vpa_map = _vpa_map(conn)
    ref = datetime.now(timezone.utc).date()
    metrics: list[dict] = []
    for tk, quote in rows:
        try:
            prog["candidatos"] += 1
            m = fz.compute_fii(quote, ref)
            if m is None:
                prog["etfs_ignorados"] += 1
            else:
                metrics.append(m)
                prog["fiis"] += 1
        except Exception as exc:
            logger.warning("fii reprocess %s: %s", tk, exc)
            prog["erros"] += 1
    if metrics:
        # P/VP efetivo com VPA CVM — mesmo tratamento do ingest (auditoria FII)
        metrics = [
            {**m, "pvp": fz.pvp_efetivo(m.get("price"),
                                        vpa_map.get(m["ticker"]),
                                        m.get("pvp"))}
            for m in metrics
        ]
        calculated_at = datetime.now(timezone.utc)
        prog["cobertura"] = round(len(metrics) / max(expected, 1), 4)
        apply_ranking = _ranking_coverage_ok(len(metrics), expected)
        ranked_by = (
            {r["ticker"]: r for r in fz.rank_fiis(metrics, weights=weights)}
            if apply_ranking else {}
        )
        prog["ranking_aplicado"] = apply_ranking
        out = [
            _row(
                ranked_by.get(m["ticker"], m), include_score=apply_ranking,
                metadata_ready=metadata_ready, calculated_at=calculated_at,
            )
            for m in metrics
        ]
        with engine.begin() as conn:
            prog["gravados"] = repo.upsert(conn, "fiis", out)
            if not apply_ranking:
                repo.log_quality(
                    conn, table_name="fiis", field_name="score",
                    issue_type="reprocessamento_incompleto_score_preservado",
                    old_value=f"{len(metrics)}/{expected}", severity="error",
                    source="brapi_raw_payloads",
                )
    logger.info("market/fii reprocess: %s", prog)
    return prog

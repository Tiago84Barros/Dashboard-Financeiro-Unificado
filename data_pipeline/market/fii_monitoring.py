"""Monitoramento operacional, estatístico e de cobertura do pipeline de FIIs."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy import text

from data_pipeline.utils.db_utils import get_pipeline_engine


def _severity(status: str) -> str:
    return {"passed": "info", "warning": "medium", "failed": "high"}.get(status, "medium")


def run_monitoring() -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "alerts": [{"message": "banco indisponível"}]}
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        metrics = dict(conn.execute(text("""
            SELECT
              (SELECT extract(epoch FROM (now()-max(updated_at)))/86400 FROM market.fiis) AS fii_age_days,
              (SELECT extract(epoch FROM (now()-max(observed_at)))/86400
                 FROM market.fii_metric_observations) AS metric_age_days,
              (SELECT count(*) FROM market.fii_documents
                 WHERE processing_status IN ('pending','failed','needs_review')) AS document_backlog,
              (SELECT count(*) FROM market.fii_extraction_evidence
                 WHERE validation_status='pending') AS evidence_backlog,
              (SELECT count(*) FROM market.fii_extraction_evidence
                 WHERE validation_method='human' AND reviewed_at IS NOT NULL
                   AND reviewer_id IS NOT NULL) AS human_reviews,
              (SELECT count(*) FROM market.fii_extraction_evidence
                 WHERE validation_method='human'
                   AND (reviewed_at IS NULL OR reviewer_id IS NULL)) AS invalid_human_reviews,
              (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='market'
                   AND c.relname IN ('fii_documents','fii_document_versions',
                     'fii_extraction_runs','fii_extraction_evidence',
                     'fii_metric_observations','fii_audit_events')
                   AND NOT c.relrowsecurity) AS rls_missing_tables,
              (SELECT count(*) FROM market.fii_reconciliation_issues WHERE status='open') AS reconciliation_open,
              (SELECT count(*) FROM market.fii_quality_results
                 WHERE status IN ('failed','quarantined')
                   AND created_at >= now()-interval '7 days') AS quality_failures_7d,
              (SELECT count(*) FROM market.fii_cvm_archive_loads l
                 WHERE l.parser_version=(SELECT parser_version
                         FROM market.fii_cvm_archive_loads
                         ORDER BY started_at DESC LIMIT 1)
                   AND l.status='failed') AS cvm_archive_failures,
              (SELECT count(*) FROM market.fii_cvm_archive_loads l
                 WHERE l.parser_version=(SELECT parser_version
                         FROM market.fii_cvm_archive_loads
                         ORDER BY started_at DESC LIMIT 1)
                   AND l.status='completed') AS cvm_archives_completed,
              (SELECT count(*) FILTER (WHERE source_release_id IS NOT NULL)::numeric /
                      NULLIF(count(*),0)
                 FROM market.fii_metric_observations
                 WHERE source IN ('cvm_informe_mensal','cvm_informe_trimestral',
                                  'cvm_informe_anual','cvm_dfin','cvm_eventuais'))
                  AS cvm_release_coverage,
              (SELECT count(DISTINCT reference_date) FROM market.fii_pit_score_snapshots
                 WHERE methodology_version=:version) AS pit_dates,
              (SELECT status FROM market.fii_validation_runs
                 WHERE methodology_version=:version
                 ORDER BY coalesce(finished_at,started_at) DESC LIMIT 1) AS validation_status
        """), {"version": "6.0.0"}).mappings().one())
        latest = conn.execute(text("""
            WITH current_scores AS (
              SELECT DISTINCT ON (ticker) ticker,confidence,coverage,
                     data_readiness_status
              FROM market.fii_score_snapshots
              WHERE methodology_version=:version
                AND reference_date=(SELECT max(reference_date)
                    FROM market.fii_score_snapshots WHERE methodology_version=:version)
              ORDER BY ticker,available_at DESC,id DESC
            )
            SELECT avg(confidence) AS confidence, avg(coverage) AS coverage,
                   count(*) FILTER (WHERE data_readiness_status='ready')::numeric /
                       NULLIF(count(*),0) AS ready_fraction
            FROM current_scores
        """), {"version": "6.0.0"}).mappings().one()
        metrics.update(dict(latest))
        rules = [
            ("fii_snapshot_freshness", float(metrics.get("fii_age_days") or 999) <= 2,
             metrics.get("fii_age_days"), "snapshot corrente sem atualização por mais de 2 dias"),
            ("metric_freshness", float(metrics.get("metric_age_days") or 999) <= 7,
             metrics.get("metric_age_days"), "observações fundamentais sem atualização por mais de 7 dias"),
            ("document_backlog", int(metrics.get("document_backlog") or 0) <= 500,
             metrics.get("document_backlog"), "fila de documentos acima de 500"),
            ("human_review_backlog", int(metrics.get("evidence_backlog") or 0) <= 250,
             metrics.get("evidence_backlog"), "fila de evidências humanas acima de 250"),
            ("human_calibration_integrity",
             int(metrics.get("invalid_human_reviews") or 0) == 0,
             metrics.get("invalid_human_reviews"),
             "decisões marcadas como humanas sem revisor ou reviewed_at"),
            ("fii_backend_rls", int(metrics.get("rls_missing_tables") or 0) == 0,
             metrics.get("rls_missing_tables"),
             "tabelas backend-only do pipeline FII sem RLS habilitado"),
            ("quality_failures", int(metrics.get("quality_failures_7d") or 0) == 0,
             metrics.get("quality_failures_7d"), "falhas ou quarentenas de qualidade nos últimos 7 dias"),
            ("cvm_archive_failures", int(metrics.get("cvm_archive_failures") or 0) == 0,
             metrics.get("cvm_archive_failures"), "partições CVM falharam no parser corrente"),
            ("cvm_release_lineage", float(metrics.get("cvm_release_coverage") or 0) >= .95,
             metrics.get("cvm_release_coverage"), "menos de 95% das métricas CVM possuem release versionada"),
            ("score_coverage", float(metrics.get("coverage") or 0) >= .70,
             metrics.get("coverage"), "cobertura média do score abaixo de 70%"),
            ("pit_history", int(metrics.get("pit_dates") or 0) >= 36,
             metrics.get("pit_dates"), "menos de 36 datas PIT reconstruídas"),
            ("methodology_validation", metrics.get("validation_status") == "passed",
             metrics.get("validation_status"), "metodologia PIT ainda não aprovada"),
        ]
        alerts = []
        for code, passed, value, message in rules:
            status = "passed" if passed else "failed"
            alert_id = conn.execute(text("""
                INSERT INTO market.fii_pipeline_alerts (
                    alert_code,severity,status,observed_value,message,first_seen_at,
                    last_seen_at,occurrences,metadata_json
                ) VALUES (:code,:severity,:status,CAST(:value AS jsonb),:message,now(),now(),1,
                          CAST(:metadata AS jsonb))
                ON CONFLICT (alert_code,status) WHERE resolved_at IS NULL
                DO UPDATE SET observed_value=EXCLUDED.observed_value,last_seen_at=now(),
                              occurrences=market.fii_pipeline_alerts.occurrences+1,
                              metadata_json=EXCLUDED.metadata_json
                RETURNING id
            """), {"code": code, "severity": _severity(status), "status": status,
                    "value": json.dumps(value, default=str), "message": message,
                    "metadata": json.dumps({"checked_at": now.isoformat()})}).scalar()
            alerts.append({"id": int(alert_id), "code": code, "status": status,
                           "value": value, "message": message})
        failed = sum(alert["status"] == "failed" for alert in alerts)
        conn.execute(text("""
            UPDATE market.fii_pipeline_alerts a SET resolved_at=now()
            WHERE resolved_at IS NULL AND status='failed'
              AND EXISTS (SELECT 1 FROM market.fii_pipeline_alerts p
                          WHERE p.alert_code=a.alert_code AND p.status='passed'
                            AND p.last_seen_at>=a.last_seen_at)
        """))
    return {"status": "passed" if failed == 0 else "warning", "failed": failed,
            "metrics": metrics, "alerts": alerts}

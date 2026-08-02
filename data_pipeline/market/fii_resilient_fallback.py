"""Fallback estruturado CVM para lotes documentais com host em circuito.

O fallback acrescenta somente observações regulatórias versionadas. Ele não
executa reprocessamento, snapshot, revisão humana ou promoção documental.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text


def _engine():
    from data_pipeline.utils.db_utils import get_pipeline_engine
    return get_pipeline_engine()


def active_document_host_circuits(*, cooldown_minutes: int = 60,
                                  engine=None) -> list[dict[str, Any]]:
    database = engine or _engine()
    if database is None:
        return []
    with database.connect() as conn:
        rows = conn.execute(text("""
            SELECT entity_id AS host,max(created_at) AS opened_at,count(*) AS events
              FROM market.fii_audit_events
             WHERE event_type='document_host_circuit_opened'
               AND entity_type='fii_document_host'
               AND created_at > now()-(:minutes * interval '1 minute')
             GROUP BY entity_id
             ORDER BY max(created_at) DESC,entity_id
        """), {"minutes": max(int(cooldown_minutes), 1)}).mappings().all()
    return [dict(row) for row in rows]


def structured_monthly_profile(*, engine=None) -> dict[str, Any]:
    """Perfil compacto da partição mensal mais recente já normalizada."""
    database = engine or _engine()
    if database is None:
        return {"available": False, "reason": "banco indisponível"}
    with database.connect() as conn:
        row = conn.execute(text("""
            WITH latest_reference AS (
              SELECT max(reference_date) AS reference_date
                FROM market.fii_metric_observations
               WHERE source='cvm_informe_mensal'
            ), latest AS (
              SELECT o.* FROM market.fii_metric_observations o
              JOIN latest_reference r USING (reference_date)
              WHERE o.source='cvm_informe_mensal'
            ), keys AS (
              SELECT ticker,metric_name,reference_date,available_at,vintage,source,
                     count(*) AS copies
                FROM latest
               GROUP BY ticker,metric_name,reference_date,available_at,vintage,source
            )
            SELECT r.reference_date,
                   count(l.*) AS observations,
                   count(DISTINCT l.ticker) AS tickers,
                   (SELECT count(*) FROM market.fiis WHERE cnpj IS NOT NULL
                      AND regexp_replace(cnpj,'\\D','','g')<>'') AS eligible_tickers,
                   count(*) FILTER (WHERE l.source_release_id IS NOT NULL)
                       AS release_linked,
                   count(*) FILTER (WHERE l.reference_date>l.knowledge_at::date)
                       AS future_reference_violations,
                   count(*) FILTER (WHERE l.value_numeric IS NULL
                                      AND l.value_text IS NULL
                                      AND l.value_json IS NULL)
                       AS empty_value_rows,
                   COALESCE((SELECT sum(copies-1) FROM keys WHERE copies>1),0)
                       AS duplicate_natural_keys,
                   max(l.knowledge_at) AS latest_knowledge_at
              FROM latest_reference r LEFT JOIN latest l ON true
             GROUP BY r.reference_date
        """)).mappings().one()
    profile = dict(row)
    observations = int(profile.get("observations") or 0)
    tickers = int(profile.get("tickers") or 0)
    eligible = int(profile.get("eligible_tickers") or 0)
    linked = int(profile.get("release_linked") or 0)
    profile.update({
        "available": bool(observations),
        "observations": observations,
        "tickers": tickers,
        "eligible_tickers": eligible,
        "ticker_coverage": tickers / eligible if eligible else 0.0,
        "release_coverage": linked / observations if observations else 0.0,
        "release_linked": linked,
        "future_reference_violations": int(
            profile.get("future_reference_violations") or 0
        ),
        "empty_value_rows": int(profile.get("empty_value_rows") or 0),
        "duplicate_natural_keys": int(profile.get("duplicate_natural_keys") or 0),
    })
    return profile


def run_resilient_fallback(
    *,
    force_structured: bool = False,
    process_documents: bool = True,
    structured_years: int = 1,
    cooldown_minutes: int = 60,
    document_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Executa o fallback mensal se houver circuito e depois o lote documental."""
    from data_pipeline.market.fii_cvm_structured import ingest_cvm_structured
    from data_pipeline.market.fii_documents import process_pending_documents

    database = _engine()
    if database is None:
        return {"status": "blocked", "blocker": "banco indisponível"}
    circuits_before = active_document_host_circuits(
        cooldown_minutes=cooldown_minutes, engine=database,
    )
    before = structured_monthly_profile(engine=database)
    fallback_triggered = bool(force_structured or circuits_before)
    if fallback_triggered:
        structured = ingest_cvm_structured(
            years=max(int(structured_years), 1),
            kinds=("monthly",),
            run_postprocess=False,
        )
    else:
        structured = {
            "status": "skipped",
            "reason": "nenhum host documental em circuito",
        }
    documents = (
        process_pending_documents(**(document_options or {}))
        if process_documents else {"status": "skipped"}
    )
    circuits_after = active_document_host_circuits(
        cooldown_minutes=cooldown_minutes, engine=database,
    )
    if not fallback_triggered and circuits_after:
        fallback_triggered = True
        structured = ingest_cvm_structured(
            years=max(int(structured_years), 1),
            kinds=("monthly",),
            run_postprocess=False,
        )
    after = structured_monthly_profile(engine=database)
    failures = int(documents.get("failed") or 0)
    attempted = int(documents.get("attempted") or 0)
    failure_rate = failures / attempted if attempted else 0.0
    structured_failed = str(structured.get("status")) in {"failed", "partial"}
    deadline_limited = bool(documents.get("batch_deadline_exhausted"))
    status = "completed"
    compensated_transient_failure = bool(
        failure_rate > .5
        and fallback_triggered
        and circuits_after
        and failures > 0
        and int(documents.get("transient_failed") or 0) == failures
        and not structured_failed
    )
    if structured_failed or (failure_rate > .5 and not compensated_transient_failure):
        status = "partial"
    elif compensated_transient_failure:
        status = "warning"
    elif deadline_limited:
        status = "warning"
    return {
        "status": status,
        "fallback_triggered": fallback_triggered,
        "circuits_before": circuits_before,
        "active_circuits": circuits_after,
        "structured": structured,
        "documents": documents,
        "quality_before": before,
        "quality_after": after,
        "failure_rate_attempted": failure_rate,
        "compensated_transient_failure": compensated_transient_failure,
        "deadline_limited": deadline_limited,
        "policy": {
            "source": "CVM Portal de Dados Abertos",
            "kinds": ["monthly"],
            "run_postprocess": False,
            "score_promotion": False,
            "snapshot_publication": False,
        },
    }

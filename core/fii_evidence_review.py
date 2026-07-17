"""Fila de revisão humana e promoção auditável de métricas documentais de FIIs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import uuid
from typing import Any

from sqlalchemy import text

from data_pipeline.market import repository as repo
from data_pipeline.market.fii_sources import metric_observation
from data_pipeline.utils.db_utils import get_pipeline_engine


VALID_DECISIONS = frozenset({"accepted", "corrected", "rejected"})


def _numeric_value(value: Any) -> float:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("valor corrigido deve ser numérico e finito")
    return number


def review_backlog_summary() -> dict[str, Any]:
    engine = get_pipeline_engine()
    if engine is None:
        return {"available": False, "pending": 0, "reason": "banco indisponível"}
    try:
        with engine.connect() as conn:
            exists = conn.execute(text(
                "SELECT to_regclass('market.fii_extraction_evidence') IS NOT NULL"
            )).scalar()
            if not exists:
                return {"available": False, "pending": 0, "reason": "schema documental ausente"}
            row = conn.execute(text("""
                SELECT count(*) FILTER (WHERE e.validation_status='pending') AS pending,
                       count(*) FILTER (WHERE e.validation_method='human') AS human_reviewed,
                       count(DISTINCT d.ticker) FILTER (
                           WHERE e.validation_status='pending') AS pending_tickers
                FROM market.fii_extraction_evidence e
                JOIN market.fii_extraction_runs r ON r.id=e.extraction_run_id
                JOIN market.fii_document_versions v ON v.id=r.document_version_id
                JOIN market.fii_documents d ON d.id=v.document_id
            """)).mappings().one()
        return {"available": True, **{key: int(value or 0) for key, value in row.items()}}
    except Exception as exc:
        return {"available": False, "pending": 0, "reason": str(exc)[:300]}


def load_pending_evidence(*, limit: int = 50, tickers: list[str] | None = None,
                          metrics: list[str] | None = None) -> list[dict[str, Any]]:
    """Carrega uma fila reproduzível, priorizando evidências fortes e recentes."""
    engine = get_pipeline_engine()
    if engine is None:
        return []
    normalized_tickers = sorted({str(value).upper().replace(".SA", "")
                                 for value in (tickers or []) if value})
    normalized_metrics = sorted({str(value) for value in (metrics or []) if value})
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT e.id,e.metric_name,e.raw_value,e.normalized_value,e.unit,
                   e.page_number,e.evidence_text,e.confidence,e.validation_status,
                   d.ticker,d.document_type,d.reference_date,d.source_published_at,
                   d.first_observed_at,d.source_url,v.content_sha256,
                   r.parser_name,r.parser_version,r.layout_signature,r.text_method
            FROM market.fii_extraction_evidence e
            JOIN market.fii_extraction_runs r ON r.id=e.extraction_run_id
            JOIN market.fii_document_versions v ON v.id=r.document_version_id
            JOIN market.fii_documents d ON d.id=v.document_id
            WHERE e.validation_status='pending'
              AND (:ticker_filter=false OR d.ticker=ANY(CAST(:tickers AS text[])))
              AND (:metric_filter=false OR e.metric_name=ANY(CAST(:metrics AS text[])))
            ORDER BY CASE WHEN e.confidence>=.90 THEN 0 ELSE 1 END,
                     d.reference_date DESC NULLS LAST,e.confidence DESC,e.id
            LIMIT :limit
        """), {
            "ticker_filter": bool(normalized_tickers), "tickers": normalized_tickers,
            "metric_filter": bool(normalized_metrics), "metrics": normalized_metrics,
            "limit": max(1, min(int(limit), 500)),
        }).mappings().all()
    return [dict(row) for row in rows]


def review_evidence(evidence_id: int, *, decision: str, reviewer_id: str,
                    corrected_value: float | None = None,
                    note: str | None = None) -> dict[str, Any]:
    """Persiste decisão humana e promove somente valores aceitos/corrigidos.

    ``knowledge_at`` da observação promovida é o instante da revisão. Assim uma
    validação feita hoje jamais reescreve o que o backtest poderia conhecer antes.
    """
    decision = str(decision).strip().lower()
    if decision not in VALID_DECISIONS:
        raise ValueError(f"decisão inválida: {decision}")
    reviewer = str(reviewer_id or "").strip()
    if len(reviewer) < 3:
        raise ValueError("informe um identificador de revisor com ao menos 3 caracteres")
    engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("banco indisponível")

    correlation_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    promoted_id: int | None = None
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT e.*,d.id AS document_id,d.ticker,d.reference_date,
                   d.source_published_at,d.first_observed_at,d.source_url,
                   v.id AS document_version_id,v.content_sha256,
                   r.parser_name,r.parser_version
            FROM market.fii_extraction_evidence e
            JOIN market.fii_extraction_runs r ON r.id=e.extraction_run_id
            JOIN market.fii_document_versions v ON v.id=r.document_version_id
            JOIN market.fii_documents d ON d.id=v.document_id
            WHERE e.id=:id FOR UPDATE OF e
        """), {"id": int(evidence_id)}).mappings().first()
        if not row:
            raise LookupError(f"evidência {evidence_id} não encontrada")
        if row["validation_status"] != "pending":
            raise ValueError(
                f"evidência {evidence_id} já foi revisada como {row['validation_status']}")
        current_value = row["normalized_value"]
        if decision == "corrected":
            if corrected_value is None:
                raise ValueError("a decisão corrected exige corrected_value")
            final_value = _numeric_value(corrected_value)
        else:
            final_value = _numeric_value(current_value) if decision == "accepted" else None
        review_payload = {
            "evidence_id": int(evidence_id), "decision": decision,
            "reviewer_id": reviewer, "previous_value": current_value,
            "final_value": final_value, "note": str(note or ""),
            "reviewed_at": now.isoformat(), "content_sha256": row["content_sha256"],
            "parser": f"{row['parser_name']}:{row['parser_version']}",
        }
        review_hash = hashlib.sha256(json.dumps(
            review_payload, ensure_ascii=False, sort_keys=True, default=str,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

        if decision in {"accepted", "corrected"}:
            reference = row["reference_date"] or now.date()
            observation = metric_observation(
                ticker=str(row["ticker"] or ""),
                metric_name=str(row["metric_name"]), value=final_value,
                reference_date=reference, available_at=now,
                source="public_fii_report_human_review_v1",
                vintage=f"human-review:{evidence_id}:{review_hash[:16]}",
                source_published_at=row["source_published_at"],
                availability_quality="first_observed_proxy",
                metadata={
                    "evidence_id": int(evidence_id),
                    "document_id": int(row["document_id"]),
                    "document_version_id": int(row["document_version_id"]),
                    "document_sha256": row["content_sha256"],
                    "parser_name": row["parser_name"],
                    "parser_version": row["parser_version"],
                    "reviewer_id": reviewer, "review_decision": decision,
                    "review_hash": review_hash,
                    "source_first_observed_at": str(row["first_observed_at"]),
                },
            )
            observation["source_url"] = str(row["source_url"] or "")
            observation["quality_status"] = "accepted"
            repo.upsert(conn, "fii_metric_observations", [observation])
            promoted_id = conn.execute(text("""
                SELECT id FROM market.fii_metric_observations
                WHERE ticker=:ticker AND metric_name=:metric
                  AND vintage=:vintage AND source=:source
                ORDER BY id DESC LIMIT 1
            """), {
                "ticker": str(row["ticker"] or ""), "metric": row["metric_name"],
                "vintage": observation["vintage"], "source": observation["source"],
            }).scalar()

        conn.execute(text("""
            UPDATE market.fii_extraction_evidence
            SET validation_status=:decision, validation_method='human',
                normalized_value=CASE WHEN :decision='corrected'
                    THEN CAST(:normalized AS jsonb) ELSE normalized_value END,
                reviewer_id=:reviewer,reviewed_at=:reviewed_at,review_note=:note,
                promoted_observation_id=:promoted,review_hash=:review_hash
            WHERE id=:id
        """), {
            "decision": decision, "normalized": json.dumps(final_value),
            "reviewer": reviewer, "reviewed_at": now, "note": str(note or "")[:2000],
            "promoted": promoted_id, "review_hash": review_hash, "id": int(evidence_id),
        })
        conn.execute(text("""
            INSERT INTO market.fii_audit_events (
                event_type,entity_type,entity_id,actor_type,actor_id,correlation_id,
                parser_version,payload_json
            ) VALUES ('evidence_human_reviewed','fii_extraction_evidence',:entity,
                      'user',:actor,:correlation,:parser,CAST(:payload AS jsonb))
        """), {
            "entity": str(evidence_id), "actor": reviewer,
            "correlation": correlation_id, "parser": row["parser_version"],
            "payload": json.dumps(review_payload, ensure_ascii=False, default=str),
        })

    from data_pipeline.market.fii_confidence_pipeline import calibrate_parsers
    calibration = calibrate_parsers()
    return {
        "evidence_id": int(evidence_id), "decision": decision,
        "promoted_observation_id": int(promoted_id) if promoted_id else None,
        "review_hash": review_hash, "calibration": calibration,
    }

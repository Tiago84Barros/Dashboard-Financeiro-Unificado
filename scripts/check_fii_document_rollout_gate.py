"""Avalia se o parser documental FII pode avançar para um lote maior."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def evaluate_rollout(
    metrics: dict[str, Any], *, min_attempts: int = 50,
    min_success_rate: float = .90, max_ocr_required_rate: float = .25,
) -> dict[str, Any]:
    attempted = max(int(metrics.get("attempted") or 0), 0)
    extracted = max(int(metrics.get("extracted") or 0), 0)
    ocr_required = max(int(metrics.get("ocr_required") or 0), 0)
    success_rate = extracted / attempted if attempted else 0.0
    ocr_required_rate = ocr_required / extracted if extracted else 0.0
    blockers: list[str] = []
    if attempted < max(int(min_attempts), 1):
        blockers.append("minimum_attempts")
    if success_rate < float(min_success_rate):
        blockers.append("success_rate")
    if ocr_required_rate > float(max_ocr_required_rate):
        blockers.append("ocr_required_rate")
    zero_gates = {
        "processing": "processing_claims",
        "provisional": "provisional_promotions",
        "document_pit_violations": "document_pit_violations",
        "document_duplicates": "document_duplicates",
        "document_empty_evidence": "document_empty_evidence",
        "unverified_sources": "unverified_sources",
        "cvm_pit_violations": "cvm_pit_violations",
        "cvm_duplicates": "cvm_duplicates",
        "cvm_empty_values": "cvm_empty_values",
    }
    for metric, blocker in zero_gates.items():
        if int(metrics.get(metric) or 0) != 0:
            blockers.append(blocker)
    return {
        "allowed": not blockers,
        "blockers": blockers,
        "success_rate": round(success_rate, 6),
        "ocr_required_rate": round(ocr_required_rate, 6),
        "metrics": metrics,
        "thresholds": {
            "min_attempts": max(int(min_attempts), 1),
            "min_success_rate": float(min_success_rate),
            "max_ocr_required_rate": float(max_ocr_required_rate),
        },
    }


def collect_metrics(engine, *, parser_version: str) -> dict[str, Any]:
    from data_pipeline.market.fii_resilient_fallback import structured_monthly_profile

    with engine.connect() as conn:
        parser = conn.execute(text("""
            SELECT count(*) AS extracted,
                   count(*) FILTER (
                       WHERE COALESCE(
                           metrics_json->>'usability_status',
                           CASE WHEN confidence<.15 THEN 'ocr_required' END
                       )='ocr_required'
                   ) AS ocr_required,
                   count(*) FILTER (
                       WHERE metrics_json->>'usability_status'
                           ='readable_no_target_facts'
                   ) AS readable_no_target_facts,
                   count(*) FILTER (
                       WHERE metrics_json->>'usability_status'
                           ='partial_ocr_review'
                   ) AS partial_ocr_review,
                   count(*) FILTER (
                       WHERE COALESCE(metrics_json->>'source_class','')
                           ='public_document_unverified'
                   ) AS unverified_sources
              FROM market.fii_extraction_runs
             WHERE parser_name='fii_public_report' AND parser_version=:parser
        """), {"parser": parser_version}).mappings().one()
        failures = int(conn.execute(text("""
            SELECT count(*) FROM market.fii_audit_events
             WHERE parser_version=:parser
               AND event_type IN ('document_download_failed','document_parser_timed_out')
        """), {"parser": parser_version}).scalar() or 0)
        controls = conn.execute(text("""
            SELECT
              (SELECT count(*) FROM market.fii_documents
                WHERE processing_status='processing') AS processing,
              (SELECT count(*) FROM market.fii_extraction_evidence evidence
                JOIN market.fii_extraction_runs run
                  ON run.id=evidence.extraction_run_id
               WHERE run.parser_version=:parser
                 AND evidence.validation_status='provisional') AS provisional,
              (SELECT count(*) FROM market.fii_project_observations
               WHERE knowledge_at<source_published_at) AS document_pit_violations,
              (SELECT count(*) FROM (
                  SELECT project_id,reference_date,document_version_id
                    FROM market.fii_project_observations
                   GROUP BY 1,2,3 HAVING count(*)>1
               ) duplicates) AS document_duplicates,
              (SELECT count(*) FROM market.fii_project_observations
               WHERE evidence_text IS NULL OR btrim(evidence_text)='')
                 +
              (SELECT count(*) FROM market.fii_extraction_evidence evidence
                JOIN market.fii_extraction_runs run
                  ON run.id=evidence.extraction_run_id
               WHERE run.parser_version=:parser
                 AND (evidence.evidence_text IS NULL
                      OR btrim(evidence.evidence_text)='')) AS document_empty_evidence
        """), {"parser": parser_version}).mappings().one()
    cvm = structured_monthly_profile(engine=engine)
    extracted = int(parser["extracted"] or 0)
    return {
        "parser_version": parser_version,
        "attempted": extracted + failures,
        "extracted": extracted,
        "failed": failures,
        "ocr_required": int(parser["ocr_required"] or 0),
        "readable_no_target_facts": int(
            parser["readable_no_target_facts"] or 0
        ),
        "partial_ocr_review": int(parser["partial_ocr_review"] or 0),
        "unverified_sources": int(parser["unverified_sources"] or 0),
        **{key: int(value or 0) for key, value in dict(controls).items()},
        "cvm_pit_violations": int(cvm.get("future_reference_violations") or 0),
        "cvm_duplicates": int(cvm.get("duplicate_natural_keys") or 0),
        "cvm_empty_values": int(cvm.get("empty_value_rows") or 0),
        "cvm_ticker_coverage": float(cvm.get("ticker_coverage") or 0.0),
        "cvm_release_coverage": float(cvm.get("release_coverage") or 0.0),
    }


def main() -> int:
    from scripts.backfill_fii_documents_local import _configure_database

    _configure_database()
    from data_pipeline.market.fii_documents import PARSER_VERSION
    from data_pipeline.utils.db_utils import get_pipeline_engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parser-version", default=PARSER_VERSION)
    parser.add_argument("--min-attempts", type=int, default=50)
    parser.add_argument("--min-success-rate", type=float, default=.90)
    parser.add_argument("--max-ocr-required-rate", type=float, default=.25)
    parser.add_argument(
        "--max-low-confidence-rate", type=float, default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    engine = get_pipeline_engine()
    if engine is None:
        print(json.dumps({"allowed": False, "blockers": ["database"]}))
        return 2
    report = evaluate_rollout(
        collect_metrics(engine, parser_version=args.parser_version),
        min_attempts=args.min_attempts,
        min_success_rate=args.min_success_rate,
        max_ocr_required_rate=(
            args.max_low_confidence_rate
            if args.max_low_confidence_rate is not None
            else args.max_ocr_required_rate
        ),
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["allowed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

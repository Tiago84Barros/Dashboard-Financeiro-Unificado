"""Reconciliação conservadora de observações por empreendimento FII."""
from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import text

from data_pipeline.utils.db_utils import get_pipeline_engine

PROJECT_TOLERANCES: dict[str, tuple[float, float]] = {
    # campo: (tolerância absoluta, tolerância relativa)
    "portfolio_weight": (.01, .10),
    "construction_progress": (.03, .10),
    "sales_progress": (.03, .10),
    "expected_irr": (.02, .10),
    "expected_result_brl": (1_000_000.0, .05),
    "vgv_brl": (1_000_000.0, .05),
    "sellable_area_sqm": (100.0, .02),
    "unit_count": (0.0, 0.0),
}


def values_conflict(
    left: Any, right: Any, *, absolute_tolerance: float,
    relative_tolerance: float,
) -> tuple[bool, float | None, float | None]:
    if left is None or right is None:
        return False, None, None
    try:
        left_value = float(left)
        right_value = float(right)
    except (TypeError, ValueError):
        return left != right, None, None
    if not math.isfinite(left_value) or not math.isfinite(right_value):
        return True, None, None
    absolute = abs(left_value - right_value)
    denominator = max(abs(left_value), abs(right_value), 1e-12)
    relative = absolute / denominator
    conflict = absolute > absolute_tolerance and relative > relative_tolerance
    return conflict, absolute, relative


def detect_project_conflicts(*, tickers: list[str] | None = None) -> dict[str, int]:
    """Compara as duas fontes mais recentes para a mesma data de referência.

    Concordância não promove dados automaticamente: duas versões podem vir do
    mesmo gestor. Divergências materiais são marcadas para revisão humana.
    """
    engine = get_pipeline_engine()
    if engine is None:
        return {"compared": 0, "conflicts": 0}
    normalized_tickers = sorted({
        str(value).upper().replace(".SA", "") for value in (tickers or []) if value
    })
    with engine.begin() as conn:
        if not conn.execute(text(
            "SELECT to_regclass('market.fii_project_observations') IS NOT NULL"
        )).scalar():
            return {"compared": 0, "conflicts": 0}
        resolved_stale = conn.execute(text("""
            WITH stale AS (
                SELECT issue.id
                  FROM market.fii_reconciliation_issues issue
                  JOIN market.fii_project_observations left_observation
                    ON left_observation.id=split_part(issue.left_source,':',2)::bigint
                  JOIN market.fii_document_versions left_version
                    ON left_version.id=left_observation.document_version_id
                  JOIN market.fii_documents left_document
                    ON left_document.id=left_version.document_id
                  JOIN market.fii_project_observations right_observation
                    ON right_observation.id=split_part(issue.right_source,':',2)::bigint
                  JOIN market.fii_document_versions right_version
                    ON right_version.id=right_observation.document_version_id
                  JOIN market.fii_documents right_document
                    ON right_document.id=right_version.document_id
                 WHERE issue.metric_name LIKE 'project.%'
                   AND issue.status='open'
                   AND issue.left_source ~ '^document:[0-9]+$'
                   AND issue.right_source ~ '^document:[0-9]+$'
                   AND (
                       left_observation.reference_date
                           IS DISTINCT FROM left_document.reference_date
                       OR right_observation.reference_date
                           IS DISTINCT FROM right_document.reference_date
                   )
            )
            UPDATE market.fii_reconciliation_issues issue
               SET status='resolved',resolved_at=now(),
                   resolution_json=COALESCE(issue.resolution_json,'{}'::jsonb)
                     || jsonb_build_object(
                         'resolution','invalid_temporal_pairing',
                         'resolved_by','document_reference_gate_v1'
                     )
              FROM stale
             WHERE issue.id=stale.id
        """))
        resolved_stale_count = max(int(resolved_stale.rowcount or 0), 0)
        rows = conn.execute(text("""
            WITH ranked AS (
                SELECT o.*,p.ticker,p.project_key,
                       d.source_url,
                       row_number() OVER (
                           PARTITION BY o.project_id,o.reference_date
                           ORDER BY o.knowledge_at DESC,o.id DESC
                       ) AS position
                FROM market.fii_project_observations o
                JOIN market.fii_projects p ON p.id=o.project_id
                JOIN market.fii_document_versions v ON v.id=o.document_version_id
                JOIN market.fii_documents d ON d.id=v.document_id
                WHERE o.validation_status<>'rejected'
                  AND d.reference_date IS NOT NULL
                  AND o.reference_date=d.reference_date
                  AND (:ticker_filter=false OR p.ticker=ANY(CAST(:tickers AS text[])))
            )
            SELECT newest.id AS newest_id,previous.id AS previous_id,
                   newest.ticker,newest.project_key,newest.reference_date,
                   newest.source_url AS newest_source,
                   previous.source_url AS previous_source,
                   newest.portfolio_weight AS newest_portfolio_weight,
                   previous.portfolio_weight AS previous_portfolio_weight,
                   newest.construction_progress AS newest_construction_progress,
                   previous.construction_progress AS previous_construction_progress,
                   newest.sales_progress AS newest_sales_progress,
                   previous.sales_progress AS previous_sales_progress,
                   newest.expected_irr AS newest_expected_irr,
                   previous.expected_irr AS previous_expected_irr,
                   newest.expected_result_brl AS newest_expected_result_brl,
                   previous.expected_result_brl AS previous_expected_result_brl,
                   newest.vgv_brl AS newest_vgv_brl,
                   previous.vgv_brl AS previous_vgv_brl,
                   newest.sellable_area_sqm AS newest_sellable_area_sqm,
                   previous.sellable_area_sqm AS previous_sellable_area_sqm,
                   newest.unit_count AS newest_unit_count,
                   previous.unit_count AS previous_unit_count
            FROM ranked newest
            JOIN ranked previous
              ON previous.project_id=newest.project_id
             AND previous.reference_date=newest.reference_date
             AND previous.position=2
            WHERE newest.position=1
              AND newest.document_version_id<>previous.document_version_id
        """), {
            "ticker_filter": bool(normalized_tickers), "tickers": normalized_tickers,
        }).mappings().all()
        conflicts = 0
        for row in rows:
            row_conflicts = 0
            for field, (absolute_tolerance, relative_tolerance) in PROJECT_TOLERANCES.items():
                left = row[f"previous_{field}"]
                right = row[f"newest_{field}"]
                is_conflict, absolute, relative = values_conflict(
                    left, right,
                    absolute_tolerance=absolute_tolerance,
                    relative_tolerance=relative_tolerance,
                )
                if not is_conflict:
                    continue
                left_source = f"document:{row['previous_id']}"
                right_source = f"document:{row['newest_id']}"
                conn.execute(text("""
                    INSERT INTO market.fii_reconciliation_issues (
                        ticker,metric_name,reference_date,left_source,left_value,
                        right_source,right_value,absolute_difference,
                        relative_difference,tolerance,status,resolution_json
                    ) VALUES (
                        :ticker,:metric,:reference,:left_source,CAST(:left_value AS jsonb),
                        :right_source,CAST(:right_value AS jsonb),:absolute,
                        :relative,:tolerance,'open',CAST(:resolution AS jsonb)
                    )
                    ON CONFLICT (
                        ticker,metric_name,reference_date,left_source,right_source
                    ) DO UPDATE SET
                        left_value=EXCLUDED.left_value,right_value=EXCLUDED.right_value,
                        absolute_difference=EXCLUDED.absolute_difference,
                        relative_difference=EXCLUDED.relative_difference,
                        tolerance=EXCLUDED.tolerance,status='open',
                        resolution_json=EXCLUDED.resolution_json,
                        detected_at=now(),resolved_at=NULL
                """), {
                    "ticker": row["ticker"],
                    "metric": f"project.{row['project_key']}.{field}",
                    "reference": row["reference_date"],
                    "left_source": left_source, "right_source": right_source,
                    "left_value": json.dumps(left, default=str),
                    "right_value": json.dumps(right, default=str),
                    "absolute": absolute, "relative": relative,
                    "tolerance": max(absolute_tolerance, relative_tolerance),
                    "resolution": json.dumps({
                        "previous_url": row["previous_source"],
                        "newest_url": row["newest_source"],
                        "absolute_tolerance": absolute_tolerance,
                        "relative_tolerance": relative_tolerance,
                    }, ensure_ascii=False),
                })
                row_conflicts += 1
            if row_conflicts:
                conn.execute(text("""
                    UPDATE market.fii_project_observations
                    SET validation_status='conflicting'
                    WHERE id=ANY(CAST(:ids AS bigint[]))
                      AND validation_status='pending'
                """), {"ids": [int(row["newest_id"]), int(row["previous_id"])]})
                conflicts += row_conflicts
        return {
            "compared": len(rows), "conflicts": conflicts,
            "resolved_stale": resolved_stale_count,
        }

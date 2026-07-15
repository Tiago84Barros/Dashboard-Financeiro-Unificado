"""Orquestração incremental das fontes públicas de FIIs.

Arquivos estruturados e históricos são processados por hash/parser. PDFs ficam
limitados a uma janela recente, a um universo priorizado e a um orçamento de
armazenamento. Evidências extraídas nunca entram automaticamente no score.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text


def _engine():
    from data_pipeline.utils.db_utils import get_pipeline_engine
    return get_pipeline_engine()


def _current_candidates(limit: int) -> list[str]:
    engine = _engine()
    if engine is None:
        return []
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT ticker
            FROM market.fiis
            WHERE ticker IS NOT NULL
              AND score_version IS NOT NULL
              AND price > 0
              AND COALESCE(liquidez_diaria, 0) > 0
            ORDER BY score DESC NULLS LAST, liquidez_diaria DESC NULLS LAST,
                     ticker
            LIMIT :limit
        """), {"limit": max(int(limit), 1)}).scalars().all()
    return [str(value).upper().replace(".SA", "") for value in rows]


def run_enrichment(*, years: int = 5, candidate_limit: int = 12,
                   document_limit: int = 150, recent_months: int = 24,
                   document_budget_bytes: int = 250 * 1024 * 1024,
                   max_document_bytes: int = 30 * 1024 * 1024,
                   min_free_bytes: int = 10 * 1024 * 1024 * 1024,
                   tickers: list[str] | None = None) -> dict[str, Any]:
    """Executa a esteira pública em lotes retomáveis e devolve cada etapa."""
    from data_pipeline.market.fii_b3_history import ingest_b3_history
    from data_pipeline.market.fii_confidence_pipeline import calibrate_parsers
    from data_pipeline.market.fii_cvm_cri import ingest_cvm_cri
    from data_pipeline.market.fii_cvm_structured import ingest_cvm_structured
    from data_pipeline.market.fii_documents import process_pending_documents
    from data_pipeline.market.fii_ingest import (
        audit_methodology_v4_data, reprocess, snapshot_methodology_v4,
    )
    from data_pipeline.market.fii_monitoring import run_monitoring

    selected = sorted({str(value).upper().replace(".SA", "")
                       for value in (tickers or []) if value})
    if not selected:
        selected = _current_candidates(candidate_limit)
    stages: dict[str, Any] = {}

    def run_stage(name: str, function, **kwargs) -> None:
        try:
            stages[name] = function(**kwargs)
        except Exception as exc:  # mantém diagnóstico das etapas seguintes
            stages[name] = {"status": "failed", "errors": [str(exc)[:1000]]}

    run_stage("b3_history", ingest_b3_history, years=max(int(years), 1))
    run_stage("cvm_structured", ingest_cvm_structured, years=max(int(years), 1))
    run_stage("cvm_cri", ingest_cvm_cri, years=max(int(years), 1))
    run_stage(
        "documents", process_pending_documents,
        limit=max(int(document_limit), 1), tickers=selected,
        recent_months=max(int(recent_months), 0),
        max_batch_bytes=max(int(document_budget_bytes), 1),
        max_document_bytes=max(int(max_document_bytes), 1),
        min_free_bytes=max(int(min_free_bytes), 0),
    )
    run_stage("reprocess", reprocess)
    run_stage("score_snapshot", snapshot_methodology_v4)
    run_stage("audit", audit_methodology_v4_data)
    run_stage("confidence_calibration", calibrate_parsers)
    run_stage("monitoring", run_monitoring)

    failed = [name for name, report in stages.items()
              if str((report or {}).get("status", "completed"))
              in {"failed", "partial", "blocked"}]
    return {
        "status": "partial" if failed else "completed",
        "as_of": date.today().isoformat(), "candidates": selected,
        "stages": stages, "failed_stages": failed,
        "policy": {"documents_are_evidence_only": True,
                    "point_in_time_required": True,
                    "storage_budget_bytes": int(document_budget_bytes),
                    "minimum_free_bytes": int(min_free_bytes)},
    }

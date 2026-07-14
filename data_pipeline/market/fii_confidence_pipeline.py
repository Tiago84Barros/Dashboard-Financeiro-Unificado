"""Calibra parsers a partir das decisões da fila de revisão humana."""
from __future__ import annotations

from sqlalchemy import text

from core.fii_confidence import beta_posterior
from data_pipeline.utils.db_utils import get_pipeline_engine


def calibrate_parsers() -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "error": "banco indisponível"}
    with engine.begin() as conn:
        groups = [dict(row._mapping) for row in conn.execute(text("""
            SELECT r.parser_name, r.parser_version, e.metric_name,
                   count(*) FILTER (WHERE e.validation_status='accepted') AS accepted,
                   count(*) FILTER (WHERE e.validation_status='corrected') AS corrected,
                   count(*) FILTER (WHERE e.validation_status='rejected') AS rejected
            FROM market.fii_extraction_evidence e
            JOIN market.fii_extraction_runs r ON r.id=e.extraction_run_id
            WHERE e.validation_status IN ('accepted','corrected','rejected')
            GROUP BY 1,2,3
        """))]
        for row in groups:
            posterior = beta_posterior(row["accepted"], row["corrected"], row["rejected"])
            conn.execute(text("""
                INSERT INTO market.fii_parser_calibrations (
                    parser_name,parser_version,metric_name,reviewed_count,accepted_count,
                    corrected_count,rejected_count,posterior_mean,lower_bound,upper_bound,
                    calibrated_at
                ) VALUES (:parser_name,:parser_version,:metric_name,:reviewed,:accepted,
                          :corrected,:rejected,:mean,:lower,:upper,now())
                ON CONFLICT (parser_name,parser_version,metric_name)
                DO UPDATE SET reviewed_count=EXCLUDED.reviewed_count,
                              accepted_count=EXCLUDED.accepted_count,
                              corrected_count=EXCLUDED.corrected_count,
                              rejected_count=EXCLUDED.rejected_count,
                              posterior_mean=EXCLUDED.posterior_mean,
                              lower_bound=EXCLUDED.lower_bound,
                              upper_bound=EXCLUDED.upper_bound,
                              calibrated_at=now()
            """), {**row, "reviewed": posterior["reviewed"],
                    "mean": posterior["posterior_mean"],
                    "lower": posterior["lower_bound"], "upper": posterior["upper_bound"]})
    return {"status": "completed", "calibrations": len(groups),
            "reviewed": sum(int(row["accepted"] + row["corrected"] + row["rejected"])
                            for row in groups)}

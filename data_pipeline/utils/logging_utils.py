"""
data_pipeline/utils/logging_utils.py
Grava e consulta logs de execução nas tabelas administrativas do pipeline.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def log_start(
    table_name: str,
    source_name: str,
    job_name: str | None = None,
) -> int | None:
    """
    Registra o início de uma execução em data_update_logs (status='running').
    Retorna o ID do log, ou None se o banco não estiver disponível.
    """
    from data_pipeline.utils.db_utils import get_pipeline_engine, table_exists
    engine = get_pipeline_engine()
    if engine is None or not table_exists("data_update_logs"):
        return None
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""
                INSERT INTO data_update_logs
                    (table_name, source_name, job_name, started_at, status)
                VALUES
                    (:tn, :sn, :jn, :sa, 'running')
                RETURNING id
            """), {"tn": table_name, "sn": source_name, "jn": job_name, "sa": _now()})
            return row.scalar()
    except Exception as exc:
        logger.warning("log_start falhou: %s", exc)
        return None


def log_finish(
    log_id: int | None,
    status: str,
    records_inserted: int = 0,
    records_updated: int = 0,
    records_failed: int = 0,
    error_message: str | None = None,
    started_at: datetime | None = None,
) -> None:
    """Atualiza o registro de log com o resultado final."""
    if log_id is None:
        return
    from data_pipeline.utils.db_utils import get_pipeline_engine
    engine = get_pipeline_engine()
    if engine is None:
        return
    finished = _now()
    exec_secs: float | None = None
    if started_at:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        exec_secs = (finished - started_at).total_seconds()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE data_update_logs
                SET finished_at             = :fa,
                    status                  = :st,
                    records_inserted        = :ri,
                    records_updated         = :ru,
                    records_failed          = :rf,
                    error_message           = :em,
                    execution_time_seconds  = :es
                WHERE id = :id
            """), {
                "fa": finished, "st": status,
                "ri": records_inserted, "ru": records_updated, "rf": records_failed,
                "em": error_message, "es": exec_secs, "id": log_id,
            })
    except Exception as exc:
        logger.warning("log_finish falhou: %s", exc)


def update_freshness(
    table_name: str,
    source_name: str,
    job_name: str | None,
    status: str,
    records_inserted: int = 0,
    records_updated: int = 0,
    records_failed: int = 0,
    error_message: str | None = None,
    frequency: str = "diario",
) -> None:
    """Atualiza (UPSERT) data_freshness_status após cada execução."""
    from data_pipeline.utils.db_utils import get_pipeline_engine, table_exists
    from data_pipeline.utils.date_utils import freshness_label, next_expected_update

    engine = get_pipeline_engine()
    if engine is None or not table_exists("data_freshness_status"):
        return

    now = _now()
    last_success = now if status == "success" else None
    fresh_label = freshness_label(now if status == "success" else None, frequency)
    next_upd = next_expected_update(now if status == "success" else None, frequency)

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO data_freshness_status
                    (table_name, source_name, job_name,
                     last_success_at, last_attempt_at, last_status,
                     next_expected_update, freshness_status,
                     last_records_inserted, last_records_updated, last_records_failed,
                     last_error_message, updated_at)
                VALUES
                    (:tn, :sn, :jn,
                     :lsa, :la, :ls,
                     :neu, :fs,
                     :ri, :ru, :rf,
                     :em, :now)
                ON CONFLICT (table_name) DO UPDATE SET
                    source_name           = EXCLUDED.source_name,
                    job_name              = EXCLUDED.job_name,
                    last_success_at       = COALESCE(EXCLUDED.last_success_at, data_freshness_status.last_success_at),
                    last_attempt_at       = EXCLUDED.last_attempt_at,
                    last_status           = EXCLUDED.last_status,
                    next_expected_update  = EXCLUDED.next_expected_update,
                    freshness_status      = EXCLUDED.freshness_status,
                    last_records_inserted = EXCLUDED.last_records_inserted,
                    last_records_updated  = EXCLUDED.last_records_updated,
                    last_records_failed   = EXCLUDED.last_records_failed,
                    last_error_message    = EXCLUDED.last_error_message,
                    updated_at            = EXCLUDED.updated_at
            """), {
                "tn": table_name, "sn": source_name, "jn": job_name,
                "lsa": last_success, "la": now, "ls": status,
                "neu": next_upd, "fs": fresh_label,
                "ri": records_inserted, "ru": records_updated, "rf": records_failed,
                "em": error_message, "now": now,
            })
    except Exception as exc:
        logger.warning("update_freshness falhou: %s", exc)

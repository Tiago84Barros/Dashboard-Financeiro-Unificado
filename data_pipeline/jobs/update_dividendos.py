"""
data_pipeline/jobs/update_dividendos.py
Stub — implementação pendente.

Quando implementado: baixará dividendos e proventos de ativos B3 via B3/CVM
e fará UPSERT em public.proventos.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TABLE_NAME  = "proventos"
SOURCE_NAME = "B3 / CVM"
JOB_NAME    = "update_dividendos"


def run() -> dict:
    logger.info("update_dividendos: não implementado — pulando")
    return {
        "status":           "skipped",
        "table_name":       TABLE_NAME,
        "source_name":      SOURCE_NAME,
        "job_name":         JOB_NAME,
        "records_inserted": 0,
        "records_updated":  0,
        "records_failed":   0,
        "error_message":    "Implementação pendente",
    }

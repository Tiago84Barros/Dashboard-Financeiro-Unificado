"""
data_pipeline/jobs/update_empresas_eua.py
Stub — implementação pendente.

Quando implementado: baixará demonstrações financeiras de empresas EUA via
SEC EDGAR / Yahoo Finance / FMP e fará UPSERT em demonstracoes_financeiras_eua.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TABLE_NAME  = "demonstracoes_financeiras_eua"
SOURCE_NAME = "SEC / Yahoo / FMP"
JOB_NAME    = "update_empresas_eua"


def run() -> dict:
    logger.info("update_empresas_eua: não implementado — pulando")
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

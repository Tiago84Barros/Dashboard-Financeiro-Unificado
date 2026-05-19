"""
data_pipeline/update_registry.py
Gerencia o registry de fontes/tabelas a atualizar.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Registry padrão — semeado na primeira execução se a tabela estiver vazia
_DEFAULT_REGISTRY: list[dict] = [
    {
        "table_name":   "asset_quotes",
        "source_name":  "B3/Yahoo Finance",
        "job_name":     "update_b3_quotes",
        "update_type":  "incremental",
        "frequency":    "diario",
        "priority":     1,
        "is_active":    True,
        "description":  "Cotações históricas de ativos B3 e internacionais via yfinance",
    },
    {
        "table_name":   "macro",
        "source_name":  "Banco Central do Brasil (BCB/SGS)",
        "job_name":     "update_bcb",
        "update_type":  "incremental",
        "frequency":    "diario",
        "priority":     2,
        "is_active":    True,
        "description":  "Selic, IPCA, câmbio, PIB, balança comercial — API SGS do BCB",
    },
    {
        "table_name":   "macro",
        "source_name":  "App1 / Supabase B3",
        "job_name":     "update_macro",
        "update_type":  "incremental",
        "frequency":    "mensal",
        "priority":     3,
        "is_active":    True,
        "description":  "Indicadores macro consolidados importados do App 1",
    },
    {
        "table_name":   "docs_corporativos_chunks",
        "source_name":  "CVM / IPE",
        "job_name":     "update_cvm",
        "update_type":  "incremental",
        "frequency":    "semanal",
        "priority":     4,
        "is_active":    True,
        "description":  "Documentos corporativos CVM (fatos relevantes, resultados, atas)",
    },
    {
        "table_name":   "proventos",
        "source_name":  "B3 / CVM",
        "job_name":     "update_dividendos",
        "update_type":  "incremental",
        "frequency":    "semanal",
        "priority":     5,
        "is_active":    False,
        "description":  "Dividendos e proventos de ativos B3 (implementação pendente)",
    },
    {
        "table_name":   "demonstracoes_financeiras_eua",
        "source_name":  "SEC / Yahoo / FMP",
        "job_name":     "update_empresas_eua",
        "update_type":  "incremental",
        "frequency":    "trimestral",
        "priority":     6,
        "is_active":    False,
        "description":  "Demonstrações financeiras de empresas EUA (implementação pendente)",
    },
]


def get_registry(active_only: bool = False) -> list[dict]:
    """Retorna todos os registros de data_update_registry."""
    from data_pipeline.utils.db_utils import get_pipeline_engine, table_exists
    engine = get_pipeline_engine()
    if engine is None or not table_exists("data_update_registry"):
        return []
    try:
        where = "WHERE r.is_active = TRUE" if active_only else ""
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT
                    r.id, r.table_name, r.source_name, r.job_name,
                    r.update_type, r.frequency, r.priority, r.is_active, r.description,
                    f.last_success_at, f.last_attempt_at, f.last_status,
                    f.next_expected_update, f.freshness_status,
                    f.last_records_inserted, f.last_records_updated, f.last_records_failed,
                    f.last_error_message
                FROM data_update_registry r
                LEFT JOIN data_freshness_status f ON f.job_name = r.job_name
                {where}
                ORDER BY r.priority ASC, r.source_name ASC
            """)).fetchall()
            return [dict(r._mapping) for r in rows]
    except Exception as exc:
        logger.warning("get_registry falhou: %s", exc)
        return []


def seed_registry() -> int:
    """
    Insere os registros padrão se o registry estiver vazio.
    Retorna a quantidade de registros inseridos.
    """
    from data_pipeline.utils.db_utils import get_pipeline_engine, table_exists
    engine = get_pipeline_engine()
    if engine is None or not table_exists("data_update_registry"):
        return 0
    try:
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM data_update_registry")
            ).scalar() or 0
        if count > 0:
            return 0
        inserted = 0
        with engine.begin() as conn:
            for item in _DEFAULT_REGISTRY:
                conn.execute(text("""
                    INSERT INTO data_update_registry
                        (table_name, source_name, job_name, update_type,
                         frequency, priority, is_active, description)
                    VALUES
                        (:table_name, :source_name, :job_name, :update_type,
                         :frequency, :priority, :is_active, :description)
                    ON CONFLICT DO NOTHING
                """), item)
                inserted += 1
        return inserted
    except Exception as exc:
        logger.warning("seed_registry falhou: %s", exc)
        return 0

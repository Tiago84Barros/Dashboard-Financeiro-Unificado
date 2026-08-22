"""
data_pipeline/update_registry.py
Gerencia o registry de fontes/tabelas a atualizar.
"""
from __future__ import annotations

import logging

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
        "table_name":   "asset_quotes",
        "source_name":  "FX USD/BRL",
        "job_name":     "update_fx_rates",
        "update_type":  "incremental",
        "frequency":    "diario",
        "priority":     1,
        "is_active":    True,
        "description":  "Cotação diária USD/BRL (USDBRL=X via yfinance, fallback awesomeapi). Usada para converter posições USD em BRL na Carteira.",
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
        # LEGADO (app1): sincronizava public.macro do banco do App1. Desativado —
        # a tabela `macro` já é alimentada diretamente pela fonte primária via
        # update_bcb (Banco Central / SGS). App1 descontinuado.
        "table_name":   "macro",
        "source_name":  "Macro / Histórico B3 (legado app1)",
        "job_name":     "update_macro",
        "update_type":  "incremental",
        "frequency":    "manual",
        "priority":     3,
        "is_active":    False,
        "description":  "[Legado] Sync do App1; substituído por update_bcb (fonte primária BCB/SGS).",
    },
    {
        # LEGADO (app1): copiava docs CVM do banco do App1. Desativado —
        # substituído pelo coletor nativo update_cvm_ipe (CVM Dados Abertos).
        "table_name":   "docs_corporativos_chunks",
        "source_name":  "CVM / IPE (legado app1)",
        "job_name":     "update_cvm",
        "update_type":  "incremental",
        "frequency":    "manual",
        "priority":     4,
        "is_active":    False,
        "description":  "[Legado] Sync do App1; substituído pelo coletor nativo update_cvm_ipe.",
    },
    {
        "table_name":   "docs_corporativos, docs_corporativos_chunks",
        "source_name":  "CVM Dados Abertos (IPE)",
        "job_name":     "update_cvm_ipe",
        "update_type":  "incremental",
        "frequency":    "semanal",
        "priority":     4,
        "is_active":    False,
        "description":  "[Movido para staging LOCAL] Coletor nativo do dataset IPE da CVM. "
                        "A ingestão de documentos passou a rodar num Postgres local (sem limite) "
                        "e só o subconjunto curado é publicado no Supabase por "
                        "scripts/sync_docs_to_supabase.py — evita inflar o banco na nuvem. "
                        "Ver local_staging/README.md.",
    },
    {
        "table_name":   "docs_corporativos, docs_corporativos_chunks",
        "source_name":  "CVM ENET (texto completo)",
        "job_name":     "update_cvm_fulltext",
        "update_type":  "incremental",
        "frequency":    "diario",
        "priority":     5,
        "is_active":    False,
        "description":  "[Movido para staging LOCAL] Extração de texto completo dos documentos "
                        "CVM. Roda localmente sobre o corpus completo; só o curado sobe ao "
                        "Supabase (scripts/sync_docs_to_supabase.py). Ver local_staging/README.md.",
    },
    {
        # DESCONTINUADO (2026-07): escrevia na tabela legada public.multiplos,
        # que foi DROPADA — DY/dividendos agora vêm da ingestão market.* (brapi,
        # run_market_ingest daily/annual). Rodar este job só geraria erro.
        "table_name":   "multiplos",
        "source_name":  "brapi.dev (histórico DY)",
        "job_name":     "update_brapi_history",
        "update_type":  "incremental",
        "frequency":    "manual",
        "priority":     6,
        "is_active":    False,
        "description":  "[Descontinuado] Backfill de DY na tabela legada `multiplos`, DROPADA — "
                        "dividendos/DY agora vêm da ingestão market.* (brapi).",
    },
    {
        # DESCONTINUADO (2026-07): scraping (yfinance + Fundamentus) para as
        # tabelas legadas public.multiplos / "Demonstracoes_Financeiras", ambas
        # DROPADAS. Fonte única dos fundamentos: market.* (brapi). O job
        # auto-limita via tables_present (viraria no-op), mas fica desativado
        # para não gastar CI nem reintroduzir dado de scraping.
        "table_name":   "Demonstracoes_Financeiras, multiplos",
        "source_name":  "B3 / yfinance + Fundamentus",
        "job_name":     "update_b3_fundamentals",
        "update_type":  "incremental",
        "frequency":    "manual",
        "priority":     5,
        "is_active":    False,
        "description":  "[Descontinuado] Scraping de fundamentos p/ tabelas legadas DROPADAS; "
                        "substituído pela ingestão market.* (run_market_ingest annual).",
    },
    {
        "table_name":   "proventos",
        "source_name":  "B3 / CVM",
        "job_name":     "update_dividendos",
        "update_type":  "incremental",
        "frequency":    "semanal",
        "priority":     6,
        "is_active":    False,
        "description":  "Dividendos e proventos de ativos B3 (implementação pendente)",
    },
    {
        "table_name":   "demonstracoes_financeiras_eua",
        "source_name":  "SEC / Yahoo / FMP",
        "job_name":     "update_empresas_eua",
        "update_type":  "incremental",
        "frequency":    "trimestral",
        "priority":     7,
        "is_active":    False,
        "description":  "Demonstrações financeiras de empresas EUA (implementação pendente)",
    },
    {
        "table_name":   "multiplos, data_quality_scores, data_quality_reports",
        "source_name":  "Data Quality (Banco x Fundamentus x Status Invest)",
        "job_name":     "audit_and_heal",
        "update_type":  "incremental",
        "frequency":    "diario",
        "priority":     10,
        "is_active":    False,
        "description":  "[Descontinuado] Saneamento por scraping cruzado (Fundamentus/Status "
                        "Invest) sobre a tabela legada `multiplos`, que foi DROPADA (2026-07). "
                        "Fundamentos vêm exclusivamente do market.* (brapi); não há mais o que "
                        "sanear por scraping. Mantido inativo.",
    },
    # ── Importações manuais de investimentos ─────────────────────────────────
    # frequency='manual' garante que NUNCA são executadas pelo
    # orquestrador automático (run_data_updates --all / GitHub Actions).
    # Cada importação é disparada pela UI (Configurações > Banco & Importação)
    # e grava aqui apenas o log de execução, para histórico/visibilidade.
    {
        "table_name":   "investment_transactions",
        "source_name":  "B3 — Negociação (manual)",
        "job_name":     "import_b3_negociacao",
        "update_type":  "manual",
        "frequency":    "manual",
        "priority":     20,
        "is_active":    True,
        "description":  "Importação manual de operações de compra/venda exportadas da B3 (Área do Investidor → Negociação)",
    },
    {
        "table_name":   "dividends, investment_transactions",
        "source_name":  "B3 — Movimentação (manual)",
        "job_name":     "import_b3_movimentacao",
        "update_type":  "manual",
        "frequency":    "manual",
        "priority":     21,
        "is_active":    True,
        "description":  "Importação manual de proventos e eventos exportados da B3 (Área do Investidor → Movimentação)",
    },
    {
        "table_name":   "portfolio_position_snapshots, dividends",
        "source_name":  "XP — Consolidado (manual)",
        "job_name":     "import_xp_consolidado",
        "update_type":  "manual",
        "frequency":    "manual",
        "priority":     22,
        "is_active":    True,
        "description":  "Importação manual do Relatório Consolidado XP (mensal ou anual) — snapshot de posições + proventos recebidos",
    },
    {
        "table_name":   "investment_transactions",
        "source_name":  "Nomad — Notas PDF (manual)",
        "job_name":     "import_nomad_pdf",
        "update_type":  "manual",
        "frequency":    "manual",
        "priority":     23,
        "is_active":    True,
        "description":  "Importação manual de notas Nomad em PDF (Apex Clearing / DriveWealth) — aceita múltiplos arquivos por lote",
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
                    f.next_expected_update,
                    COALESCE(f.freshness_status, 'never_updated') AS freshness_status,
                    COALESCE(f.last_records_inserted, 0) AS last_records_inserted,
                    COALESCE(f.last_records_updated, 0) AS last_records_updated,
                    COALESCE(f.last_records_failed, 0) AS last_records_failed,
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
        inserted = 0
        with engine.begin() as conn:
            for item in _DEFAULT_REGISTRY:
                result = conn.execute(text("""
                    INSERT INTO data_update_registry
                        (table_name, source_name, job_name, update_type,
                         frequency, priority, is_active, description)
                    VALUES
                        (:table_name, :source_name, :job_name, :update_type,
                         :frequency, :priority, :is_active, :description)
                    ON CONFLICT (job_name) DO UPDATE SET
                        table_name = EXCLUDED.table_name,
                        description = EXCLUDED.description,
                        source_name = EXCLUDED.source_name,
                        update_type = EXCLUDED.update_type,
                        frequency = EXCLUDED.frequency,
                        priority = EXCLUDED.priority,
                        is_active = EXCLUDED.is_active,
                        updated_at = NOW()
                """), item)
                inserted += result.rowcount
        return inserted
    except Exception as exc:
        logger.warning("seed_registry falhou: %s", exc)
        return 0

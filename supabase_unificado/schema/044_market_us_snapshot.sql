-- ============================================================
-- 044_market_us_snapshot.sql
-- Vitrine Empresas Americanas — snapshot compacto por empresa.
--
-- Mesmo padrão da vitrine de FIIs (039): o warehouse local computa TUDO
-- (scores, assimetria, avançado, dossiê) e publica aqui só o produto final —
-- alguns KB por empresa. Esta é a ÚNICA tabela market_us que vive no Supabase;
-- os históricos pesados continuam warehouse-only.
--
-- AUTOSSUFICIENTE de propósito: cria o schema e não tem FK para companies,
-- porque no Supabase as demais tabelas market_us NÃO existem.
-- Idempotente; sem DROP/TRUNCATE/DELETE.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS market_us;

CREATE TABLE IF NOT EXISTS market_us.company_snapshots (
    symbol          TEXT        PRIMARY KEY,
    cik             TEXT,
    name            TEXT,
    sector          TEXT,
    industry        TEXT,
    exchange        TEXT,
    security_type   TEXT,
    is_reit         BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    -- score fundamentalista (pré-computado no warehouse)
    score                      NUMERIC(6,2),
    score_quality              NUMERIC(6,2),
    score_growth               NUMERIC(6,2),
    score_solidity             NUMERIC(6,2),
    score_capital_efficiency   NUMERIC(6,2),
    score_valuation            NUMERIC(6,2),
    score_shareholder          NUMERIC(6,2),
    coverage                   NUMERIC(6,2),
    -- produtos completos serializados (deterministas, computados no warehouse)
    metrics         JSONB,      -- compute_company_metrics (snapshot)
    asymmetry       JSONB,      -- score_asymmetry (Fora da Curva)
    advanced        JSONB,      -- Piotroski/Altman/Sloan/ROIC incremental
    dossie          JSONB,      -- assemble_dossie (classificação, flags, tese)
    financials      JSONB,      -- série anual compacta p/ tabela do dossiê
    last_fiscal_year INTEGER,
    -- proveniência
    score_version   TEXT,
    generated_at    TIMESTAMPTZ,
    published_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market_us.company_snapshots IS
    'Vitrine EUA: 1 linha/empresa com scores e produtos finais. Publicada do warehouse local; única tabela market_us no Supabase.';
CREATE INDEX IF NOT EXISTS idx_us_snap_sector ON market_us.company_snapshots (sector, industry);
CREATE INDEX IF NOT EXISTS idx_us_snap_score  ON market_us.company_snapshots (score DESC);

-- ============================================================
-- FIM 044.
-- ============================================================

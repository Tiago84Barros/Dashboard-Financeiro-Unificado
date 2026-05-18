-- ============================================================
-- 010_portfolio_position_snapshots.sql
-- Snapshots historicos de posicoes de investimento
-- Banco: Dashboard Financeiro Unificado (Supabase - schema public)
--
-- SEGURANCA:
--   Nao contem DROP TABLE, TRUNCATE ou DELETE.
--   CREATE/ALTER sao idempotentes.
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio_position_snapshots (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    portfolio_id    UUID          NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    asset_id        UUID          NOT NULL REFERENCES assets(id) ON DELETE RESTRICT,
    report_date     DATE          NOT NULL,
    quantity        NUMERIC(18,8) NOT NULL,
    market_price    NUMERIC(15,6),
    market_value    NUMERIC(15,2) NOT NULL,
    invested_value  NUMERIC(15,2),
    original_market_value  NUMERIC(15,2),
    original_invested_value NUMERIC(15,2),
    fx_rate_to_brl  NUMERIC(15,6),
    asset_name      VARCHAR(300),
    asset_type      VARCHAR(50),
    is_loaned       BOOLEAN       NOT NULL DEFAULT FALSE,
    institution     VARCHAR(150),
    currency        CHAR(3)       NOT NULL DEFAULT 'BRL',
    country         CHAR(2)       NOT NULL DEFAULT 'BR',
    source_system   VARCHAR(30)   NOT NULL DEFAULT 'app2',
    source_table    VARCHAR(50)   NOT NULL DEFAULT 'xp_positions',
    source_id       TEXT,
    imported_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (portfolio_id, asset_id, report_date, source_system, source_table, source_id)
);

COMMENT ON TABLE portfolio_position_snapshots IS 'Snapshots historicos de posicoes importados do App2/XP. Preserva patrimonio por ativo e data.';
COMMENT ON COLUMN portfolio_position_snapshots.report_date IS 'Data de referencia do relatorio/snapshot da corretora.';
COMMENT ON COLUMN portfolio_position_snapshots.market_value IS 'Valor de mercado no snapshot original.';
COMMENT ON COLUMN portfolio_position_snapshots.invested_value IS 'Valor investido informado pela fonte, quando disponivel.';
COMMENT ON COLUMN portfolio_position_snapshots.original_market_value IS 'Valor de mercado na moeda original da fonte, quando diferente de BRL.';
COMMENT ON COLUMN portfolio_position_snapshots.original_invested_value IS 'Valor investido na moeda original da fonte, quando diferente de BRL.';
COMMENT ON COLUMN portfolio_position_snapshots.fx_rate_to_brl IS 'Taxa usada para converter a moeda original para BRL no snapshot.';

ALTER TABLE portfolio_position_snapshots
    ADD COLUMN IF NOT EXISTS original_market_value NUMERIC(15,2);

ALTER TABLE portfolio_position_snapshots
    ADD COLUMN IF NOT EXISTS original_invested_value NUMERIC(15,2);

ALTER TABLE portfolio_position_snapshots
    ADD COLUMN IF NOT EXISTS fx_rate_to_brl NUMERIC(15,6);

CREATE INDEX IF NOT EXISTS idx_position_snapshots_user_date
    ON portfolio_position_snapshots (user_id, report_date DESC);

CREATE INDEX IF NOT EXISTS idx_position_snapshots_asset_date
    ON portfolio_position_snapshots (asset_id, report_date DESC);

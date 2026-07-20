-- ============================================================
-- 045_market_us_quality_v3.sql
-- Cobertura, rastreabilidade e validação da metodologia americana v0.3.
-- Idempotente; sem DROP/TRUNCATE/DELETE. Warehouse local + vitrine remota.
-- ============================================================

ALTER TABLE market_us.assets
    ADD COLUMN IF NOT EXISTS analysis_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS status_reason TEXT,
    ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ;

DO $$ BEGIN
    ALTER TABLE market_us.assets ADD CONSTRAINT ck_us_asset_analysis_status
        CHECK (analysis_status IN ('eligible','pending','excluded','unresolved'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE market_us.cash_flow_statements
    ADD COLUMN IF NOT EXISTS depreciation_and_amortization NUMERIC(24,2);

ALTER TABLE market_us.company_snapshots
    ADD COLUMN IF NOT EXISTS score_confidence NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS score_status TEXT,
    ADD COLUMN IF NOT EXISTS critical_missing JSONB;

ALTER TABLE market_us.score_vintages
    ADD COLUMN IF NOT EXISTS coverage NUMERIC(6,2),
    ADD COLUMN IF NOT EXISTS score_confidence NUMERIC(6,2);

ALTER TABLE market_us.backtest_results
    ADD COLUMN IF NOT EXISTS transaction_cost_bps NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS gross_ann_return NUMERIC(10,6),
    ADD COLUMN IF NOT EXISTS benchmark_name TEXT NOT NULL DEFAULT 'equal_weight_universe',
    ADD COLUMN IF NOT EXISTS validation_status TEXT NOT NULL DEFAULT 'diagnostic',
    ADD COLUMN IF NOT EXISTS bootstrap_json JSONB;

ALTER TABLE market_us.data_quality_audit
    ADD COLUMN IF NOT EXISTS run_key TEXT;

CREATE INDEX IF NOT EXISTS idx_us_assets_analysis_status
    ON market_us.assets (analysis_status, company_id);
CREATE INDEX IF NOT EXISTS idx_us_prices_monthly_symbol_date
    ON market_us.prices_monthly (symbol, month_end DESC);
CREATE INDEX IF NOT EXISTS idx_us_mcap_symbol_date
    ON market_us.market_cap_history (symbol, date DESC);
CREATE INDEX IF NOT EXISTS idx_us_score_version_asof
    ON market_us.score_vintages (score_version, as_of_date, track);
CREATE INDEX IF NOT EXISTS idx_us_dq_run_key
    ON market_us.data_quality_audit (run_key, check_name);

-- O App4 usa conexão PostgreSQL de servidor. Clientes anon/authenticated não
-- precisam acessar diretamente a vitrine pelo Data API.
ALTER TABLE market_us.company_snapshots ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE market_us.company_snapshots FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE market_us.company_snapshots FROM authenticated;
    END IF;
END $$;

COMMENT ON COLUMN market_us.assets.analysis_status IS
    'Elegibilidade explícita; ativos sem demonstrações não contaminam a cobertura do universo analisável.';
COMMENT ON COLUMN market_us.company_snapshots.score_confidence IS
    'Confiança 0-100 baseada em cobertura e presença das trilhas críticas.';
COMMENT ON COLUMN market_us.company_snapshots.score_status IS
    'screen_grade, research_grade ou decision_grade; não equivale a recomendação.';

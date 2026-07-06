-- ============================================================
-- 022_market_pit_cutover.sql
-- Marca o instante a partir do qual first_seen_at passa a representar
-- observação prospectiva real. Dados anteriores são baseline de migração
-- e NÃO podem alimentar backtests point-in-time.
-- ============================================================

CREATE TABLE IF NOT EXISTS market.pipeline_cutovers (
    name        TEXT PRIMARY KEY,
    cutoff_at   TIMESTAMPTZ NOT NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO market.pipeline_cutovers (name, cutoff_at, notes)
VALUES (
    'point_in_time_v1',
    NOW(),
    'Demonstrações com first_seen_at anterior ou igual a este instante foram retropreenchidas pela migration 019.'
)
ON CONFLICT (name) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_metric_vintages_backtest_eligible
    ON market.calculated_metric_vintages
       (ticker, year, metric_name, available_at)
    WHERE availability_quality <> 'migration_baseline';


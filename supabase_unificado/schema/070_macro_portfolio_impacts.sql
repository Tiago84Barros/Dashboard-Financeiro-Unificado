CREATE TABLE IF NOT EXISTS macro_portfolio_impacts (
    id BIGSERIAL PRIMARY KEY,
    asset_class TEXT NOT NULL,
    model_id UUID NOT NULL,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_code TEXT NOT NULL,
    factor TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('positive','negative','neutral')),
    intensity NUMERIC(8,2) NOT NULL,
    confidence NUMERIC(8,2) NOT NULL,
    portfolio_weight NUMERIC(12,8) NOT NULL,
    weighted_intensity NUMERIC(10,4) NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_macro_portfolio_impacts_latest
    ON macro_portfolio_impacts (asset_class, model_id, calculated_at DESC);

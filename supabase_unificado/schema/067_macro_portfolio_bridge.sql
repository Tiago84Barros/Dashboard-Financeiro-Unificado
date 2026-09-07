-- Ponte local, mínima e aditiva entre snapshots de carteira e inteligência macro.
-- Não replica payloads financeiros nem identificadores de proprietário.

CREATE TABLE IF NOT EXISTS public.macro_portfolio_assets (
    asset_class TEXT NOT NULL CHECK (asset_class IN ('b3', 'us', 'fii')),
    model_id UUID NOT NULL,
    symbol TEXT NOT NULL,
    weight NUMERIC(12,8) NOT NULL CHECK (weight >= 0 AND weight <= 1),
    currency CHAR(3) NOT NULL,
    as_of_date DATE NOT NULL,
    source_digest TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (asset_class, model_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_macro_portfolio_assets_as_of
    ON public.macro_portfolio_assets (asset_class, as_of_date DESC);

COMMENT ON TABLE public.macro_portfolio_assets IS
    'Réplica local mínima de posições de carteiras-modelo para impactos macro; sem payload ou proprietário.';

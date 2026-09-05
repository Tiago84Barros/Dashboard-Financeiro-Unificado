-- Exposições macro declaradas por setor. Nenhum coeficiente é inferido.

ALTER TABLE public.macro_portfolio_assets
    ADD COLUMN IF NOT EXISTS sector TEXT;

CREATE TABLE IF NOT EXISTS public.macro_sector_exposures (
    asset_class TEXT NOT NULL CHECK (asset_class IN ('b3', 'us', 'fii')),
    sector TEXT NOT NULL,
    factor TEXT NOT NULL,
    sensitivity NUMERIC(5,4) NOT NULL CHECK (sensitivity BETWEEN -1 AND 1),
    confidence NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    channel TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (asset_class, sector, factor)
);

COMMENT ON TABLE public.macro_sector_exposures IS
    'Mapa explícito de setor para fator macro; ausência significa impacto não mapeado.';

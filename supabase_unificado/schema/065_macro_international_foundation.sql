-- 065_macro_international_foundation.sql
-- Fundação append-only para indicadores macro internacionais.
-- Não contém DROP, TRUNCATE ou alteração de dados existentes.
-- Aplicar apenas após backup verificável e em ambiente descartável primeiro.

CREATE TABLE IF NOT EXISTS public.macro_indicators (
    id BIGSERIAL PRIMARY KEY,
    canonical_code TEXT NOT NULL,
    provider_code TEXT NOT NULL,
    provider TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    country_code TEXT,
    category TEXT NOT NULL DEFAULT 'unmapped',
    unit TEXT NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency IN ('intraday','daily','weekly','monthly','quarterly','annual','irregular')),
    seasonal_adjustment TEXT,
    source_organization TEXT NOT NULL,
    source_url TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_macro_indicators_provider_code_country
  ON public.macro_indicators (provider, provider_code, COALESCE(country_code, ''));

CREATE TABLE IF NOT EXISTS public.macro_observations (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_code TEXT NOT NULL,
    country_code TEXT,
    reference_period DATE NOT NULL,
    value NUMERIC,
    status TEXT,
    released_at TIMESTAMPTZ,
    retrieved_at TIMESTAMPTZ NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    is_preliminary BOOLEAN NOT NULL DEFAULT FALSE,
    is_forecast BOOLEAN NOT NULL DEFAULT FALSE,
    vintage_date DATE,
    revision_number INTEGER,
    raw_payload_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- A mesma observação/vintage recebida novamente é idempotente; uma revisão
    -- com vintage, release ou valor diferente permanece em uma linha separada.
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_macro_observations_immutable_version
  ON public.macro_observations (provider, provider_code, COALESCE(country_code, ''), reference_period,
      COALESCE(vintage_date, DATE '9999-12-31'), COALESCE(released_at, TIMESTAMPTZ 'infinity'), COALESCE(value, 'NaN'::NUMERIC));

CREATE INDEX IF NOT EXISTS idx_macro_observations_lookup
  ON public.macro_observations (provider, provider_code, country_code, reference_period DESC, retrieved_at DESC);
CREATE INDEX IF NOT EXISTS idx_macro_observations_vintage
  ON public.macro_observations (reference_period, vintage_date DESC, retrieved_at DESC);

CREATE TABLE IF NOT EXISTS public.macro_releases (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    country_code TEXT NOT NULL,
    event_name TEXT NOT NULL,
    scheduled_at TIMESTAMPTZ NOT NULL,
    retrieved_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('scheduled','released','revised','cancelled')),
    actual_value NUMERIC,
    previous_value NUMERIC,
    revised_previous_value NUMERIC,
    consensus_value NUMERIC,
    forecast_value NUMERIC,
    unit TEXT,
    importance SMALLINT CHECK (importance BETWEEN 0 AND 3),
    raw_payload_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_macro_releases_immutable_version
  ON public.macro_releases (provider, country_code, event_name, scheduled_at, retrieved_at,
      COALESCE(actual_value, 'NaN'::NUMERIC));
CREATE INDEX IF NOT EXISTS idx_macro_releases_calendar
  ON public.macro_releases (country_code, scheduled_at DESC);

COMMENT ON TABLE public.macro_observations IS
  'Registro imutável de observações macro. Revisões não sobrescrevem o valor anterior; consultas PIT filtram retrieved_at/released_at.';

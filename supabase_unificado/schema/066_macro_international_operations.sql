-- Operação local da ingestão macro. Não destrutiva e idempotente.
-- Aplicar somente no PostgreSQL Docker indicado por MACRO_LOCAL_DB_URL.

CREATE TABLE IF NOT EXISTS public.macro_ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    run_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('running','completed','partial_success','failed','skipped')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    note TEXT
);

CREATE TABLE IF NOT EXISTS public.macro_ingestion_checkpoints (
    run_id BIGINT NOT NULL REFERENCES public.macro_ingestion_runs(id),
    provider TEXT NOT NULL,
    cursor_value TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed','skipped')),
    records_inserted INTEGER NOT NULL DEFAULT 0,
    records_failed INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, provider)
);

CREATE TABLE IF NOT EXISTS public.macro_provider_health_checks (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    available BOOLEAN NOT NULL,
    detail TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    run_id BIGINT REFERENCES public.macro_ingestion_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_macro_provider_health_latest
    ON public.macro_provider_health_checks (provider, checked_at DESC);

COMMENT ON TABLE public.macro_ingestion_checkpoints IS
  'Checkpoint por provedor para auditoria e retomada manual; não contém payload ou credencial.';

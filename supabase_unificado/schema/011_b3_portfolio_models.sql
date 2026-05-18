-- ============================================================
-- 011_b3_portfolio_models.sql
-- Portfolio B3 modelo criado pelo usuario
-- Banco: Dashboard Financeiro Unificado (Supabase - schema public)
--
-- SEGURANCA:
--   Nao contem DROP TABLE, TRUNCATE ou DELETE.
--   CREATE sao idempotentes.
-- ============================================================

CREATE TABLE IF NOT EXISTS b3_portfolio_models (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name         VARCHAR(160) NOT NULL,
    status       VARCHAR(20) NOT NULL DEFAULT 'active',
    ano_compra   INTEGER,
    source       VARCHAR(80) NOT NULL DEFAULT 'criacao_portfolio_b3',
    plan_hash    TEXT NOT NULL,
    params_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('active', 'archived'))
);

CREATE TABLE IF NOT EXISTS b3_portfolio_model_items (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id      UUID NOT NULL REFERENCES b3_portfolio_models(id) ON DELETE CASCADE,
    ticker        VARCHAR(16) NOT NULL,
    nome          VARCHAR(200),
    setor         TEXT,
    subsetor      TEXT,
    segmento      TEXT,
    weight        NUMERIC(12,8),
    score         NUMERIC(18,8),
    alpha_selic   NUMERIC(12,4),
    alpha_ew      NUMERIC(12,4),
    rank_score    INTEGER,
    ano_lider     INTEGER,
    motivos_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
    meta_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_id, ticker)
);

COMMENT ON TABLE b3_portfolio_models IS 'Portfolio B3 modelo/padrao criado pelo usuario a partir da Criacao de Portfolio.';
COMMENT ON TABLE b3_portfolio_model_items IS 'Empresas e pesos sugeridos do portfolio B3 modelo ativo/historico.';
COMMENT ON COLUMN b3_portfolio_models.status IS 'active = portfolio modelo atual; archived = versao anterior substituida.';
COMMENT ON COLUMN b3_portfolio_model_items.weight IS 'Peso normalizado sugerido no portfolio modelo.';

CREATE INDEX IF NOT EXISTS idx_b3_portfolio_models_user_status
    ON b3_portfolio_models (user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_b3_portfolio_model_items_model_weight
    ON b3_portfolio_model_items (model_id, weight DESC);

-- ============================================================
-- 047_us_portfolio_models.sql
-- Portfolio americano modelo criado pelo usuario
-- Banco: Dashboard Financeiro Unificado (Supabase - schema public)
--
-- Espelha 011_b3_portfolio_models.sql + 018_rls_portfolio_models.sql +
-- 020_b3_portfolio_hardening.sql para o mercado dos Estados Unidos.
-- Fica em public (e nao em market_us) porque e decisao do usuario, nao dado
-- de mercado: market_us roda em modo snapshot/somente-leitura no deploy.
--
-- SEGURANCA:
--   Nao contem DROP TABLE, TRUNCATE ou DELETE.
--   CREATE sao idempotentes.
--
-- Este arquivo e a referencia declarativa. O app tambem cria as tabelas sob
-- demanda em core/us_portfolio_model.py (_ensure_tables), como ja acontece
-- com as carteiras B3 e FII.
-- ============================================================

CREATE TABLE IF NOT EXISTS us_portfolio_models (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name         VARCHAR(160) NOT NULL,
    status       VARCHAR(20) NOT NULL DEFAULT 'active',
    ano_compra   INTEGER,
    source       VARCHAR(80) NOT NULL DEFAULT 'criacao_portfolio_us',
    plan_hash    TEXT NOT NULL,
    params_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('active', 'archived'))
);

CREATE TABLE IF NOT EXISTS us_portfolio_model_items (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id          UUID NOT NULL REFERENCES us_portfolio_models(id) ON DELETE CASCADE,
    symbol            VARCHAR(16) NOT NULL,
    nome              VARCHAR(200),
    setor             TEXT,
    industria         TEXT,
    weight            NUMERIC(12,8),
    entry_score       NUMERIC(18,8),
    fundamental_score NUMERIC(18,8),
    coverage          NUMERIC(12,4),
    rank_score        INTEGER,
    meta_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_us_portfolio_models_user_status
    ON us_portfolio_models (user_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_us_portfolio_model_items_model_weight
    ON us_portfolio_model_items (model_id, weight DESC);

-- No maximo um modelo ativo por usuario.
CREATE UNIQUE INDEX IF NOT EXISTS uq_us_portfolio_models_active_per_user
    ON us_portfolio_models (user_id)
    WHERE status = 'active';

-- ── RLS ─────────────────────────────────────────────────────
-- Protege acesso via Supabase API/anon key. A conexao do app (role postgres)
-- bypassa RLS; a politica existe para o caminho HTTP.
ALTER TABLE us_portfolio_models      ENABLE ROW LEVEL SECURITY;
ALTER TABLE us_portfolio_model_items ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='us_portfolio_models'
          AND policyname='us_portfolio_models_owner_all'
    ) THEN
        CREATE POLICY us_portfolio_models_owner_all ON us_portfolio_models
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='us_portfolio_model_items'
          AND policyname='us_portfolio_model_items_owner_all'
    ) THEN
        CREATE POLICY us_portfolio_model_items_owner_all ON us_portfolio_model_items
            USING (EXISTS (
                SELECT 1 FROM us_portfolio_models m
                WHERE m.id = model_id AND m.user_id = auth.uid()
            ))
            WITH CHECK (EXISTS (
                SELECT 1 FROM us_portfolio_models m
                WHERE m.id = model_id AND m.user_id = auth.uid()
            ));
    END IF;
END; $$;

-- ============================================================
-- FIM 047.
-- ============================================================

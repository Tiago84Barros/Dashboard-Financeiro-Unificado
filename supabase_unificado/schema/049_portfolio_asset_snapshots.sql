-- ============================================================
-- 049_portfolio_asset_snapshots.sql
-- Snapshot analitico por ativo das carteiras-modelo do usuario.
-- Banco: Dashboard Financeiro Unificado (Supabase - schema public)
--
-- Espelha o padrao de 047_us_portfolio_models.sql (indices + RLS por dono).
--
-- NOTA DE MODELAGEM:
--   model_id e POLIMORFICO: aponta para b3_portfolio_models,
--   us_portfolio_models ou fii_portfolio_models conforme asset_class.
--   Por isso nao ha FOREIGN KEY nem ON DELETE CASCADE nessa coluna.
--   A limpeza de orfaos e responsabilidade de core/portfolio/repository.prune_orphans(),
--   criada na Task 5 desta fase e chamada a cada gravacao. Foi uma troca deliberada:
--   integridade declarativa por extensibilidade (classe de ativo nova nao exige ALTER).
--
-- SEGURANCA:
--   Apenas CREATE e ALTER (idempotentes).
--   Nenhum comando destrutivo.
-- ============================================================

CREATE TABLE IF NOT EXISTS portfolio_asset_snapshots (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    asset_class    VARCHAR(16) NOT NULL,
    model_id       UUID NOT NULL,
    symbol         VARCHAR(16) NOT NULL,
    schema_version INTEGER NOT NULL,
    as_of_date     DATE NOT NULL,
    payload        JSONB NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (asset_class, model_id, symbol),
    CHECK (asset_class IN ('b3', 'us', 'fii'))
);

CREATE TABLE IF NOT EXISTS portfolio_allocation_targets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    status       VARCHAR(20) NOT NULL DEFAULT 'active',
    total_brl    NUMERIC(18,2),
    targets_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (status IN ('active', 'archived'))
);

COMMENT ON TABLE portfolio_asset_snapshots IS
    'Snapshot analitico por ativo (fundamentos, metricas, historico, premissas, evidencias) da carteira-modelo.';
COMMENT ON COLUMN portfolio_asset_snapshots.model_id IS
    'Polimorfico: id em b3_/us_/fii_portfolio_models conforme asset_class. Sem FK por decisao de projeto.';
COMMENT ON TABLE portfolio_allocation_targets IS
    'Alocacao-alvo por classe de ativo usada pelo Portfolio Global. Consumida a partir da Fase 2.';

CREATE INDEX IF NOT EXISTS idx_portfolio_asset_snapshots_lookup
    ON portfolio_asset_snapshots (user_id, asset_class, model_id);

CREATE INDEX IF NOT EXISTS idx_portfolio_asset_snapshots_symbol
    ON portfolio_asset_snapshots (asset_class, symbol, as_of_date DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_portfolio_allocation_targets_active_per_user
    ON portfolio_allocation_targets (user_id)
    WHERE status = 'active';

-- RLS: protege o caminho HTTP (anon key). A conexao do app (role postgres) bypassa.
ALTER TABLE portfolio_asset_snapshots    ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_allocation_targets ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='portfolio_asset_snapshots'
          AND policyname='portfolio_asset_snapshots_owner_all'
    ) THEN
        CREATE POLICY portfolio_asset_snapshots_owner_all ON portfolio_asset_snapshots
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='portfolio_allocation_targets'
          AND policyname='portfolio_allocation_targets_owner_all'
    ) THEN
        CREATE POLICY portfolio_allocation_targets_owner_all ON portfolio_allocation_targets
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ============================================================
-- FIM 049.
-- ============================================================

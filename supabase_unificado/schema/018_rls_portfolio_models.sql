-- ============================================================
-- 018_rls_portfolio_models.sql
-- RLS + unicidade de modelo ativo p/ tabelas de portfolio do usuario
-- Banco: Dashboard Financeiro Unificado (Supabase - schema public)
--
-- Auditoria 2026-07-04: b3_portfolio_models, b3_portfolio_model_items e
-- portfolio_position_snapshots foram criadas (010/011) SEM Row Level
-- Security, ao contrario das demais tabelas com user_id (006/012).
-- A conexao do app (role postgres) NAO e afetada (bypassa RLS); isto
-- protege o acesso via Supabase API/anon key, como nas demais tabelas.
--
-- Tambem garante no maximo UM modelo ativo por usuario (indice parcial).
-- O fluxo de gravacao ja arquiva o ativo antes de inserir o novo, na
-- mesma transacao (core/b3_portfolio_model.py), entao o indice nao
-- conflita com o app — apenas impede estados invalidos.
--
-- SEGURANCA:
--   Nao contem DROP TABLE, TRUNCATE ou DELETE.
--   Comandos idempotentes.
-- ============================================================

ALTER TABLE b3_portfolio_models        ENABLE ROW LEVEL SECURITY;
ALTER TABLE b3_portfolio_model_items   ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_position_snapshots ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='b3_portfolio_models'
          AND policyname='b3_portfolio_models_owner_all'
    ) THEN
        CREATE POLICY b3_portfolio_models_owner_all ON b3_portfolio_models
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- Itens nao tem user_id proprio: herda o dono via modelo pai.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='b3_portfolio_model_items'
          AND policyname='b3_portfolio_model_items_owner_all'
    ) THEN
        CREATE POLICY b3_portfolio_model_items_owner_all ON b3_portfolio_model_items
            USING (EXISTS (
                SELECT 1 FROM b3_portfolio_models m
                WHERE m.id = model_id AND m.user_id = auth.uid()
            ))
            WITH CHECK (EXISTS (
                SELECT 1 FROM b3_portfolio_models m
                WHERE m.id = model_id AND m.user_id = auth.uid()
            ));
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='portfolio_position_snapshots'
          AND policyname='portfolio_position_snapshots_owner_all'
    ) THEN
        CREATE POLICY portfolio_position_snapshots_owner_all ON portfolio_position_snapshots
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- No maximo 1 modelo ativo por usuario.
CREATE UNIQUE INDEX IF NOT EXISTS uq_b3_portfolio_models_active_per_user
    ON b3_portfolio_models (user_id)
    WHERE status = 'active';

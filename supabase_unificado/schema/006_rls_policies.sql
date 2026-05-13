-- ============================================================
-- 006_rls_policies.sql
-- Row Level Security + role app4_reader
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE.
--   ENABLE ROW LEVEL SECURITY é idempotente.
--   CREATE POLICY é executado via DO $$ IF NOT EXISTS para idempotência.
--   GRANT é idempotente (repetir não causa erro).
--
-- ARQUITETURA DE SEGURANÇA:
--   App 4 usa conexão direta PostgreSQL via SQLAlchemy (como `postgres`).
--   Conexões diretas bypass RLS por padrão no PostgreSQL.
--   Filtro real: WHERE user_id = :owner_id no código da aplicação.
--   RLS aqui serve como camada extra para conexões via Supabase API (anon key).
--   auth.uid() retorna NULL em conexões SQLAlchemy — comportamento esperado.
--
-- TABELAS COM RLS (15): dados pessoais do usuário
--   profiles, accounts, cards, categories, transactions,
--   budgets, financial_goals, debts, portfolios,
--   portfolio_positions, investment_transactions, dividends,
--   alerts, user_settings, import_batches
--
-- TABELAS SEM RLS (7): dados de mercado e controle interno
--   financial_institutions, assets, asset_quotes,
--   benchmarks, benchmark_quotes, import_logs, migration_source_map
--
-- DEPENDÊNCIAS: 001 a 004 (todas as tabelas devem existir)
-- PRÓXIMO ARQUIVO: 007_views.sql
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- PARTE 1: Criar role app4_reader (idempotente)
-- ────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app4_reader') THEN
        CREATE ROLE app4_reader NOLOGIN;
        COMMENT ON ROLE app4_reader IS 'Role somente-leitura do App 4. Nunca conceder INSERT, UPDATE, DELETE ou BYPASSRLS.';
    END IF;
END;
$$;

-- ────────────────────────────────────────────────────────────
-- PARTE 2: ENABLE ROW LEVEL SECURITY (idempotente)
-- ────────────────────────────────────────────────────────────

ALTER TABLE profiles                ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounts                ENABLE ROW LEVEL SECURITY;
ALTER TABLE cards                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE categories              ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions            ENABLE ROW LEVEL SECURITY;
ALTER TABLE budgets                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE financial_goals         ENABLE ROW LEVEL SECURITY;
ALTER TABLE debts                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolios              ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_positions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE investment_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE dividends               ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_settings           ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_batches          ENABLE ROW LEVEL SECURITY;

-- ────────────────────────────────────────────────────────────
-- PARTE 3: POLICIES (idempotentes via DO $$ IF NOT EXISTS)
-- ────────────────────────────────────────────────────────────

-- ── profiles ─────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='profiles'
                   AND policyname='profiles_owner_select') THEN
        CREATE POLICY profiles_owner_select ON profiles
            FOR SELECT USING (id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='profiles'
                   AND policyname='profiles_owner_update') THEN
        CREATE POLICY profiles_owner_update ON profiles
            FOR UPDATE USING (id = auth.uid())
            WITH CHECK (id = auth.uid());
    END IF;
END; $$;

-- M03: policy INSERT para profiles — necessária para criação via Supabase API
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='profiles'
                   AND policyname='profiles_owner_insert') THEN
        CREATE POLICY profiles_owner_insert ON profiles
            FOR INSERT
            WITH CHECK (id = auth.uid());
    END IF;
END; $$;

-- ── accounts ─────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='accounts'
                   AND policyname='accounts_owner_all') THEN
        CREATE POLICY accounts_owner_all ON accounts
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── cards ─────────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='cards'
                   AND policyname='cards_owner_all') THEN
        CREATE POLICY cards_owner_all ON cards
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── categories ───────────────────────────────────────────────
-- Leitura: categorias do usuário + categorias do sistema (user_id IS NULL)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='categories'
                   AND policyname='categories_read_owner_or_system') THEN
        CREATE POLICY categories_read_owner_or_system ON categories
            FOR SELECT USING (user_id = auth.uid() OR user_id IS NULL);
    END IF;
END; $$;

-- M04: escrita separada em INSERT / UPDATE / DELETE para não sobrepor a policy SELECT acima
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='categories'
                   AND policyname='categories_write_owner') THEN
        CREATE POLICY categories_write_owner ON categories
            FOR INSERT
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='categories'
                   AND policyname='categories_update_owner') THEN
        CREATE POLICY categories_update_owner ON categories
            FOR UPDATE USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='categories'
                   AND policyname='categories_delete_owner') THEN
        CREATE POLICY categories_delete_owner ON categories
            FOR DELETE USING (user_id = auth.uid());
    END IF;
END; $$;

-- ── transactions ─────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='transactions'
                   AND policyname='transactions_owner_all') THEN
        CREATE POLICY transactions_owner_all ON transactions
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── budgets ──────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='budgets'
                   AND policyname='budgets_owner_all') THEN
        CREATE POLICY budgets_owner_all ON budgets
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── financial_goals ──────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='financial_goals'
                   AND policyname='goals_owner_all') THEN
        CREATE POLICY goals_owner_all ON financial_goals
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── debts ────────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='debts'
                   AND policyname='debts_owner_all') THEN
        CREATE POLICY debts_owner_all ON debts
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── portfolios ───────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='portfolios'
                   AND policyname='portfolios_owner_all') THEN
        CREATE POLICY portfolios_owner_all ON portfolios
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── portfolio_positions ──────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='portfolio_positions'
                   AND policyname='positions_owner_all') THEN
        CREATE POLICY positions_owner_all ON portfolio_positions
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── investment_transactions ──────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='investment_transactions'
                   AND policyname='inv_tx_owner_all') THEN
        CREATE POLICY inv_tx_owner_all ON investment_transactions
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── dividends ────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='dividends'
                   AND policyname='dividends_owner_all') THEN
        CREATE POLICY dividends_owner_all ON dividends
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── alerts ───────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='alerts'
                   AND policyname='alerts_owner_all') THEN
        CREATE POLICY alerts_owner_all ON alerts
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── user_settings ────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='user_settings'
                   AND policyname='settings_owner_all') THEN
        CREATE POLICY settings_owner_all ON user_settings
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ── import_batches ───────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='import_batches'
                   AND policyname='batches_owner_all') THEN
        CREATE POLICY batches_owner_all ON import_batches
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- ────────────────────────────────────────────────────────────
-- PARTE 4: GRANT SELECT ao role app4_reader (idempotente)
-- ────────────────────────────────────────────────────────────
-- O role app4_reader recebe apenas SELECT nas 22 tabelas.
-- NUNCA conceder INSERT, UPDATE, DELETE, TRUNCATE ou BYPASSRLS.

GRANT SELECT ON TABLE
    profiles,
    financial_institutions,
    accounts,
    cards,
    categories,
    transactions,
    budgets,
    financial_goals,
    debts,
    assets,
    portfolios,
    portfolio_positions,
    investment_transactions,
    dividends,
    asset_quotes,
    benchmarks,
    benchmark_quotes,
    alerts,
    user_settings,
    import_batches,
    import_logs,
    migration_source_map
TO app4_reader;

-- Acesso às views analíticas (criadas em 007_views.sql)
-- Executar após criar as views:
-- GRANT SELECT ON TABLE
--     v_account_balance,
--     v_monthly_cashflow,
--     v_category_spending_mtd,
--     v_budget_usage_mtd,
--     v_investment_summary,
--     v_net_worth
-- TO app4_reader;

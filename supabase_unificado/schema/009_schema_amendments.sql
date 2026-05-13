-- ============================================================
-- 009_schema_amendments.sql
-- Correções identificadas na Fase 4.4 (revisão humana)
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.4 — Correções pós-revisão
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE, DROP SCHEMA.
--   ALTER TABLE usado apenas para recriar FKs (DROP + ADD CONSTRAINT).
--   Idempotente: DROP CONSTRAINT IF EXISTS + DO $$ IF NOT EXISTS.
--   Seguro para executar múltiplas vezes.
--
-- QUANDO EXECUTAR:
--   Após executar 001–008 com sucesso e ANTES de inserir dados reais.
--   (Aplicar constraints em tabelas vazias é mais rápido e sem risco.)
--   Se as tabelas já tiverem dados, o PostgreSQL valida cada linha —
--   confirmar que não há dados inconsistentes antes de executar.
--
-- CORREÇÕES (M01–M05 da revisão Fase 4.4):
--   M01: transactions.account_id → ON DELETE RESTRICT
--   M02: portfolio_positions/investment_transactions/dividends → asset_id ON DELETE RESTRICT
--   M03: policy INSERT para profiles
--   M04: refatorar categories_write_owner (FOR ALL → INSERT + UPDATE + DELETE)
--   M05: sem SQL — apenas correção de comentário no arquivo 008
--
-- DEPENDÊNCIAS: 001 a 008 (todas as tabelas e policies devem existir)
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- M01: transactions.account_id — adicionar ON DELETE RESTRICT
-- Impede excluir contas que possuem transações.
-- ────────────────────────────────────────────────────────────

ALTER TABLE transactions
    DROP CONSTRAINT IF EXISTS transactions_account_id_fkey;

ALTER TABLE transactions
    ADD CONSTRAINT transactions_account_id_fkey
        FOREIGN KEY (account_id)
        REFERENCES accounts(id)
        ON DELETE RESTRICT;

-- ────────────────────────────────────────────────────────────
-- M02: FKs para assets — adicionar ON DELETE RESTRICT
-- Impede excluir ativos referenciados por posições, operações ou proventos.
-- ────────────────────────────────────────────────────────────

-- portfolio_positions.asset_id
ALTER TABLE portfolio_positions
    DROP CONSTRAINT IF EXISTS portfolio_positions_asset_id_fkey;

ALTER TABLE portfolio_positions
    ADD CONSTRAINT portfolio_positions_asset_id_fkey
        FOREIGN KEY (asset_id)
        REFERENCES assets(id)
        ON DELETE RESTRICT;

-- investment_transactions.asset_id
ALTER TABLE investment_transactions
    DROP CONSTRAINT IF EXISTS investment_transactions_asset_id_fkey;

ALTER TABLE investment_transactions
    ADD CONSTRAINT investment_transactions_asset_id_fkey
        FOREIGN KEY (asset_id)
        REFERENCES assets(id)
        ON DELETE RESTRICT;

-- dividends.asset_id
ALTER TABLE dividends
    DROP CONSTRAINT IF EXISTS dividends_asset_id_fkey;

ALTER TABLE dividends
    ADD CONSTRAINT dividends_asset_id_fkey
        FOREIGN KEY (asset_id)
        REFERENCES assets(id)
        ON DELETE RESTRICT;

-- ────────────────────────────────────────────────────────────
-- M03: policy INSERT para profiles
-- Permite criar perfil via Supabase API (necessário se o app
-- evoluir de single-user para multi-usuário ou usar Auth).
-- ────────────────────────────────────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'profiles'
                     AND policyname = 'profiles_owner_insert') THEN
        CREATE POLICY profiles_owner_insert ON profiles
            FOR INSERT
            WITH CHECK (id = auth.uid());
    END IF;
END; $$;

-- ────────────────────────────────────────────────────────────
-- M04: refatorar categories_write_owner
-- Substitui FOR ALL (que incluía SELECT) por policies específicas
-- de INSERT, UPDATE e DELETE, eliminando sobreposição com
-- categories_read_owner_or_system (FOR SELECT).
-- ────────────────────────────────────────────────────────────

-- Remover a policy ALL existente
DROP POLICY IF EXISTS categories_write_owner ON categories;

-- Recriar como INSERT apenas
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'categories'
                     AND policyname = 'categories_write_owner') THEN
        CREATE POLICY categories_write_owner ON categories
            FOR INSERT
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- UPDATE
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'categories'
                     AND policyname = 'categories_update_owner') THEN
        CREATE POLICY categories_update_owner ON categories
            FOR UPDATE USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- DELETE
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public'
                     AND tablename  = 'categories'
                     AND policyname = 'categories_delete_owner') THEN
        CREATE POLICY categories_delete_owner ON categories
            FOR DELETE USING (user_id = auth.uid());
    END IF;
END; $$;

-- ────────────────────────────────────────────────────────────
-- M05: sem SQL necessário
-- O problema era um valor de exemplo incorreto ('digital_bank')
-- no comentário do arquivo 008_seed_reference_data.sql.
-- Corrigido diretamente no arquivo de origem para 'fintech'.
-- Valores válidos para financial_institutions.type:
--   'bank', 'broker', 'fintech', 'insurance'
-- ────────────────────────────────────────────────────────────

-- (nenhuma instrução SQL necessária para M05)

-- ────────────────────────────────────────────────────────────
-- Verificação pós-execução (executar manualmente para confirmar)
-- ────────────────────────────────────────────────────────────

-- Confirmar FK com ON DELETE RESTRICT em transactions:
-- SELECT conname, confdeltype
-- FROM pg_constraint
-- WHERE conrelid = 'transactions'::regclass AND conname = 'transactions_account_id_fkey';
-- Esperado: confdeltype = 'r' (RESTRICT)

-- Confirmar policies de categories:
-- SELECT policyname, cmd FROM pg_policies
-- WHERE schemaname = 'public' AND tablename = 'categories'
-- ORDER BY policyname;
-- Esperado: categories_delete_owner, categories_read_owner_or_system,
--           categories_update_owner, categories_write_owner (4 linhas)

-- Confirmar policy INSERT de profiles:
-- SELECT policyname FROM pg_policies
-- WHERE schemaname = 'public' AND tablename = 'profiles'
-- ORDER BY policyname;
-- Esperado: profiles_owner_insert, profiles_owner_select, profiles_owner_update (3 linhas)

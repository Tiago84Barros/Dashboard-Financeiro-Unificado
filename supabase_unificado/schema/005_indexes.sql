-- ============================================================
-- 005_indexes.sql
-- Índices de performance para as 22 tabelas do banco unificado
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP INDEX, DROP TABLE, TRUNCATE, DELETE.
--   Todos os CREATE usam IF NOT EXISTS.
--   Seguro para executar múltiplas vezes.
--
-- DEPENDÊNCIAS: 001 a 004 (todas as tabelas devem existir)
-- PRÓXIMO ARQUIVO: 006_rls_policies.sql
--
-- CONVENÇÃO DE NOMES:
--   idx_<tabela>_<colunas>
--   PKs e UNIQUEs (criados nas tabelas) não são repetidos aqui.
-- ============================================================

-- ── profiles ─────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_profiles_email
    ON profiles (email);

-- ── financial_institutions ───────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_fi_name
    ON financial_institutions (name);

-- ── accounts ─────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_accounts_user_id
    ON accounts (user_id);

CREATE INDEX IF NOT EXISTS idx_accounts_fi_id
    ON accounts (financial_institution_id)
    WHERE financial_institution_id IS NOT NULL;

-- ── cards ─────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_cards_user_id
    ON cards (user_id);

CREATE INDEX IF NOT EXISTS idx_cards_account_id
    ON cards (account_id)
    WHERE account_id IS NOT NULL;

-- ── categories ───────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_categories_user_id
    ON categories (user_id);

CREATE INDEX IF NOT EXISTS idx_categories_parent_id
    ON categories (parent_id)
    WHERE parent_id IS NOT NULL;

-- ── transactions ─────────────────────────────────────────────
-- Principal: queries de período por usuário (dashboard mensal)
CREATE INDEX IF NOT EXISTS idx_tx_user_due_date
    ON transactions (user_id, due_date DESC);

-- Queries de conta (saldo atual, extrato)
CREATE INDEX IF NOT EXISTS idx_tx_account_id
    ON transactions (account_id);

-- Queries de categoria (orçamento, análise de gastos)
CREATE INDEX IF NOT EXISTS idx_tx_category_id
    ON transactions (category_id)
    WHERE category_id IS NOT NULL;

-- Queries de status (pendentes)
CREATE INDEX IF NOT EXISTS idx_tx_status
    ON transactions (status)
    WHERE status = 'pending';

-- Parcelamentos
CREATE INDEX IF NOT EXISTS idx_tx_installment_group
    ON transactions (installment_group)
    WHERE installment_group IS NOT NULL;

-- ── budgets ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_budgets_user_month
    ON budgets (user_id, month_year DESC);

-- ── financial_goals ──────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_goals_user_id
    ON financial_goals (user_id);

CREATE INDEX IF NOT EXISTS idx_goals_active
    ON financial_goals (user_id, active)
    WHERE active = TRUE;

-- ── debts ────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_debts_user_id
    ON debts (user_id);

CREATE INDEX IF NOT EXISTS idx_debts_active
    ON debts (user_id, active)
    WHERE active = TRUE;

-- ── assets ───────────────────────────────────────────────────
-- ticker já tem UNIQUE index criado na tabela.
CREATE INDEX IF NOT EXISTS idx_assets_class
    ON assets (class);

CREATE INDEX IF NOT EXISTS idx_assets_exchange
    ON assets (exchange)
    WHERE exchange IS NOT NULL;

-- ── portfolios ───────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_portfolios_user_id
    ON portfolios (user_id);

-- ── portfolio_positions ──────────────────────────────────────
-- (portfolio_id, asset_id) UNIQUE já existe como constraint.
CREATE INDEX IF NOT EXISTS idx_positions_user_portfolio
    ON portfolio_positions (user_id, portfolio_id);

CREATE INDEX IF NOT EXISTS idx_positions_asset_id
    ON portfolio_positions (asset_id);

-- ── investment_transactions ──────────────────────────────────
-- Principal: queries por usuário + ativo (rentabilidade, posição)
CREATE INDEX IF NOT EXISTS idx_inv_tx_user_asset
    ON investment_transactions (user_id, asset_id);

-- Queries por data (cronologia de operações)
CREATE INDEX IF NOT EXISTS idx_inv_tx_date
    ON investment_transactions (transaction_date DESC);

-- Queries por carteira
CREATE INDEX IF NOT EXISTS idx_inv_tx_portfolio_id
    ON investment_transactions (portfolio_id)
    WHERE portfolio_id IS NOT NULL;

-- ── dividends ────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_dividends_user_asset
    ON dividends (user_id, asset_id);

CREATE INDEX IF NOT EXISTS idx_dividends_payment_date
    ON dividends (payment_date DESC);

-- ── asset_quotes ─────────────────────────────────────────────
-- PK (asset_id, timestamp) já indexado. Adicionar ordem DESC para queries recentes.
CREATE INDEX IF NOT EXISTS idx_asset_quotes_asset_ts_desc
    ON asset_quotes (asset_id, timestamp DESC);

-- ── benchmarks ───────────────────────────────────────────────
-- code já tem UNIQUE index. Nenhum índice adicional necessário.

-- ── benchmark_quotes ─────────────────────────────────────────
-- PK (benchmark_id, date) já indexado.
CREATE INDEX IF NOT EXISTS idx_bq_benchmark_date_desc
    ON benchmark_quotes (benchmark_id, date DESC);

-- ── alerts ───────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_alerts_user_active
    ON alerts (user_id, active)
    WHERE active = TRUE;

-- ── user_settings ────────────────────────────────────────────
-- PK = user_id. Sem índice adicional necessário.

-- ── import_batches ───────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_batches_user_status
    ON import_batches (user_id, status);

CREATE INDEX IF NOT EXISTS idx_batches_source
    ON import_batches (source);

-- ── import_logs ──────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_import_logs_batch_id
    ON import_logs (batch_id);

-- Rastrear origem de um registro específico
CREATE INDEX IF NOT EXISTS idx_import_logs_target_source
    ON import_logs (target_table, source_id)
    WHERE source_id IS NOT NULL;

-- ── migration_source_map ─────────────────────────────────────
-- UNIQUE constraints já criam índices. Adicionar para lookup inverso.
CREATE INDEX IF NOT EXISTS idx_msm_target_id
    ON migration_source_map (target_table, target_id);

CREATE INDEX IF NOT EXISTS idx_msm_migrated_at
    ON migration_source_map (migrated_at DESC);

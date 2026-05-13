-- ============================================================
-- 002_financial_tables.sql
-- Tabelas de finanças pessoais
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE, DROP SCHEMA.
--   Todos os CREATE usam IF NOT EXISTS.
--   Seguro para executar múltiplas vezes.
--
-- DEPENDÊNCIAS: 001_core_tables.sql (profiles, financial_institutions)
-- TABELAS: accounts, cards, categories, transactions,
--          budgets, financial_goals, debts
-- PRÓXIMO ARQUIVO: 003_investment_tables.sql
-- ============================================================

-- ── accounts ─────────────────────────────────────────────────
-- Contas bancárias, poupança e carteiras digitais.
-- Equivalente ao antigo `contas` (renomeado; dados preservados).

CREATE TABLE IF NOT EXISTS accounts (
    id                       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    financial_institution_id UUID          REFERENCES financial_institutions(id),
    name                     VARCHAR(100)  NOT NULL,
    type                     VARCHAR(50)   NOT NULL
                               CHECK (type IN ('checking','savings','digital_wallet','investment')),
    initial_balance          NUMERIC(15,2) NOT NULL DEFAULT 0,
    currency                 CHAR(3)       NOT NULL DEFAULT 'BRL',
    active                   BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at               TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  accounts                          IS 'Contas bancárias, poupança e carteiras digitais. Equiv. ao antigo `contas`.';
COMMENT ON COLUMN accounts.user_id                  IS 'Proprietário. Toda query filtra WHERE user_id = :owner_id.';
COMMENT ON COLUMN accounts.financial_institution_id IS 'Nullable — migração histórica pode não ter banco vinculado.';
COMMENT ON COLUMN accounts.initial_balance          IS 'Saldo na criação. Saldo atual = initial_balance + SUM(transactions.amount).';

-- ── cards ─────────────────────────────────────────────────────
-- Cartões de crédito com controle de limite, vencimento e fechamento.
-- Tabela nova — sem dados de migração disponíveis.

CREATE TABLE IF NOT EXISTS cards (
    id                       UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    account_id               UUID          REFERENCES accounts(id),
    financial_institution_id UUID          REFERENCES financial_institutions(id),
    name                     VARCHAR(100)  NOT NULL,
    brand                    VARCHAR(50)   CHECK (brand IN ('visa','mastercard','elo','amex','hipercard')),
    credit_limit             NUMERIC(15,2),
    due_day                  SMALLINT      CHECK (due_day  BETWEEN 1 AND 31),
    close_day                SMALLINT      CHECK (close_day BETWEEN 1 AND 31),
    active                   BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at               TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  cards            IS 'Cartões de crédito. Tabela nova — população manual.';
COMMENT ON COLUMN cards.account_id IS 'Conta vinculada para débito automático da fatura. Opcional.';
COMMENT ON COLUMN cards.due_day    IS 'Dia do vencimento da fatura (1–31).';
COMMENT ON COLUMN cards.close_day  IS 'Dia de fechamento da fatura (1–31).';

-- ── categories ───────────────────────────────────────────────
-- Categorias de transação, hierárquicas (auto-referência via parent_id).
-- user_id = NULL indica categoria do sistema (visível a todos).

CREATE TABLE IF NOT EXISTS categories (
    id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id   UUID         REFERENCES profiles(id) ON DELETE CASCADE,
    name      VARCHAR(100) NOT NULL,
    type      VARCHAR(20)  NOT NULL CHECK (type IN ('income','expense','transfer')),
    icon      VARCHAR(50),
    color     CHAR(7),
    parent_id UUID         REFERENCES categories(id)
);

COMMENT ON TABLE  categories         IS 'Categorias de transação hierárquicas. Equiv. ao antigo `categorias`.';
COMMENT ON COLUMN categories.user_id IS 'NULL = categoria padrão do sistema (sem RLS — visível a todos).';
COMMENT ON COLUMN categories.color   IS 'Hex color no formato #RRGGBB.';

-- ── transactions ─────────────────────────────────────────────
-- Receitas, despesas e transferências pessoais.
-- REGRA: amount positivo = receita; amount negativo = despesa.
-- Saldo = SUM(amount) WHERE user_id = :owner_id AND status = 'settled'.

CREATE TABLE IF NOT EXISTS transactions (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    account_id          UUID          NOT NULL REFERENCES accounts(id),
    card_id             UUID          REFERENCES cards(id),
    category_id         UUID          REFERENCES categories(id),
    description         VARCHAR(255)  NOT NULL,
    amount              NUMERIC(15,2) NOT NULL,
    due_date            DATE          NOT NULL,
    payment_date        DATE,
    type                VARCHAR(20)   NOT NULL CHECK (type IN ('income','expense','transfer')),
    status              VARCHAR(20)   NOT NULL DEFAULT 'settled'
                          CHECK (status IN ('pending','settled','cancelled')),
    recurring           BOOLEAN       NOT NULL DEFAULT FALSE,
    installment_current SMALLINT,
    installment_total   SMALLINT,
    installment_group   UUID,
    source              VARCHAR(50)   NOT NULL DEFAULT 'manual'
                          CHECK (source IN ('manual','import','csv','open_banking')),
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  transactions               IS 'Receitas, despesas e transferências. Equiv. ao antigo `transacoes`.';
COMMENT ON COLUMN transactions.amount        IS 'SIGNED: receita > 0, despesa < 0. Saldo = SUM(amount).';
COMMENT ON COLUMN transactions.installment_group IS 'UUID que agrupa parcelas do mesmo parcelamento.';

-- ── budgets ──────────────────────────────────────────────────
-- Orçamentos mensais por categoria.
-- month_year deve ser sempre o primeiro dia do mês (ex: 2026-05-01).

CREATE TABLE IF NOT EXISTS budgets (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    category_id  UUID          NOT NULL REFERENCES categories(id),
    month_year   DATE          NOT NULL,
    amount_limit NUMERIC(15,2) NOT NULL,
    UNIQUE (user_id, category_id, month_year)
);

COMMENT ON TABLE  budgets            IS 'Orçamentos mensais por categoria. Equiv. ao antigo `orcamentos`.';
COMMENT ON COLUMN budgets.month_year IS 'Primeiro dia do mês. Ex: DATE 2026-05-01 = maio/2026.';

-- ── financial_goals ──────────────────────────────────────────
-- Metas financeiras com acompanhamento de progresso.

CREATE TABLE IF NOT EXISTS financial_goals (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name           VARCHAR(150)  NOT NULL,
    type           VARCHAR(50)   CHECK (type IN ('emergency_fund','travel','purchase','debt_payment','other')),
    target_amount  NUMERIC(15,2) NOT NULL,
    current_amount NUMERIC(15,2) NOT NULL DEFAULT 0,
    deadline       DATE,
    active         BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  financial_goals                IS 'Metas financeiras. Equiv. ao antigo `metas`.';
COMMENT ON COLUMN financial_goals.current_amount IS 'Valor acumulado. Atualizado manualmente ou por ETL.';

-- ── debts ────────────────────────────────────────────────────
-- Dívidas, financiamentos e parcelamentos de longo prazo.
-- Tabela nova — sem dados de migração disponíveis.

CREATE TABLE IF NOT EXISTS debts (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name                VARCHAR(200)  NOT NULL,
    type                VARCHAR(50)   NOT NULL
                          CHECK (type IN ('loan','financing','credit_card_revolving','installment')),
    original_amount     NUMERIC(15,2),
    outstanding_balance NUMERIC(15,2) NOT NULL,
    interest_rate       NUMERIC(8,4),
    start_date          DATE,
    end_date            DATE,
    total_installments  SMALLINT,
    paid_installments   SMALLINT      DEFAULT 0,
    active              BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  debts                     IS 'Dívidas e financiamentos. Tabela nova — população manual.';
COMMENT ON COLUMN debts.outstanding_balance IS 'Saldo devedor atual. Atualizado manualmente.';
COMMENT ON COLUMN debts.interest_rate       IS 'Taxa de juros em % ao mês.';

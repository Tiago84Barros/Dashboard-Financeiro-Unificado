-- ============================================================
-- 001_core_tables.sql
-- Tabelas base: identidade e instituições financeiras
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE, DROP SCHEMA.
--   Todos os CREATE usam IF NOT EXISTS.
--   Seguro para executar múltiplas vezes.
--
-- DEPENDÊNCIAS: nenhuma (tabelas raiz do schema).
-- PRÓXIMO ARQUIVO: 002_financial_tables.sql
-- ============================================================

-- ── profiles ─────────────────────────────────────────────────
-- Perfil do usuário proprietário. App 4 é single-user:
-- deve existir exatamente 1 registro com active = TRUE.
-- O UUID desta linha é o OWNER_USER_ID do .env / Streamlit Secrets.

CREATE TABLE IF NOT EXISTS profiles (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(150) NOT NULL,
    email         VARCHAR(255) NOT NULL UNIQUE,
    password_hash TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    active        BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE  profiles               IS 'Perfil do usuário proprietário. Single-user: 1 registro ativo.';
COMMENT ON COLUMN profiles.id            IS 'UUID imutável. Referenciar em OWNER_USER_ID no .env / Streamlit Secrets.';
COMMENT ON COLUMN profiles.email         IS 'E-mail único. Imutável após criação.';
COMMENT ON COLUMN profiles.password_hash IS 'Hash SHA-256 ou bcrypt. Nunca armazenar senha em texto claro.';
COMMENT ON COLUMN profiles.active        IS 'TRUE = perfil ativo. FALSE = soft-delete.';

-- ── financial_institutions ───────────────────────────────────
-- Bancos, corretoras e fintechs referenciados por contas e cartões.
-- Dados de referência — sem RLS, sem dado pessoal.

CREATE TABLE IF NOT EXISTS financial_institutions (
    id        UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name      VARCHAR(200) NOT NULL,
    type      VARCHAR(50)  NOT NULL CHECK (type IN ('bank','broker','fintech','insurance')),
    cnpj      VARCHAR(18)  UNIQUE,
    bank_code CHAR(3),
    website   VARCHAR(255),
    active    BOOLEAN      NOT NULL DEFAULT TRUE
);

COMMENT ON TABLE  financial_institutions           IS 'Instituições financeiras (bancos, corretoras, fintechs). Dados de referência — sem RLS.';
COMMENT ON COLUMN financial_institutions.type      IS 'Classificação: bank, broker, fintech, insurance.';
COMMENT ON COLUMN financial_institutions.cnpj      IS 'CNPJ formato XX.XXX.XXX/XXXX-XX. UNIQUE quando preenchido.';
COMMENT ON COLUMN financial_institutions.bank_code IS 'Código COMPE 3 dígitos (ex: 341=Itaú, 033=Santander, 260=Nubank).';

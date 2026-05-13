-- ============================================================
-- 003_investment_tables.sql
-- Tabelas de investimentos e dados de mercado
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE, DROP SCHEMA.
--   Todos os CREATE usam IF NOT EXISTS.
--   Seguro para executar múltiplas vezes.
--
-- DEPENDÊNCIAS: 001_core_tables.sql (profiles)
-- TABELAS: assets, portfolios, portfolio_positions,
--          investment_transactions, dividends,
--          asset_quotes, benchmarks, benchmark_quotes
-- PRÓXIMO ARQUIVO: 004_import_migration_tables.sql
-- ============================================================

-- ── assets ───────────────────────────────────────────────────
-- Cadastro de ativos negociáveis: ações, FIIs, ETFs, renda fixa, cripto.
-- Sem RLS — dados de mercado, não pessoais.
-- Equivalente ao antigo `ativos`.

CREATE TABLE IF NOT EXISTS assets (
    id       UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker   VARCHAR(20)  NOT NULL UNIQUE,
    name     VARCHAR(200) NOT NULL,
    class    VARCHAR(50)  NOT NULL
               CHECK (class IN ('stock','reit','etf','fixed_income','crypto','other')),
    sector   VARCHAR(100),
    currency CHAR(3)      NOT NULL DEFAULT 'BRL',
    exchange VARCHAR(20)
);

COMMENT ON TABLE  assets          IS 'Cadastro de ativos negociáveis. Sem RLS — dados de mercado. Equiv. ao antigo `ativos`.';
COMMENT ON COLUMN assets.ticker   IS 'Código de negociação único (ex: PETR4, BTCUSD, AAPL). Chave de lookup em APIs.';
COMMENT ON COLUMN assets.class    IS 'Classe: stock (ação), reit (FII), etf, fixed_income, crypto, other.';
COMMENT ON COLUMN assets.exchange IS 'Bolsa: B3, NASDAQ, NYSE, BINANCE, etc.';

-- ── portfolios ───────────────────────────────────────────────
-- Carteiras de investimento nomeadas.
-- Tabela nova — App 2 (SQLite) não tinha carteiras nomeadas.

CREATE TABLE IF NOT EXISTS portfolios (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID         NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    name        VARCHAR(150) NOT NULL,
    description TEXT,
    type        VARCHAR(50)  CHECK (type IN ('personal','pension','speculative','other')),
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE portfolios IS 'Carteiras de investimento. Tabela nova — população manual. Migração App2 cria carteira padrão.';

-- ── portfolio_positions ──────────────────────────────────────
-- Posição consolidada atual por ativo em cada carteira.
-- Calculada a partir de investment_transactions pelo ETL.
-- Tabela nova — performance: evita recalcular N operações a cada consulta.

CREATE TABLE IF NOT EXISTS portfolio_positions (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    portfolio_id   UUID          NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    asset_id       UUID          NOT NULL REFERENCES assets(id),
    quantity       NUMERIC(18,8) NOT NULL,
    average_price  NUMERIC(15,6) NOT NULL,
    total_invested NUMERIC(15,2) NOT NULL,
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (portfolio_id, asset_id)
);

COMMENT ON TABLE  portfolio_positions               IS 'Posição atual por ativo em cada carteira. Calculada pelo ETL.';
COMMENT ON COLUMN portfolio_positions.average_price IS 'Preço médio de aquisição. Recalculado após cada importação.';
COMMENT ON COLUMN portfolio_positions.updated_at    IS 'Última atualização. Permite saber se a posição está defasada.';

-- ── investment_transactions ──────────────────────────────────
-- Operações de compra e venda de ativos.
-- Equivalente ao antigo `operacoes`.
-- portfolio_id é NULLABLE: compatibilidade com dados históricos do App 2.

CREATE TABLE IF NOT EXISTS investment_transactions (
    id               UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    asset_id         UUID          NOT NULL REFERENCES assets(id),
    portfolio_id     UUID          REFERENCES portfolios(id),
    type             VARCHAR(10)   NOT NULL CHECK (type IN ('buy','sell')),
    quantity         NUMERIC(18,8) NOT NULL,
    unit_price       NUMERIC(15,6) NOT NULL,
    fees             NUMERIC(15,2) NOT NULL DEFAULT 0,
    transaction_date DATE          NOT NULL,
    broker           VARCHAR(100),
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  investment_transactions              IS 'Operações de compra/venda de ativos. Equiv. ao antigo `operacoes`.';
COMMENT ON COLUMN investment_transactions.portfolio_id IS 'Nullable — dados históricos do App2 não tinham carteira nomeada.';
COMMENT ON COLUMN investment_transactions.fees         IS 'Taxas de corretagem, emolumentos, custódia, etc.';
COMMENT ON COLUMN investment_transactions.unit_price   IS 'Preço unitário na data da operação. 6 casas decimais para ativos fracionários.';

-- ── dividends ────────────────────────────────────────────────
-- Proventos recebidos: dividendos, JCP, rendimentos de FII, amortizações.
-- Equivalente ao antigo `proventos`.

CREATE TABLE IF NOT EXISTS dividends (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    asset_id        UUID          NOT NULL REFERENCES assets(id),
    type            VARCHAR(30)   NOT NULL
                      CHECK (type IN ('dividend','jcp','reit_income','amortization','other')),
    amount_per_unit NUMERIC(15,6) NOT NULL,
    quantity        NUMERIC(18,8) NOT NULL,
    total_amount    NUMERIC(15,2) NOT NULL,
    ex_date         DATE,
    payment_date    DATE          NOT NULL
);

COMMENT ON TABLE  dividends              IS 'Proventos recebidos. Equiv. ao antigo `proventos`.';
COMMENT ON COLUMN dividends.ex_date      IS 'Data-com (corte para ter direito ao provento). Opcional.';
COMMENT ON COLUMN dividends.quantity     IS 'Quantidade em custódia na data-com.';
COMMENT ON COLUMN dividends.total_amount IS 'amount_per_unit × quantity.';

-- ── asset_quotes ─────────────────────────────────────────────
-- Série histórica de preços de ativos (OHLCV).
-- PK composta: (asset_id, timestamp) — sem ID separado.
-- Sem RLS — dados de mercado.
-- Equivalente ao antigo `cotacoes`.

CREATE TABLE IF NOT EXISTS asset_quotes (
    asset_id  UUID          NOT NULL REFERENCES assets(id),
    timestamp TIMESTAMPTZ   NOT NULL,
    open      NUMERIC(15,6),
    high      NUMERIC(15,6),
    low       NUMERIC(15,6),
    close     NUMERIC(15,6) NOT NULL,
    volume    NUMERIC(20,2),
    PRIMARY KEY (asset_id, timestamp)
);

COMMENT ON TABLE  asset_quotes IS 'Série histórica de preços (OHLCV). Sem RLS — dados de mercado. Equiv. ao antigo `cotacoes`.';

-- ── benchmarks ───────────────────────────────────────────────
-- Índices e taxas de referência para comparação de rentabilidade.
-- Sem RLS — dados de referência públicos.
-- Seed em 008_seed_reference_data.sql.

CREATE TABLE IF NOT EXISTS benchmarks (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(20)  NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL,
    type        VARCHAR(50)  CHECK (type IN ('index','rate')),
    frequency   VARCHAR(20)  CHECK (frequency IN ('daily','monthly')),
    description TEXT
);

COMMENT ON TABLE  benchmarks      IS 'Índices e taxas de referência (IBOVESPA, CDI, IPCA, SELIC, IFIX). Seed em 008.';
COMMENT ON COLUMN benchmarks.code IS 'Código único. Usado para lookup em yfinance/BCB.';

-- ── benchmark_quotes ─────────────────────────────────────────
-- Série histórica dos benchmarks (variação diária ou mensal).
-- PK composta: (benchmark_id, date).
-- Sem RLS — dados de referência.

CREATE TABLE IF NOT EXISTS benchmark_quotes (
    benchmark_id     UUID          NOT NULL REFERENCES benchmarks(id),
    date             DATE          NOT NULL,
    value            NUMERIC(15,8) NOT NULL,
    daily_change_pct NUMERIC(8,6),
    PRIMARY KEY (benchmark_id, date)
);

COMMENT ON TABLE  benchmark_quotes IS 'Série histórica dos benchmarks. Populada via API (yfinance/BCB) na Fase 4.7.';

-- ============================================================
-- 013_market_brapi_schema.sql
-- Nova arquitetura de dados de mercado (fonte: BRAPI Pro)
-- Banco: Dashboard Financeiro Unificado (Supabase)
-- Criado: 2026-06-22
--
-- OBJETIVO:
--   Base limpa, auditável e escalável para dados financeiros, com a BRAPI Pro
--   como fonte principal. Criada em um SCHEMA dedicado `market`, ISOLADA do
--   legado em `public` (que NÃO é alterado/removido aqui).
--
-- SEGURANÇA:
--   Não contém DROP/TRUNCATE/DELETE. Todos os objetos usam IF NOT EXISTS.
--   Idempotente — seguro para reexecutar. Não toca em tabelas existentes.
--
-- UPSERT SEGURO:
--   Cada tabela tem chave natural única (PK composta ou UNIQUE) para
--   INSERT ... ON CONFLICT (...) DO UPDATE. Ver comentários por tabela.
--
-- ORDEM DE CARGA (respeita FKs): companies -> assets -> (prices, statements,
--   dividends, calculated_metrics). macro_indicators e *_raw_payloads/_logs
--   são independentes.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS market;
COMMENT ON SCHEMA market IS 'Dados de mercado (BRAPI Pro) — arquitetura limpa que substitui o legado de múltiplos/demonstrações em public.';

-- Função de updated_at (idempotente)
CREATE OR REPLACE FUNCTION market.set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ── 1. brapi_raw_payloads ───────────────────────────────────
-- Auditoria das respostas cruas da API (rastreabilidade total).
CREATE TABLE IF NOT EXISTS market.brapi_raw_payloads (
    id             BIGSERIAL    PRIMARY KEY,
    ticker         TEXT,
    endpoint       TEXT         NOT NULL,
    payload_json   JSONB,
    fetched_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    source         TEXT         NOT NULL DEFAULT 'brapi.dev',
    request_status TEXT         NOT NULL DEFAULT 'success'
                      CHECK (request_status IN ('success','failed','rate_limited','empty')),
    error_message  TEXT
);
COMMENT ON TABLE market.brapi_raw_payloads IS 'Log append-only das respostas da BRAPI (por ticker/endpoint) para auditoria e reprocessamento.';
CREATE INDEX IF NOT EXISTS idx_brapi_raw_ticker   ON market.brapi_raw_payloads (ticker);
CREATE INDEX IF NOT EXISTS idx_brapi_raw_endpoint ON market.brapi_raw_payloads (endpoint, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_brapi_raw_fetched  ON market.brapi_raw_payloads (fetched_at DESC);


-- ── 2. companies ────────────────────────────────────────────
-- Empresa (emissor). Chave natural: codigo_cvm (preferida) ou cnpj.
CREATE TABLE IF NOT EXISTS market.companies (
    id          BIGSERIAL   PRIMARY KEY,
    codigo_cvm  INTEGER,                    -- chave natural CVM (estável)
    name        TEXT        NOT NULL,
    cnpj        TEXT,
    sector      TEXT,
    subsector   TEXT,
    segment     TEXT,
    website     TEXT,
    description TEXT,
    logo_url    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market.companies IS 'Empresas/emissores. Upsert por codigo_cvm (preferencial) ou cnpj.';
-- UNIQUE cheio (NULLs são distintos no Postgres → múltiplos NULL permitidos;
-- e ON CONFLICT (col) casa com índice não-parcial — necessário p/ o upsert).
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_codigo_cvm ON market.companies (codigo_cvm);
CREATE UNIQUE INDEX IF NOT EXISTS uq_companies_cnpj       ON market.companies (cnpj);
DROP TRIGGER IF EXISTS trg_companies_updated ON market.companies;
CREATE TRIGGER trg_companies_updated BEFORE UPDATE ON market.companies
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 3. assets ───────────────────────────────────────────────
-- Ativo negociável (ticker). FK -> companies. Upsert por ticker.
CREATE TABLE IF NOT EXISTS market.assets (
    id          BIGSERIAL   PRIMARY KEY,
    company_id  BIGINT      REFERENCES market.companies(id) ON DELETE SET NULL,
    ticker      TEXT        NOT NULL UNIQUE,
    asset_type  TEXT        NOT NULL DEFAULT 'stock'
                   CHECK (asset_type IN ('stock','fii','bdr','etf','unit','other')),
    exchange    TEXT        NOT NULL DEFAULT 'B3',
    currency    TEXT        NOT NULL DEFAULT 'BRL',
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market.assets IS 'Ativos B3 (ticker). Upsert por ticker. Seed antes das tabelas-fato.';
CREATE INDEX IF NOT EXISTS idx_assets_company ON market.assets (company_id);
CREATE INDEX IF NOT EXISTS idx_assets_active  ON market.assets (is_active);
DROP TRIGGER IF EXISTS trg_assets_updated ON market.assets;
CREATE TRIGGER trg_assets_updated BEFORE UPDATE ON market.assets
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 4. historical_prices ────────────────────────────────────
-- Série OHLCV. PK natural (ticker, date) => upsert ON CONFLICT (ticker,date).
CREATE TABLE IF NOT EXISTS market.historical_prices (
    ticker         TEXT        NOT NULL,
    date           DATE        NOT NULL,
    open           NUMERIC(18,6),
    high           NUMERIC(18,6),
    low            NUMERIC(18,6),
    close          NUMERIC(18,6),
    adjusted_close NUMERIC(18,6),
    volume         BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, date),
    CONSTRAINT fk_prices_asset FOREIGN KEY (ticker) REFERENCES market.assets(ticker)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE market.historical_prices IS 'OHLCV diário/mensal. Upsert por (ticker,date).';
CREATE INDEX IF NOT EXISTS idx_prices_date ON market.historical_prices (date);


-- ── helper: período/ano/trimestre p/ demonstrações ──────────
-- period: 'annual' | 'quarterly'; quarter: 0 (anual) ou 1..4.

-- ── 5. income_statements ────────────────────────────────────
CREATE TABLE IF NOT EXISTS market.income_statements (
    id           BIGSERIAL   PRIMARY KEY,
    ticker       TEXT        NOT NULL,
    period       TEXT        NOT NULL DEFAULT 'annual' CHECK (period IN ('annual','quarterly')),
    year         INTEGER     NOT NULL,
    quarter      SMALLINT    NOT NULL DEFAULT 0 CHECK (quarter BETWEEN 0 AND 4),
    revenue      NUMERIC(20,2),
    gross_profit NUMERIC(20,2),
    ebit         NUMERIC(20,2),
    ebitda       NUMERIC(20,2),
    net_income   NUMERIC(20,2),
    eps          NUMERIC(20,6),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_income UNIQUE (ticker, period, year, quarter),
    CONSTRAINT fk_income_asset FOREIGN KEY (ticker) REFERENCES market.assets(ticker)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
-- Idempotente p/ bancos já criados: LPA por ano (basicEarningsPerCommonShare/1000),
-- usado p/ derivar ações históricas e tornar o valuation anual exato.
ALTER TABLE market.income_statements ADD COLUMN IF NOT EXISTS eps NUMERIC(20,6);
COMMENT ON TABLE market.income_statements IS 'DRE. Upsert por (ticker,period,year,quarter); quarter=0 => anual.';
CREATE INDEX IF NOT EXISTS idx_income_ticker_year ON market.income_statements (ticker, year);
DROP TRIGGER IF EXISTS trg_income_updated ON market.income_statements;
CREATE TRIGGER trg_income_updated BEFORE UPDATE ON market.income_statements
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 6. balance_sheets ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS market.balance_sheets (
    id                BIGSERIAL   PRIMARY KEY,
    ticker            TEXT        NOT NULL,
    period            TEXT        NOT NULL DEFAULT 'annual' CHECK (period IN ('annual','quarterly')),
    year              INTEGER     NOT NULL,
    quarter           SMALLINT    NOT NULL DEFAULT 0 CHECK (quarter BETWEEN 0 AND 4),
    total_assets      NUMERIC(20,2),
    total_liabilities NUMERIC(20,2),
    equity            NUMERIC(20,2),
    cash              NUMERIC(20,2),
    gross_debt        NUMERIC(20,2),
    net_debt          NUMERIC(20,2),
    current_assets    NUMERIC(20,2),
    current_liabilities NUMERIC(20,2),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_balance UNIQUE (ticker, period, year, quarter),
    CONSTRAINT fk_balance_asset FOREIGN KEY (ticker) REFERENCES market.assets(ticker)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
-- Idempotente p/ bancos já criados antes destas colunas (ativo/passivo circulante
-- → Liquidez_Corrente). PostgreSQL suporta ADD COLUMN IF NOT EXISTS.
ALTER TABLE market.balance_sheets ADD COLUMN IF NOT EXISTS current_assets      NUMERIC(20,2);
ALTER TABLE market.balance_sheets ADD COLUMN IF NOT EXISTS current_liabilities NUMERIC(20,2);
COMMENT ON TABLE market.balance_sheets IS 'Balanço patrimonial. Upsert por (ticker,period,year,quarter).';
CREATE INDEX IF NOT EXISTS idx_balance_ticker_year ON market.balance_sheets (ticker, year);
DROP TRIGGER IF EXISTS trg_balance_updated ON market.balance_sheets;
CREATE TRIGGER trg_balance_updated BEFORE UPDATE ON market.balance_sheets
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 7. cash_flow_statements ─────────────────────────────────
CREATE TABLE IF NOT EXISTS market.cash_flow_statements (
    id                   BIGSERIAL   PRIMARY KEY,
    ticker               TEXT        NOT NULL,
    period               TEXT        NOT NULL DEFAULT 'annual' CHECK (period IN ('annual','quarterly')),
    year                 INTEGER     NOT NULL,
    quarter              SMALLINT    NOT NULL DEFAULT 0 CHECK (quarter BETWEEN 0 AND 4),
    operating_cash_flow  NUMERIC(20,2),
    investing_cash_flow  NUMERIC(20,2),
    financing_cash_flow  NUMERIC(20,2),
    capex                NUMERIC(20,2),
    free_cash_flow       NUMERIC(20,2),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_cashflow UNIQUE (ticker, period, year, quarter),
    CONSTRAINT fk_cashflow_asset FOREIGN KEY (ticker) REFERENCES market.assets(ticker)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE market.cash_flow_statements IS 'DFC. Upsert por (ticker,period,year,quarter).';
CREATE INDEX IF NOT EXISTS idx_cashflow_ticker_year ON market.cash_flow_statements (ticker, year);
DROP TRIGGER IF EXISTS trg_cashflow_updated ON market.cash_flow_statements;
CREATE TRIGGER trg_cashflow_updated BEFORE UPDATE ON market.cash_flow_statements
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 8. dividends ────────────────────────────────────────────
-- event_date GENERATED = COALESCE(payment_date, ex_date) garante dedup com nulos.
CREATE TABLE IF NOT EXISTS market.dividends (
    id           BIGSERIAL   PRIMARY KEY,
    ticker       TEXT        NOT NULL,
    payment_date DATE,
    ex_date      DATE,
    amount       NUMERIC(18,6) NOT NULL,
    type         TEXT        NOT NULL DEFAULT 'DIVIDENDO',
    source       TEXT        NOT NULL DEFAULT 'brapi.dev',
    event_date   DATE        GENERATED ALWAYS AS (COALESCE(payment_date, ex_date)) STORED,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dividends UNIQUE (ticker, event_date, type, amount),
    CONSTRAINT fk_dividends_asset FOREIGN KEY (ticker) REFERENCES market.assets(ticker)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE market.dividends IS 'Proventos (dividendos/JCP/rendimento). Upsert por (ticker,event_date,type,amount).';
CREATE INDEX IF NOT EXISTS idx_dividends_ticker_date ON market.dividends (ticker, event_date DESC);
DROP TRIGGER IF EXISTS trg_dividends_updated ON market.dividends;
CREATE TRIGGER trg_dividends_updated BEFORE UPDATE ON market.dividends
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 9. macro_indicators ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS market.macro_indicators (
    id         BIGSERIAL   PRIMARY KEY,
    indicator  TEXT        NOT NULL,        -- 'SELIC','IPCA','CAMBIO_USD','CDI',...
    date       DATE        NOT NULL,
    value      NUMERIC(20,6) NOT NULL,
    source     TEXT        NOT NULL DEFAULT 'brapi.dev',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_macro UNIQUE (indicator, date)
);
COMMENT ON TABLE market.macro_indicators IS 'Indicadores macro (série temporal). Upsert por (indicator,date).';
CREATE INDEX IF NOT EXISTS idx_macro_indicator_date ON market.macro_indicators (indicator, date DESC);
DROP TRIGGER IF EXISTS trg_macro_updated ON market.macro_indicators;
CREATE TRIGGER trg_macro_updated BEFORE UPDATE ON market.macro_indicators
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 10. calculated_metrics ──────────────────────────────────
-- Indicadores CALCULADOS pelo app (P/L, P/VP, ROE, margens, scores...).
CREATE TABLE IF NOT EXISTS market.calculated_metrics (
    id                 BIGSERIAL   PRIMARY KEY,
    ticker             TEXT        NOT NULL,
    period             TEXT        NOT NULL DEFAULT 'annual' CHECK (period IN ('annual','quarterly','ttm','spot')),
    year               INTEGER     NOT NULL DEFAULT 0,
    quarter            SMALLINT    NOT NULL DEFAULT 0 CHECK (quarter BETWEEN 0 AND 4),
    metric_name        TEXT        NOT NULL,   -- 'P/L','P/VP','ROE','DY',...
    metric_value       NUMERIC(24,8),
    calculation_method TEXT,                   -- fórmula/origem do cálculo
    source             TEXT,                   -- fonte(s) do insumo
    confidence_score   NUMERIC(5,2) CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 100)),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_calcmetric UNIQUE (ticker, period, year, quarter, metric_name),
    CONSTRAINT fk_calcmetric_asset FOREIGN KEY (ticker) REFERENCES market.assets(ticker)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED
);
COMMENT ON TABLE market.calculated_metrics IS 'Métricas derivadas/calculadas, com método, fonte e score de confiança. Upsert por (ticker,period,year,quarter,metric_name).';
CREATE INDEX IF NOT EXISTS idx_calcmetric_name  ON market.calculated_metrics (metric_name);
CREATE INDEX IF NOT EXISTS idx_calcmetric_tk    ON market.calculated_metrics (ticker, metric_name);
CREATE INDEX IF NOT EXISTS idx_calcmetric_conf  ON market.calculated_metrics (confidence_score);
DROP TRIGGER IF EXISTS trg_calcmetric_updated ON market.calculated_metrics;
CREATE TRIGGER trg_calcmetric_updated BEFORE UPDATE ON market.calculated_metrics
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();


-- ── 11. data_quality_logs ───────────────────────────────────
-- Histórico de issues/correções (append-only) da nova base.
CREATE TABLE IF NOT EXISTS market.data_quality_logs (
    id          BIGSERIAL   PRIMARY KEY,
    ticker      TEXT,
    table_name  TEXT        NOT NULL,
    field_name  TEXT,
    issue_type  TEXT        NOT NULL,   -- 'missing','outlier','divergence','filled','corrected',...
    old_value   TEXT,
    new_value   TEXT,
    severity    TEXT        NOT NULL DEFAULT 'info'
                   CHECK (severity IN ('info','warn','critical')),
    source      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market.data_quality_logs IS 'Log append-only de qualidade/correções da base market.*.';
CREATE INDEX IF NOT EXISTS idx_dqlog_ticker   ON market.data_quality_logs (ticker);
CREATE INDEX IF NOT EXISTS idx_dqlog_table    ON market.data_quality_logs (table_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dqlog_severity ON market.data_quality_logs (severity, created_at DESC);

-- ── 12. ticker_cvm ──────────────────────────────────────────
-- Mapa ticker -> codigo_cvm (muitos tickers por empresa, ex.: PETR3/PETR4).
-- Origem: cadastro oficial CVM (cad_cia_aberta + FCA). Substitui o legado
-- public.cvm_to_ticker (que tinha UNIQUE em CVM, 1 ticker por empresa).
CREATE TABLE IF NOT EXISTS market.ticker_cvm (
    ticker      TEXT        PRIMARY KEY,
    codigo_cvm  INTEGER     NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market.ticker_cvm IS 'Mapa ticker->codigo_cvm (CVM cad+FCA). Upsert por ticker.';
CREATE INDEX IF NOT EXISTS idx_ticker_cvm_cod ON market.ticker_cvm (codigo_cvm);

-- ============================================================
-- FIM 013. Próximo: 014_legacy_isolation.sql (marca o legado em public).
-- ============================================================

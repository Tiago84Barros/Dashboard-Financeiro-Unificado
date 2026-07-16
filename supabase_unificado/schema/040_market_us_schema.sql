-- ============================================================
-- 040_market_us_schema.sql
-- Empresas Americanas — schema DEDICADO `market_us` (isolado de market.*/public.*).
-- Fonte primária: Financial Modeling Prep (FMP). Destino: warehouse local
-- (Postgres 17 em warehouse/docker-compose.yml). NÃO destinado ao Supabase Free
-- para os históricos pesados (ver warehouse/tables_armazem.txt).
--
-- OBJETIVO:
--   Base auditável, point-in-time e offline-first para análise de empresas dos
--   EUA. Namespace 100% separado do B3/FII (regra: "não misturar sem namespace
--   claro"). Espelha o padrão de market.* (013_market_brapi_schema.sql):
--   idempotente, chave natural + ON CONFLICT, trigger set_updated_at.
--
-- IDENTIDADE PERMANENTE:
--   A empresa é identificada por CIK (preferencial) — NUNCA só pelo ticker, que
--   é reutilizado/renomeado. `assets` guarda o símbolo negociável; o histórico
--   de símbolos vive em `ticker_aliases`. Não apagar histórico ao trocar ticker.
--
-- POINT-IN-TIME:
--   Toda tabela-fato temporal carrega reference_date (fim do período fiscal),
--   published_date (data do filing) e available_at (quando o dado poderia ser
--   conhecido). Backtests devem filtrar por available_at, nunca por ingested_at.
--
-- SEGURANÇA:
--   Sem DROP/TRUNCATE/DELETE. Tudo IF NOT EXISTS. Seguro para reexecutar.
-- ============================================================

CREATE SCHEMA IF NOT EXISTS market_us;
COMMENT ON SCHEMA market_us IS 'Empresas Americanas (FMP) — warehouse local, isolado de market.* (B3/FII).';

-- Função de updated_at (idempotente, no schema dedicado).
CREATE OR REPLACE FUNCTION market_us.set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ── 1. exchanges ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.exchanges (
    code        TEXT        PRIMARY KEY,          -- 'NYSE','NASDAQ','AMEX'
    name        TEXT,
    mic         TEXT,                             -- ISO 10383 Market Identifier Code
    country     TEXT        NOT NULL DEFAULT 'US',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market_us.exchanges IS 'Bolsas suportadas (NYSE, NASDAQ, NYSE American).';


-- ── 2. companies (IDENTIDADE PERMANENTE) ────────────────────
-- Chave natural: cik (preferida). isin/cusip guardados quando licenciados.
CREATE TABLE IF NOT EXISTS market_us.companies (
    id            BIGSERIAL   PRIMARY KEY,
    cik           TEXT,                           -- SEC Central Index Key (estável)
    isin          TEXT,
    cusip         TEXT,
    name          TEXT        NOT NULL,
    sector        TEXT,
    industry      TEXT,
    country       TEXT,
    currency      TEXT        NOT NULL DEFAULT 'USD',
    description    TEXT,
    website       TEXT,
    ceo           TEXT,
    employees      INTEGER,
    ipo_date      DATE,
    is_reit       BOOLEAN     NOT NULL DEFAULT FALSE,   -- métricas específicas (FFO/AFFO)
    is_adr        BOOLEAN     NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,    -- FALSE quando deslistada
    source        TEXT        NOT NULL DEFAULT 'fmp',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market_us.companies IS 'Empresas EUA. Identidade permanente = cik. Upsert por cik.';
-- NULLs distintos no Postgres → múltiplos NULL permitidos; ON CONFLICT (cik) casa
-- com índice não-parcial (necessário p/ upsert).
CREATE UNIQUE INDEX IF NOT EXISTS uq_us_companies_cik   ON market_us.companies (cik);
CREATE INDEX        IF NOT EXISTS idx_us_companies_sec  ON market_us.companies (sector, industry);
CREATE INDEX        IF NOT EXISTS idx_us_companies_act  ON market_us.companies (is_active);
DROP TRIGGER IF EXISTS trg_us_companies_updated ON market_us.companies;
CREATE TRIGGER trg_us_companies_updated BEFORE UPDATE ON market_us.companies
    FOR EACH ROW EXECUTE FUNCTION market_us.set_updated_at();


-- ── 3. assets (ticker negociável) ───────────────────────────
-- Símbolo pode ser reutilizado; a identidade é company_id. Upsert (symbol,exchange).
CREATE TABLE IF NOT EXISTS market_us.assets (
    id            BIGSERIAL   PRIMARY KEY,
    company_id    BIGINT      REFERENCES market_us.companies(id) ON DELETE SET NULL,
    symbol        TEXT        NOT NULL,
    exchange      TEXT        NOT NULL DEFAULT 'NASDAQ',
    security_type TEXT        NOT NULL DEFAULT 'common'
                     CHECK (security_type IN ('common','class','adr','reit','etf',
                                              'fund','spac','unit','preferred','other')),
    currency      TEXT        NOT NULL DEFAULT 'USD',
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    is_delisted   BOOLEAN     NOT NULL DEFAULT FALSE,   -- p/ evitar survivorship bias
    delisted_date DATE,
    first_trade_date DATE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_assets_symbol_exch UNIQUE (symbol, exchange)
);
COMMENT ON TABLE market_us.assets IS 'Ativos EUA (símbolo por bolsa). Identidade real = company_id. Inclui deslistados.';
CREATE INDEX IF NOT EXISTS idx_us_assets_company ON market_us.assets (company_id);
CREATE INDEX IF NOT EXISTS idx_us_assets_symbol  ON market_us.assets (symbol);
CREATE INDEX IF NOT EXISTS idx_us_assets_type    ON market_us.assets (security_type);
CREATE INDEX IF NOT EXISTS idx_us_assets_delist  ON market_us.assets (is_delisted);
DROP TRIGGER IF EXISTS trg_us_assets_updated ON market_us.assets;
CREATE TRIGGER trg_us_assets_updated BEFORE UPDATE ON market_us.assets
    FOR EACH ROW EXECUTE FUNCTION market_us.set_updated_at();


-- ── 4. ticker_aliases (histórico de símbolos) ───────────────
-- Trocas de ticker, spin-offs, fusões. Nunca apagar o histórico da empresa.
CREATE TABLE IF NOT EXISTS market_us.ticker_aliases (
    id           BIGSERIAL   PRIMARY KEY,
    company_id   BIGINT      REFERENCES market_us.companies(id) ON DELETE CASCADE,
    old_symbol   TEXT        NOT NULL,
    new_symbol   TEXT,
    change_date  DATE,
    reason       TEXT        NOT NULL DEFAULT 'rename'
                    CHECK (reason IN ('rename','merger','acquisition','spinoff',
                                      'delisting','exchange_move','reused','other')),
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_alias UNIQUE (company_id, old_symbol, new_symbol, change_date)
);
COMMENT ON TABLE market_us.ticker_aliases IS 'Aliases/histórico de tickers (rename/merger/spinoff/reused).';
CREATE INDEX IF NOT EXISTS idx_us_alias_old ON market_us.ticker_aliases (old_symbol);
CREATE INDEX IF NOT EXISTS idx_us_alias_new ON market_us.ticker_aliases (new_symbol);


-- ── colunas PIT reutilizadas nas tabelas-fato (documentação) ─
-- reference_date : fim do período fiscal (temporalidade financeira REAL)
-- published_date : data do filing/aceite (SEC accepted date)
-- available_at   : quando o dado poderia ser conhecido pelo investidor (PIT)
-- ingested_at    : quando entrou no warehouse (NUNCA usar p/ temporalidade)
-- content_hash   : detecta alteração/restatement sem depender de datas

-- ── 5. income_statements ────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.income_statements (
    id             BIGSERIAL   PRIMARY KEY,
    company_id     BIGINT      NOT NULL REFERENCES market_us.companies(id) ON DELETE CASCADE,
    symbol         TEXT,
    period         TEXT        NOT NULL DEFAULT 'annual'
                      CHECK (period IN ('annual','quarterly','ttm')),
    fiscal_year    INTEGER     NOT NULL,
    fiscal_quarter SMALLINT    NOT NULL DEFAULT 0 CHECK (fiscal_quarter BETWEEN 0 AND 4),
    reference_date DATE,
    published_date DATE,
    available_at   DATE,
    currency       TEXT        NOT NULL DEFAULT 'USD',
    unit           TEXT        NOT NULL DEFAULT 'absolute',  -- 'absolute'|'thousands'|'millions'
    revenue                NUMERIC(24,2),
    cost_of_revenue        NUMERIC(24,2),
    gross_profit           NUMERIC(24,2),
    rnd_expenses           NUMERIC(24,2),
    sga_expenses           NUMERIC(24,2),
    operating_income       NUMERIC(24,2),
    ebitda                 NUMERIC(24,2),
    ebit                   NUMERIC(24,2),
    interest_expense       NUMERIC(24,2),
    income_tax             NUMERIC(24,2),
    net_income             NUMERIC(24,2),
    eps                    NUMERIC(20,6),
    eps_diluted            NUMERIC(20,6),
    weighted_shares        NUMERIC(24,2),
    weighted_shares_diluted NUMERIC(24,2),
    source         TEXT        NOT NULL DEFAULT 'fmp',
    source_version TEXT,
    content_hash   TEXT,
    quality_status TEXT        NOT NULL DEFAULT 'raw'
                      CHECK (quality_status IN ('raw','validated','flagged','rejected')),
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_income UNIQUE (company_id, period, fiscal_year, fiscal_quarter)
);
COMMENT ON TABLE market_us.income_statements IS 'DRE EUA. Upsert (company_id,period,fiscal_year,fiscal_quarter). quarter=0 => anual.';
CREATE INDEX IF NOT EXISTS idx_us_income_symbol ON market_us.income_statements (symbol, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_us_income_avail  ON market_us.income_statements (company_id, available_at);
DROP TRIGGER IF EXISTS trg_us_income_updated ON market_us.income_statements;
CREATE TRIGGER trg_us_income_updated BEFORE UPDATE ON market_us.income_statements
    FOR EACH ROW EXECUTE FUNCTION market_us.set_updated_at();


-- ── 6. balance_sheets ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.balance_sheets (
    id             BIGSERIAL   PRIMARY KEY,
    company_id     BIGINT      NOT NULL REFERENCES market_us.companies(id) ON DELETE CASCADE,
    symbol         TEXT,
    period         TEXT        NOT NULL DEFAULT 'annual'
                      CHECK (period IN ('annual','quarterly','ttm')),
    fiscal_year    INTEGER     NOT NULL,
    fiscal_quarter SMALLINT    NOT NULL DEFAULT 0 CHECK (fiscal_quarter BETWEEN 0 AND 4),
    reference_date DATE,
    published_date DATE,
    available_at   DATE,
    currency       TEXT        NOT NULL DEFAULT 'USD',
    unit           TEXT        NOT NULL DEFAULT 'absolute',
    cash_and_equivalents   NUMERIC(24,2),
    short_term_investments NUMERIC(24,2),
    current_assets         NUMERIC(24,2),
    total_assets           NUMERIC(24,2),
    goodwill               NUMERIC(24,2),
    intangibles            NUMERIC(24,2),
    short_term_debt        NUMERIC(24,2),
    long_term_debt         NUMERIC(24,2),
    total_debt             NUMERIC(24,2),
    net_debt               NUMERIC(24,2),
    current_liabilities    NUMERIC(24,2),
    total_liabilities      NUMERIC(24,2),
    total_equity           NUMERIC(24,2),
    shares_outstanding     NUMERIC(24,2),
    invested_capital       NUMERIC(24,2),
    source         TEXT        NOT NULL DEFAULT 'fmp',
    source_version TEXT,
    content_hash   TEXT,
    quality_status TEXT        NOT NULL DEFAULT 'raw'
                      CHECK (quality_status IN ('raw','validated','flagged','rejected')),
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_balance UNIQUE (company_id, period, fiscal_year, fiscal_quarter)
);
COMMENT ON TABLE market_us.balance_sheets IS 'Balanço EUA. Upsert (company_id,period,fiscal_year,fiscal_quarter).';
CREATE INDEX IF NOT EXISTS idx_us_balance_symbol ON market_us.balance_sheets (symbol, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_us_balance_avail  ON market_us.balance_sheets (company_id, available_at);
DROP TRIGGER IF EXISTS trg_us_balance_updated ON market_us.balance_sheets;
CREATE TRIGGER trg_us_balance_updated BEFORE UPDATE ON market_us.balance_sheets
    FOR EACH ROW EXECUTE FUNCTION market_us.set_updated_at();


-- ── 7. cash_flow_statements ─────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.cash_flow_statements (
    id             BIGSERIAL   PRIMARY KEY,
    company_id     BIGINT      NOT NULL REFERENCES market_us.companies(id) ON DELETE CASCADE,
    symbol         TEXT,
    period         TEXT        NOT NULL DEFAULT 'annual'
                      CHECK (period IN ('annual','quarterly','ttm')),
    fiscal_year    INTEGER     NOT NULL,
    fiscal_quarter SMALLINT    NOT NULL DEFAULT 0 CHECK (fiscal_quarter BETWEEN 0 AND 4),
    reference_date DATE,
    published_date DATE,
    available_at   DATE,
    currency       TEXT        NOT NULL DEFAULT 'USD',
    unit           TEXT        NOT NULL DEFAULT 'absolute',
    operating_cash_flow      NUMERIC(24,2),
    capex                    NUMERIC(24,2),
    free_cash_flow           NUMERIC(24,2),
    acquisitions             NUMERIC(24,2),
    investments              NUMERIC(24,2),
    stock_issuance           NUMERIC(24,2),
    stock_repurchase         NUMERIC(24,2),
    debt_issuance            NUMERIC(24,2),
    debt_repayment           NUMERIC(24,2),
    dividends_paid           NUMERIC(24,2),
    stock_based_compensation NUMERIC(24,2),
    source         TEXT        NOT NULL DEFAULT 'fmp',
    source_version TEXT,
    content_hash   TEXT,
    quality_status TEXT        NOT NULL DEFAULT 'raw'
                      CHECK (quality_status IN ('raw','validated','flagged','rejected')),
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_cashflow UNIQUE (company_id, period, fiscal_year, fiscal_quarter)
);
COMMENT ON TABLE market_us.cash_flow_statements IS 'DFC EUA. Upsert (company_id,period,fiscal_year,fiscal_quarter).';
CREATE INDEX IF NOT EXISTS idx_us_cashflow_symbol ON market_us.cash_flow_statements (symbol, fiscal_year);
CREATE INDEX IF NOT EXISTS idx_us_cashflow_avail  ON market_us.cash_flow_statements (company_id, available_at);
DROP TRIGGER IF EXISTS trg_us_cashflow_updated ON market_us.cash_flow_statements;
CREATE TRIGGER trg_us_cashflow_updated BEFORE UPDATE ON market_us.cash_flow_statements
    FOR EACH ROW EXECUTE FUNCTION market_us.set_updated_at();


-- ── 8. key_metrics (ratios + múltiplos por período) ─────────
-- Formato LONGO (metric_name/metric_value) espelhando market.calculated_metrics:
-- extensível sem ALTER, com método/fonte/confiança por métrica.
CREATE TABLE IF NOT EXISTS market_us.key_metrics (
    id             BIGSERIAL   PRIMARY KEY,
    company_id     BIGINT      NOT NULL REFERENCES market_us.companies(id) ON DELETE CASCADE,
    symbol         TEXT,
    period         TEXT        NOT NULL DEFAULT 'annual'
                      CHECK (period IN ('annual','quarterly','ttm','spot')),
    fiscal_year    INTEGER     NOT NULL DEFAULT 0,
    fiscal_quarter SMALLINT    NOT NULL DEFAULT 0 CHECK (fiscal_quarter BETWEEN 0 AND 4),
    metric_name    TEXT        NOT NULL,     -- 'pe','ev_ebit','roic','fcf_yield',...
    metric_value   NUMERIC(28,10),
    unit           TEXT,                     -- 'ratio'|'pct'|'usd'|'x'
    reference_date DATE,
    available_at   DATE,
    calculation_method TEXT,
    source         TEXT        NOT NULL DEFAULT 'fmp',
    quality_status TEXT        NOT NULL DEFAULT 'raw'
                      CHECK (quality_status IN ('raw','validated','flagged','rejected')),
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_keymetric UNIQUE (company_id, period, fiscal_year, fiscal_quarter, metric_name)
);
COMMENT ON TABLE market_us.key_metrics IS 'Métricas/múltiplos EUA (formato longo). Upsert por chave+metric_name.';
CREATE INDEX IF NOT EXISTS idx_us_keymetric_name ON market_us.key_metrics (metric_name);
CREATE INDEX IF NOT EXISTS idx_us_keymetric_sym  ON market_us.key_metrics (symbol, metric_name);
DROP TRIGGER IF EXISTS trg_us_keymetric_updated ON market_us.key_metrics;
CREATE TRIGGER trg_us_keymetric_updated BEFORE UPDATE ON market_us.key_metrics
    FOR EACH ROW EXECUTE FUNCTION market_us.set_updated_at();


-- ── 9. prices_daily ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.prices_daily (
    symbol         TEXT        NOT NULL,
    date           DATE        NOT NULL,
    open           NUMERIC(20,6),
    high           NUMERIC(20,6),
    low            NUMERIC(20,6),
    close          NUMERIC(20,6),
    adjusted_close NUMERIC(20,6),
    volume         BIGINT,
    source         TEXT        NOT NULL DEFAULT 'fmp',
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, date)
);
-- Sem FK para assets de propósito: símbolos deslistados/reutilizados podem não ter
-- linha ativa em assets, e o histórico de preços NUNCA deve ser apagado por isso
-- (evita delisting/survivorship bias). A integridade symbol->company resolve-se na
-- aplicação via ticker_aliases.
COMMENT ON TABLE market_us.prices_daily IS 'OHLCV diário ajustado EUA. Upsert (symbol,date).';
CREATE INDEX IF NOT EXISTS idx_us_prices_date ON market_us.prices_daily (date);


-- ── 10. prices_monthly (série derivada p/ desempenho) ───────
CREATE TABLE IF NOT EXISTS market_us.prices_monthly (
    symbol         TEXT        NOT NULL,
    month_end      DATE        NOT NULL,
    close          NUMERIC(20,6),
    adjusted_close NUMERIC(20,6),
    volume         BIGINT,
    total_return   NUMERIC(20,8),
    source         TEXT        NOT NULL DEFAULT 'derived',
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, month_end)
);
COMMENT ON TABLE market_us.prices_monthly IS 'Fechamento mensal ajustado (derivado de prices_daily) p/ backtests.';


-- ── 11. dividends ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.dividends (
    id           BIGSERIAL   PRIMARY KEY,
    symbol       TEXT        NOT NULL,
    ex_date      DATE,
    payment_date DATE,
    record_date  DATE,
    declaration_date DATE,
    amount       NUMERIC(20,6) NOT NULL,
    adjusted_amount NUMERIC(20,6),
    currency     TEXT        NOT NULL DEFAULT 'USD',
    source       TEXT        NOT NULL DEFAULT 'fmp',
    event_date   DATE        GENERATED ALWAYS AS (COALESCE(ex_date, payment_date)) STORED,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_dividends UNIQUE (symbol, event_date, amount)
);
COMMENT ON TABLE market_us.dividends IS 'Dividendos EUA. Upsert (symbol,event_date,amount).';
CREATE INDEX IF NOT EXISTS idx_us_dividends_symbol ON market_us.dividends (symbol, event_date DESC);


-- ── 12. splits ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.splits (
    id           BIGSERIAL   PRIMARY KEY,
    symbol       TEXT        NOT NULL,
    split_date   DATE        NOT NULL,
    numerator    NUMERIC(20,6) NOT NULL,   -- ex.: 2 p/ 2:1
    denominator  NUMERIC(20,6) NOT NULL,   -- ex.: 1 p/ 2:1
    source       TEXT        NOT NULL DEFAULT 'fmp',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_splits UNIQUE (symbol, split_date)
);
COMMENT ON TABLE market_us.splits IS 'Desdobramentos/grupamentos EUA. Upsert (symbol,split_date).';
CREATE INDEX IF NOT EXISTS idx_us_splits_symbol ON market_us.splits (symbol, split_date DESC);


-- ── 13. analyst_estimates ───────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.analyst_estimates (
    id             BIGSERIAL   PRIMARY KEY,
    company_id     BIGINT      REFERENCES market_us.companies(id) ON DELETE CASCADE,
    symbol         TEXT        NOT NULL,
    period         TEXT        NOT NULL DEFAULT 'annual' CHECK (period IN ('annual','quarterly')),
    fiscal_year    INTEGER     NOT NULL,
    fiscal_quarter SMALLINT    NOT NULL DEFAULT 0 CHECK (fiscal_quarter BETWEEN 0 AND 4),
    estimated_revenue  NUMERIC(24,2),
    estimated_eps      NUMERIC(20,6),
    estimated_ebitda   NUMERIC(24,2),
    num_analysts       INTEGER,
    available_at   DATE,
    source         TEXT        NOT NULL DEFAULT 'fmp',
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_estimates UNIQUE (symbol, period, fiscal_year, fiscal_quarter)
);
COMMENT ON TABLE market_us.analyst_estimates IS 'Estimativas de analistas (quando licenciadas). Upsert por chave.';
CREATE INDEX IF NOT EXISTS idx_us_estimates_symbol ON market_us.analyst_estimates (symbol, fiscal_year);
DROP TRIGGER IF EXISTS trg_us_estimates_updated ON market_us.analyst_estimates;
CREATE TRIGGER trg_us_estimates_updated BEFORE UPDATE ON market_us.analyst_estimates
    FOR EACH ROW EXECUTE FUNCTION market_us.set_updated_at();


-- ── 14. market_cap_history ──────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.market_cap_history (
    symbol       TEXT        NOT NULL,
    date         DATE        NOT NULL,
    market_cap   NUMERIC(28,2),
    source       TEXT        NOT NULL DEFAULT 'fmp',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, date)
);
COMMENT ON TABLE market_us.market_cap_history IS 'Market cap histórico. Upsert (symbol,date).';


-- ── 15. sector_industry_history (reclassificações) ──────────
CREATE TABLE IF NOT EXISTS market_us.sector_industry_history (
    id           BIGSERIAL   PRIMARY KEY,
    company_id   BIGINT      NOT NULL REFERENCES market_us.companies(id) ON DELETE CASCADE,
    sector       TEXT,
    industry     TEXT,
    effective_date DATE      NOT NULL,
    source       TEXT        NOT NULL DEFAULT 'fmp',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_sector_hist UNIQUE (company_id, effective_date)
);
COMMENT ON TABLE market_us.sector_industry_history IS 'Histórico de setor/indústria (PIT p/ comparação por indústria).';


-- ── 16. ingestion_runs (checkpoint/retomada) ────────────────
CREATE TABLE IF NOT EXISTS market_us.ingestion_runs (
    id            BIGSERIAL   PRIMARY KEY,
    run_key       TEXT        NOT NULL,        -- ex.: 'bootstrap-2026-07', 'daily'
    domain        TEXT        NOT NULL,        -- 'universe','profiles','prices',...
    status        TEXT        NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running','paused','completed','failed')),
    cursor        TEXT,                        -- último símbolo/lote processado (retomada)
    params        JSONB,
    calls_made    INTEGER     NOT NULL DEFAULT 0,
    rows_written  INTEGER     NOT NULL DEFAULT 0,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    note          TEXT,
    CONSTRAINT uq_us_run UNIQUE (run_key, domain)
);
COMMENT ON TABLE market_us.ingestion_runs IS 'Estado de cada domínio de ingestão p/ retomada incremental.';
CREATE INDEX IF NOT EXISTS idx_us_run_status ON market_us.ingestion_runs (status, domain);


-- ── 17. ingestion_errors ────────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.ingestion_errors (
    id           BIGSERIAL   PRIMARY KEY,
    run_id       BIGINT      REFERENCES market_us.ingestion_runs(id) ON DELETE CASCADE,
    symbol       TEXT,
    domain       TEXT,
    endpoint     TEXT,
    error_type   TEXT,                         -- 'http','rate_limit','parse','empty','timeout'
    http_status  INTEGER,
    attempts     INTEGER     NOT NULL DEFAULT 1,
    message      TEXT,
    resolved     BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market_us.ingestion_errors IS 'Erros de ingestão (reprocessáveis). Nunca corrompem dados válidos.';
CREATE INDEX IF NOT EXISTS idx_us_err_run  ON market_us.ingestion_errors (run_id);
CREATE INDEX IF NOT EXISTS idx_us_err_sym  ON market_us.ingestion_errors (symbol, resolved);


-- ── 18. data_quality_audit ──────────────────────────────────
CREATE TABLE IF NOT EXISTS market_us.data_quality_audit (
    id           BIGSERIAL   PRIMARY KEY,
    symbol       TEXT,
    table_name   TEXT        NOT NULL,
    field_name   TEXT,
    check_name   TEXT        NOT NULL,   -- 'balance_identity','fcf_coherence','mcap_coherence',...
    severity     TEXT        NOT NULL DEFAULT 'info'
                    CHECK (severity IN ('info','warn','critical')),
    passed       BOOLEAN,
    detail       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market_us.data_quality_audit IS 'Resultados dos testes de qualidade (identidade contábil, FCF, mcap...).';
CREATE INDEX IF NOT EXISTS idx_us_dq_table ON market_us.data_quality_audit (table_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_us_dq_sev   ON market_us.data_quality_audit (severity, created_at DESC);


-- ── 19. score_vintages (PIT — populado nas fases de análise) ─
CREATE TABLE IF NOT EXISTS market_us.score_vintages (
    id            BIGSERIAL   PRIMARY KEY,
    company_id    BIGINT      NOT NULL REFERENCES market_us.companies(id) ON DELETE CASCADE,
    symbol        TEXT,
    score_version TEXT        NOT NULL,
    as_of_date    DATE        NOT NULL,       -- data PIT do score (knowledge date)
    track         TEXT        NOT NULL DEFAULT 'fundamental'
                     CHECK (track IN ('fundamental','asymmetric')),
    score         NUMERIC(10,4),
    factors_json  JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_score UNIQUE (company_id, score_version, as_of_date, track)
);
COMMENT ON TABLE market_us.score_vintages IS 'Versões PIT dos scores (fundamentalista e assimétrico). Populado nas fases de análise.';
CREATE INDEX IF NOT EXISTS idx_us_score_asof ON market_us.score_vintages (as_of_date, track);


-- ── 20. raw_payloads (auditoria — SÓ warehouse local) ───────
CREATE TABLE IF NOT EXISTS market_us.raw_payloads (
    id             BIGSERIAL   PRIMARY KEY,
    symbol         TEXT,
    endpoint       TEXT        NOT NULL,
    payload_json   JSONB,
    request_status TEXT        NOT NULL DEFAULT 'success'
                      CHECK (request_status IN ('success','failed','rate_limited','empty')),
    http_status    INTEGER,
    fetched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source         TEXT        NOT NULL DEFAULT 'fmp'
);
COMMENT ON TABLE market_us.raw_payloads IS 'Log append-only das respostas brutas da FMP (auditoria/reprocessamento). Warehouse-only.';
CREATE INDEX IF NOT EXISTS idx_us_raw_symbol   ON market_us.raw_payloads (symbol);
CREATE INDEX IF NOT EXISTS idx_us_raw_endpoint ON market_us.raw_payloads (endpoint, fetched_at DESC);

-- ============================================================
-- FIM 040. Portfólios/backtests (us_portfolio_models, us_backtest_results,
-- us_outlier_candidates) entram em migration posterior, junto das fases de
-- carteira/backtest/fora-da-curva.
-- ============================================================

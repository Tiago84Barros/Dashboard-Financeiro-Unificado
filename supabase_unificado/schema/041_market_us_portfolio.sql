-- ============================================================
-- 041_market_us_portfolio.sql
-- Empresas Americanas — carteiras-modelo e resultados de backtest (market_us).
-- Idempotente, sem DROP/TRUNCATE/DELETE. Warehouse local.
-- ============================================================

-- ── us_portfolio_models ─────────────────────────────────────
-- Carteira-modelo (metadados leves + holdings em JSONB). Uma "ativa" por nome.
CREATE TABLE IF NOT EXISTS market_us.portfolio_models (
    id             BIGSERIAL   PRIMARY KEY,
    name           TEXT        NOT NULL DEFAULT 'default',
    score_version  TEXT        NOT NULL,
    as_of_date     DATE        NOT NULL,
    params         JSONB,                      -- restrições/pesos usados
    holdings       JSONB       NOT NULL,       -- [{symbol, weight, score, sector}]
    n_assets       INTEGER,
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    plan_hash      TEXT,                        -- dedup determinístico
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_portfolio UNIQUE (name, plan_hash)
);
COMMENT ON TABLE market_us.portfolio_models IS 'Carteira-modelo americana (holdings em JSONB, dedup por plan_hash).';
CREATE INDEX IF NOT EXISTS idx_us_portfolio_active ON market_us.portfolio_models (name, is_active);


-- ── us_backtest_results ─────────────────────────────────────
-- Resumo de um backtest walk-forward PIT (métricas agregadas + curva em JSONB).
CREATE TABLE IF NOT EXISTS market_us.backtest_results (
    id             BIGSERIAL   PRIMARY KEY,
    run_key        TEXT        NOT NULL,
    score_version  TEXT        NOT NULL,
    strategy       TEXT        NOT NULL DEFAULT 'top_n',
    params         JSONB,
    start_date     DATE,
    end_date       DATE,
    n_periods      INTEGER,
    rank_ic_mean   NUMERIC(10,6),
    rank_ic_tstat  NUMERIC(10,4),
    rank_ic_pvalue NUMERIC(10,6),
    hit_rate       NUMERIC(6,4),
    ann_return     NUMERIC(10,6),
    excess_ew      NUMERIC(10,6),
    volatility     NUMERIC(10,6),
    sharpe         NUMERIC(10,4),
    sortino        NUMERIC(10,4),
    calmar         NUMERIC(10,4),
    max_drawdown   NUMERIC(10,6),
    turnover       NUMERIC(10,6),
    equity_curve   JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_backtest UNIQUE (run_key, score_version, strategy)
);
COMMENT ON TABLE market_us.backtest_results IS 'Resultados agregados de backtests PIT (Rank-IC, Sharpe/Sortino/Calmar, curva).';
CREATE INDEX IF NOT EXISTS idx_us_backtest_run ON market_us.backtest_results (run_key, created_at DESC);

-- ============================================================
-- FIM 041. us_outlier_candidates entra na Fase 7 (Fora da Curva).
-- ============================================================

-- ============================================================
-- 042_market_us_outliers.sql
-- Empresas Fora da Curva (retorno assimétrico) — market_us.outlier_candidates.
-- Idempotente, sem DROP/TRUNCATE/DELETE. Warehouse local.
-- ============================================================

CREATE TABLE IF NOT EXISTS market_us.outlier_candidates (
    id               BIGSERIAL   PRIMARY KEY,
    company_id       BIGINT      REFERENCES market_us.companies(id) ON DELETE CASCADE,
    symbol           TEXT        NOT NULL,
    score_version    TEXT        NOT NULL,
    as_of_date       DATE        NOT NULL,       -- data PIT do score de assimetria
    asymmetry_score  NUMERIC(6,2),
    confidence       NUMERIC(6,2),
    stage            TEXT,                        -- early|scaling|growth|mature
    risk_class       TEXT,                        -- média|alta|muito alta
    suggested_pct    NUMERIC(6,2),                -- tamanho sugerido (subcarteira pequena)
    positive_signals JSONB,
    risks            JSONB,
    invalidation     JSONB,
    missing_data     JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_outlier UNIQUE (company_id, score_version, as_of_date)
);
COMMENT ON TABLE market_us.outlier_candidates IS 'Candidatas a retorno assimétrico (score + sinais + invalidação). NÃO é recomendação.';
CREATE INDEX IF NOT EXISTS idx_us_outlier_asof  ON market_us.outlier_candidates (as_of_date, asymmetry_score DESC);
CREATE INDEX IF NOT EXISTS idx_us_outlier_sym   ON market_us.outlier_candidates (symbol);

-- ============================================================
-- FIM 042. Última migration da fundação Empresas Americanas.
-- ============================================================

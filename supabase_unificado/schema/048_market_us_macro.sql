-- ============================================================
-- 048_market_us_macro.sql
-- Observacoes macroeconomicas dos EUA (FRED) para o schema market_us.
--
-- Por que existe: o regime macro da secao Empresas Americanas era um conjunto
-- de literais no codigo (Fed 4,25%, CPI 2,5%). Eles apareciam na tela como
-- "Cenario Macroeconomico" e iam para o relatorio institucional como fato.
-- Premissa apresentada como observacao e defeito de credibilidade, nao de
-- interface: com esta tabela o valor passa a ter serie, data e fonte.
--
-- Idempotente, sem DROP/TRUNCATE/DELETE. Warehouse local.
-- ============================================================

CREATE TABLE IF NOT EXISTS market_us.macro_observations (
    id           BIGSERIAL   PRIMARY KEY,
    series_id    TEXT        NOT NULL,      -- id da serie no FRED (ex.: FEDFUNDS)
    indicator    TEXT        NOT NULL,      -- campo do USMacroSnapshot
    observed_at  DATE        NOT NULL,      -- data da observacao na serie
    value        NUMERIC(14,6) NOT NULL,
    unit         TEXT,                      -- 'percent', 'pp', 'index'
    source       TEXT        NOT NULL DEFAULT 'FRED',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_us_macro UNIQUE (series_id, observed_at)
);
COMMENT ON TABLE market_us.macro_observations IS
    'Series macro dos EUA (FRED). Alimenta o regime macro com dado observado no lugar de premissa.';

CREATE INDEX IF NOT EXISTS idx_us_macro_indicator
    ON market_us.macro_observations (indicator, observed_at DESC);

-- ============================================================
-- FIM 048.
-- ============================================================

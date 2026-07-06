-- ============================================================
-- 021_market_metric_vintages.sql
-- Versões imutáveis das métricas calculadas para uso point-in-time.
--
-- Não inventa datas históricas: o baseline existente é gravado somente
-- quando o ETL for reprocessado após esta migration. A partir daí cada
-- alteração de valor gera uma nova versão.
-- ============================================================

CREATE TABLE IF NOT EXISTS market.calculated_metric_vintages (
    id                  BIGSERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL,
    period              TEXT NOT NULL,
    year                INTEGER NOT NULL,
    quarter             INTEGER NOT NULL DEFAULT 0,
    metric_name         TEXT NOT NULL,
    metric_value        NUMERIC,
    calculation_method  TEXT,
    source              TEXT,
    confidence_score    NUMERIC,
    available_at        TIMESTAMPTZ NOT NULL,
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    availability_quality TEXT NOT NULL DEFAULT 'first_seen_proxy'
        CHECK (availability_quality IN (
            'published_at', 'first_seen_proxy', 'migration_baseline'
        ))
);

CREATE INDEX IF NOT EXISTS idx_metric_vintages_lookup
    ON market.calculated_metric_vintages
       (ticker, period, year, quarter, metric_name, available_at, recorded_at);

CREATE INDEX IF NOT EXISTS idx_metric_vintages_available
    ON market.calculated_metric_vintages (available_at);

COMMENT ON TABLE market.calculated_metric_vintages IS
    'Versões imutáveis das métricas calculadas. Não atualizar linhas; inserir nova versão quando o valor mudar.';
COMMENT ON COLUMN market.calculated_metric_vintages.available_at IS
    'Momento em que os dados-fonte ficaram disponíveis. first_seen_at é proxy até integração published_at CVM.';

-- Baseline explícito dos valores já existentes. A qualidade
-- migration_baseline impede que a aplicação trate NOW() como data histórica
-- de publicação. Reexecução é idempotente por chave natural.
INSERT INTO market.calculated_metric_vintages (
    ticker, period, year, quarter, metric_name, metric_value,
    calculation_method, source, confidence_score,
    available_at, availability_quality
)
SELECT cm.ticker, cm.period, cm.year, cm.quarter, cm.metric_name,
       cm.metric_value, cm.calculation_method, cm.source,
       cm.confidence_score, NOW(), 'migration_baseline'
FROM market.calculated_metrics cm
WHERE NOT EXISTS (
    SELECT 1
    FROM market.calculated_metric_vintages v
    WHERE v.ticker = cm.ticker
      AND v.period = cm.period
      AND v.year = cm.year
      AND v.quarter = cm.quarter
      AND v.metric_name = cm.metric_name
);

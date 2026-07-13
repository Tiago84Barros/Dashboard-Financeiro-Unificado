-- Indices para manutencao idempotente dos vintages FII e limpeza seletiva.
-- Evitam varreduras integrais ao substituir observacoes derivadas de uma fonte.

CREATE INDEX IF NOT EXISTS idx_fii_metric_source
    ON market.fii_metric_observations (source, id);

CREATE INDEX IF NOT EXISTS idx_fii_metric_supersedes
    ON market.fii_metric_observations (supersedes_id)
    WHERE supersedes_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_fii_documents_ticker_reference
    ON market.fii_documents (ticker, reference_date DESC);

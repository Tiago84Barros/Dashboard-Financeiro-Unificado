-- Compatibilidade temporaria com coletores legados que ainda gravam somente
-- OHLCV. O endpoint FII v2 continua sobrescrevendo estes proxies com metadados
-- completos e alimentando a tabela append-only.

ALTER TABLE market.historical_prices
    ALTER COLUMN source SET DEFAULT 'brapi_legacy_quote',
    ALTER COLUMN knowledge_at SET DEFAULT now(),
    ALTER COLUMN availability_quality SET DEFAULT 'first_observed_proxy';

-- 088: data propria da cotacao em market.fiis (auditoria FII 10/11/2026).
-- `updated_at` e renovado por qualquer UPDATE (trigger set_updated_at) e nao data
-- o preco. ADITIVA, idempotente, NAO APLICADA: exige autorizacao humana.
-- Rollback: ALTER TABLE market.fiis DROP COLUMN IF EXISTS price_at;
-- Backup: pg_dump -t market.fiis antes de aplicar. Compatibilidade: coluna
-- anulavel; o leitor so a nomeia quando ela existe; ate a ingestao grava-la a
-- data do preco-base e tratada como desconhecida.
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS price_at TIMESTAMPTZ;
COMMENT ON COLUMN market.fiis.price_at IS
  'Instante da cotacao gravada em price/dy_12m/pvp (regularMarketTime/pregao).';

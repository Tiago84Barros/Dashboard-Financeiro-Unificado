-- 016_fiis_cvm.sql
-- Enriquecimento CVM (Informe Mensal de FIIs) em market.fiis, via join por CNPJ.
-- Traz segmento real, tipo (tijolo/papel/fof/híbrido), patrimônio, VPA, nº de
-- cotistas e composição de ativos (diversificação). NÃO traz vacância (não consta
-- no informe estruturado). Idempotente.

ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS cnpj               TEXT;
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS isin               TEXT;
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS segmento_cvm       TEXT;
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS tipo               TEXT;  -- tijolo|papel|fof|hibrido
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS tipo_gestao        TEXT;
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS patrimonio_liquido NUMERIC(20,2);
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS vpa                NUMERIC(18,6);
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS num_cotistas       INTEGER;
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS pct_imoveis        NUMERIC(8,4);
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS pct_papel          NUMERIC(8,4);
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS pct_caixa          NUMERIC(8,4);
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS pct_fundos         NUMERIC(8,4);
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS cvm_ref_date       TEXT;

CREATE INDEX IF NOT EXISTS idx_fiis_cnpj ON market.fiis (cnpj);
CREATE INDEX IF NOT EXISTS idx_fiis_tipo ON market.fiis (tipo);

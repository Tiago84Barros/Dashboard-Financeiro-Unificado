-- 017_fiis_detalhe.sql
-- Detalhe por FII (aba "Busca de ativo"): vacância (Status Invest), nº de imóveis,
-- série mensal de fundamentos (VPA/PL/cotistas/DY/composição da CVM, p/ P/VP e VPA
-- históricos) e a lista de imóveis com região/UF (scraping). Idempotente.

-- ── market.fiis: vacância (atual) + contagem de imóveis ───────────────────────
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS vacancia          NUMERIC(8,4);  -- decimal (0.07 = 7%)
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS vacancia_ref_date TEXT;          -- carimbo da coleta
ALTER TABLE market.fiis ADD COLUMN IF NOT EXISTS num_imoveis       INTEGER;

-- ── Série mensal de fundamentos (Informe Mensal de FIIs da CVM) ───────────────
-- Uma linha por (ticker, mês). VPA mensal + preço bruto de market.historical_prices
-- dão o P/VP histórico na camada de leitura. PL/cotistas/DY/composição complementam.
CREATE TABLE IF NOT EXISTS market.fii_metrics_monthly (
    ticker             TEXT        NOT NULL,
    ref_month          DATE        NOT NULL,   -- 1º dia do mês de referência
    vpa                NUMERIC(18,6),
    patrimonio_liquido NUMERIC(20,2),
    num_cotistas       INTEGER,
    dy_patrimonial_mes NUMERIC(10,6),
    pct_imoveis        NUMERIC(8,4),
    pct_papel          NUMERIC(8,4),
    pct_caixa          NUMERIC(8,4),
    pct_fundos         NUMERIC(8,4),
    PRIMARY KEY (ticker, ref_month)
);
COMMENT ON TABLE market.fii_metrics_monthly IS
    'Série mensal de fundamentos do FII (CVM Informe Mensal). Upsert por (ticker, ref_month).';
CREATE INDEX IF NOT EXISTS idx_fii_metrics_monthly_ticker ON market.fii_metrics_monthly (ticker);

-- ── Imóveis do fundo (scraping; tijolo/híbrido) ───────────────────────────────
CREATE TABLE IF NOT EXISTS market.fii_imoveis (
    ticker          TEXT        NOT NULL,
    nome_imovel     TEXT        NOT NULL,
    area_m2         NUMERIC(18,2),
    vacancia        NUMERIC(8,4),
    cidade          TEXT,
    uf              TEXT,
    regiao          TEXT,                       -- Norte|Nordeste|Centro-Oeste|Sudeste|Sul
    segmento_imovel TEXT,                       -- ex.: Logística, Lajes Corporativas, Shopping
    pct_receita     NUMERIC(8,4),
    fonte           TEXT,                       -- statusinvest|fundamentus
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ticker, nome_imovel)
);
COMMENT ON TABLE market.fii_imoveis IS
    'Imóveis por FII (scraping best-effort). Upsert por (ticker, nome_imovel).';
CREATE INDEX IF NOT EXISTS idx_fii_imoveis_ticker ON market.fii_imoveis (ticker);

DROP TRIGGER IF EXISTS trg_fii_imoveis_updated ON market.fii_imoveis;
CREATE TRIGGER trg_fii_imoveis_updated BEFORE UPDATE ON market.fii_imoveis
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();

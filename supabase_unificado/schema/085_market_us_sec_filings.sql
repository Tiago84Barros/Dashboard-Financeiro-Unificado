-- 085_market_us_sec_filings.sql
-- Documentos entregues a SEC (8-K, 10-Q, 10-K, DEF 14A...) das acoes dos EUA.
--
-- A tela de relatorios dizia em texto fixo "nao ha base de filings da SEC no
-- projeto para acoes americanas". A B3 tem `public.docs_corporativos` (CVM) e
-- os FIIs `market.fii_documents` (FNET); esta e a tabela equivalente dos EUA.
--
-- So metadados: formulario, data, itens do 8-K e o link do documento. O texto
-- nao e baixado -- o item ja diz o que aconteceu ("2.02" e divulgacao de
-- resultado, "5.02" e saida de diretor) e o link leva ao original.
--
-- Chave (accession_number, symbol): a mesma entrega vale para todas as classes
-- da empresa (GOOG e GOOGL), e a vitrine e lida por simbolo.
--
-- Mora no ARMAZEM LOCAL, como `docs_corporativos`. O app publicado le a vitrine
-- `data/public/informacoes_recentes.json.gz`. Coleta: `run_us_ingest.py
-- filings --warehouse`, alvo `us_sec_filings` de `core.publicacao_agenda`.
-- Idempotente.

CREATE TABLE IF NOT EXISTS market_us.sec_filings (
    accession_number TEXT        NOT NULL,
    symbol           TEXT        NOT NULL,
    cik              TEXT        NOT NULL,
    form             TEXT        NOT NULL,
    filing_date      DATE        NOT NULL,
    report_date      DATE,
    items            TEXT,
    primary_document TEXT,
    description      TEXT,
    url              TEXT        NOT NULL,
    collected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (accession_number, symbol)
);

CREATE INDEX IF NOT EXISTS idx_us_sec_filings_symbol_data
    ON market_us.sec_filings (symbol, filing_date DESC);

COMMENT ON TABLE market_us.sec_filings IS
    'Entregas a SEC (submissions.json) das acoes do universo dos EUA. Metadados e link; sem texto.';
COMMENT ON COLUMN market_us.sec_filings.items IS
    'Itens do 8-K separados por virgula (ex.: "2.02,9.01"). NULL fora do 8-K.';

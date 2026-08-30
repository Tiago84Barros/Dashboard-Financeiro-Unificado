-- 057 — market_us.score_vintages na VITRINE (Supabase)
--
-- A tabela já existe no warehouse local desde a 040, com FK para
-- market_us.companies. A vitrine não tem `companies` -- ela publica
-- `company_snapshots` e mais nada -- então a definição da 040 não pode ser
-- aplicada lá: a FK falharia e, pior, arrastaria a tabela de cadastro inteira
-- para dentro do limite de espaço do Supabase por nenhum motivo de leitura.
--
-- O painel PIT (`core/us_read.py::load_score_panel`) lê apenas
-- as_of_date, symbol, score, track e score_version. É essa a superfície que
-- esta migration cria, chaveada por símbolo -- e é por símbolo que o painel
-- junta a safra ao preço mensal, nunca por company_id.
--
-- `IF NOT EXISTS` faz disto um no-op no warehouse local, onde a 040 já criou a
-- versão com FK. Rodar as duas na mesma base não é conflito: é a mesma tabela
-- vista de dois lados.
CREATE TABLE IF NOT EXISTS market_us.score_vintages (
    id            BIGSERIAL   PRIMARY KEY,
    symbol        TEXT        NOT NULL,
    score_version TEXT        NOT NULL,
    as_of_date    DATE        NOT NULL,
    track         TEXT        NOT NULL DEFAULT 'fundamental'
                     CHECK (track IN ('fundamental','asymmetric')),
    score         NUMERIC(10,4),
    coverage         NUMERIC(6,2),
    score_confidence NUMERIC(6,2),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Chave de publicação: o publicador regrava a mesma safra sem duplicá-la.
-- Só na vitrine. No warehouse local a mesma (versão, data, trilha) pode ter dois
-- company_id sob o mesmo símbolo -- é lá que mora o cadastro, com seus
-- homônimos e reaproveitamentos de ticker -- e criar o índice único ali
-- falharia, ou pior, passaria hoje e quebraria a ingestão amanhã. A presença de
-- `company_id` é o que distingue as duas bases, e é o que este bloco consulta.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'market_us'
                     AND table_name = 'score_vintages'
                     AND column_name = 'company_id') THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_us_score_vintage_simbolo
            ON market_us.score_vintages (symbol, score_version, as_of_date, track);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_us_score_version_asof
    ON market_us.score_vintages (score_version, as_of_date, track);

ALTER TABLE market_us.score_vintages ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE market_us.score_vintages FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE market_us.score_vintages FROM authenticated;
    END IF;
END $$;

COMMENT ON TABLE market_us.score_vintages IS
    'Safras PIT de score publicadas para o painel de backtest dos EUA. Na vitrine, chaveadas por símbolo.';

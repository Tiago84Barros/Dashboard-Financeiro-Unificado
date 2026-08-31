-- 064_market_us_delisting_outcomes.sql
--
-- O desfecho de cada saida, na vitrine, com a chave que o painel realmente usa.
--
-- A causa e apurada em `market_us.assets.delisting_cause`, que so existe no
-- armazem local -- a Streamlit Cloud nao o alcanca. E `market_us.delistings`,
-- que a vitrine tem, e a tabela de DERIVACAO: 12.107 linhas por CIK, das quais
-- 1.926 com simbolo, muitas refutadas. Pendurar o desfecho nela misturaria a
-- apuracao com o resultado e obrigaria o painel a repetir o filtro de refutacao
-- a cada leitura.
--
-- Esta tabela e o resultado: um simbolo, uma data, um desfecho. A chave e
-- `symbol` porque e por symbol que `build_annual_panel` junta safra e preco --
-- publicar por CIK obrigaria a resolver simbolo->CIK dentro da leitura, que e
-- exatamente o passo que ja custou 55 tickers reciclados neste projeto.

CREATE TABLE IF NOT EXISTS market_us.delisting_outcomes (
    symbol         TEXT PRIMARY KEY,
    delisted_date  DATE NOT NULL,
    cause          TEXT NOT NULL,
    published_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'delisting_outcomes_cause_ck') THEN
        ALTER TABLE market_us.delisting_outcomes
            ADD CONSTRAINT delisting_outcomes_cause_ck
            CHECK (cause IN ('adquirida', 'sumiu', 'indefinido'));
    END IF;
END $$;

COMMENT ON TABLE market_us.delisting_outcomes IS
    'Desfecho da saida da bolsa por simbolo, publicado do armazem local. '
    'Alimenta a convencao de retorno de deslistagem (core/us_convencao_saida.py) '
    'para que a empresa morta pare de sair silenciosamente da apuracao de '
    'retorno -- descartar equivale a supor que ela rendeu a media das vivas. '
    'cause=indefinido e valor legitimo e majoritario: significa que a SEC nao '
    'permite decidir, e a linha continua fora da conta.';

COMMENT ON COLUMN market_us.delisting_outcomes.cause IS
    'adquirida | sumiu | indefinido. Regra unica em core/us_saida_causa.py, '
    'apurada pelo item do 8-K (1.03 antes de 2.01).';

CREATE INDEX IF NOT EXISTS delisting_outcomes_date_idx
    ON market_us.delisting_outcomes (delisted_date);

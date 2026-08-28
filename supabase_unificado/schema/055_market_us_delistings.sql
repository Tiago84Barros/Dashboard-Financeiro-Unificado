-- 055_market_us_delistings.sql
--
-- Registro das SAIDAS do universo americano.
--
-- Ate aqui `market_us.assets.delisted_date` era NULL nas 7.654 linhas e o
-- painel `score_vintages` acusava zero saidas em 16 safras -- nao porque o
-- mercado nao tenha saidas, mas porque `companies` foi montada a partir de quem
-- esta listado hoje. Quem morreu nunca entrou, entao o backtest e 100%
-- sobrevivente e o retorno que ele mostra e teto, nao expectativa.
--
-- Esta tabela existe separada de `assets` de proposito: a maioria absoluta das
-- empresas que sairam NAO tem linha em `assets` nem em `companies` (elas nunca
-- foram ingeridas). Gravar a saida so onde ja existe cadastro registraria
-- justamente a minoria irrelevante -- as que sobreviveram tempo bastante para
-- entrar no universo -- e deixaria o vies do tamanho que estava.
--
-- `delisted_date` e derivada, e a coluna `reason` diz isso. Ausencia de
-- relatorio anual nao separa falencia de aquisicao (item 1.03 x 2.01 do 8-K
-- separa), e quem foi comprado com premio nao perdeu capital: por isso a causa
-- fica num campo proprio, com valor honesto, em vez de virar "falencia" por
-- conveniencia de quem le.

CREATE TABLE IF NOT EXISTS market_us.delistings (
    cik                       BIGINT      PRIMARY KEY,
    company_id                BIGINT      NULL REFERENCES market_us.companies(id),
    symbol                    TEXT        NULL,
    last_annual_report_year   INTEGER     NOT NULL,
    absence_year              INTEGER     NOT NULL,
    delisted_date             DATE        NOT NULL,
    reason                    TEXT        NOT NULL DEFAULT 'ausencia_de_relatorio_anual',
    source                    TEXT        NOT NULL DEFAULT 'sec_full_index',
    derived_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT delistings_absence_after_last_report
        CHECK (absence_year > last_annual_report_year)
);

COMMENT ON TABLE market_us.delistings IS
    'Saidas do universo americano derivadas do full-index da SEC. delisted_date '
    'e o fim do primeiro ano em que a ausencia ja era observavel, nunca a data '
    'do ultimo relatorio: datar no ultimo relatorio afirmaria saber da morte '
    'antes de existir evidencia dela.';

COMMENT ON COLUMN market_us.delistings.reason IS
    'Como a saida foi observada, nao a causa economica. Ausencia de relatorio '
    'anual nao distingue falencia de aquisicao.';

CREATE INDEX IF NOT EXISTS delistings_absence_year_idx
    ON market_us.delistings (absence_year);
CREATE INDEX IF NOT EXISTS delistings_company_idx
    ON market_us.delistings (company_id) WHERE company_id IS NOT NULL;

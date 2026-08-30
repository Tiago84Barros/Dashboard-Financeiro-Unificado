-- 059_market_us_delistings_vitrine.sql
--
-- `market_us.delistings` na VITRINE, com a forma que a vitrine comporta.
--
-- A 055 declara `company_id BIGINT REFERENCES market_us.companies(id)`. A
-- vitrine nao tem `companies` -- ela nunca teve, e publicar o cadastro inteiro
-- so para satisfazer a chave estrangeira arrastaria uma tabela que nenhuma
-- consulta da tela le. Aqui `company_id` fica como numero solto, e a juncao que
-- importa e por SIMBOLO: a esmagadora maioria das saidas nunca teve linha em
-- `companies`, porque saiu antes de o cadastro existir.
--
-- Sem esta tabela a producao nao enxerga saida nenhuma, o portao "Universo de
-- deslistadas" cai no caminho do painel e responde "nenhuma saida em 16
-- safras" -- que e verdade sobre o painel e mentira sobre o mercado.
--
-- `refuted_form` viaja junto de proposito. Publicar so as linhas nao refutadas
-- deixaria a vitrine sem como distinguir "esta saida nunca foi conferida" de
-- "esta saida foi conferida e negada", e a proxima republicacao teria de
-- confiar na memoria de quem publicou.

CREATE SCHEMA IF NOT EXISTS market_us;

CREATE TABLE IF NOT EXISTS market_us.delistings (
    cik                       BIGINT      PRIMARY KEY,
    company_id                BIGINT      NULL,
    symbol                    TEXT        NULL,
    symbol_source             TEXT        NULL,
    symbol_as_of              DATE        NULL,
    last_annual_report_year   INTEGER     NOT NULL,
    absence_year              INTEGER     NOT NULL,
    delisted_date             DATE        NOT NULL,
    reason                    TEXT        NOT NULL DEFAULT 'ausencia_de_relatorio_anual',
    source                    TEXT        NOT NULL DEFAULT 'sec_full_index',
    refuted_form              TEXT        NULL,
    refuted_date              DATE        NULL,
    checked_at                TIMESTAMPTZ NULL,
    derived_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS delistings_symbol_idx
    ON market_us.delistings (symbol) WHERE symbol IS NOT NULL;
CREATE INDEX IF NOT EXISTS delistings_absence_year_idx
    ON market_us.delistings (absence_year);

ALTER TABLE market_us.delistings ENABLE ROW LEVEL SECURITY;

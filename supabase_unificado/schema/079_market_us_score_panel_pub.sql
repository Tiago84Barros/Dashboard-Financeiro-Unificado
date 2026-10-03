-- 079 — market_us.score_panel_pub na VITRINE (Supabase)
--
-- O painel PIT dos EUA (`core/us_read.py::load_score_panel`) era montado a
-- cada visita a partir de score_vintages + prices_monthly + delisting_outcomes,
-- e isso foi o maior egress do Supabase no ciclo set/2026 (7,8 GB contra 5 GB
-- do plano free). Estas tabelas guardam o painel já montado (~22 mil linhas,
-- uns 2 MB) e a impressão das fontes no momento da montagem; o leitor só serve
-- o painel publicado se a impressão ainda bate com as fontes.
--
-- Escritor: scripts/publish_us_score_panel.py (cria as tabelas se faltarem).
CREATE SCHEMA IF NOT EXISTS market_us;

CREATE TABLE IF NOT EXISTS market_us.score_panel_pub (
    score_version  TEXT             NOT NULL,
    horizon_months INTEGER          NOT NULL,
    ordem          INTEGER          NOT NULL,
    date           DATE             NOT NULL,
    symbol         TEXT             NOT NULL,
    score          DOUBLE PRECISION,
    fwd_return     DOUBLE PRECISION,
    censored       BOOLEAN          NOT NULL,
    PRIMARY KEY (score_version, horizon_months, ordem)
);

CREATE TABLE IF NOT EXISTS market_us.score_panel_pub_meta (
    score_version  TEXT        NOT NULL,
    horizon_months INTEGER     NOT NULL,
    impressao      TEXT        NOT NULL,
    atributos      TEXT        NOT NULL,
    n_linhas       INTEGER     NOT NULL,
    publicado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (score_version, horizon_months)
);

ALTER TABLE market_us.score_panel_pub ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_us.score_panel_pub_meta ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON TABLE market_us.score_panel_pub, market_us.score_panel_pub_meta FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON TABLE market_us.score_panel_pub, market_us.score_panel_pub_meta FROM authenticated;
    END IF;
END $$;

COMMENT ON TABLE market_us.score_panel_pub IS
    'Painel PIT dos EUA já montado (date, symbol, score, fwd_return, censored), para o backtest sem reler safras e preços.';

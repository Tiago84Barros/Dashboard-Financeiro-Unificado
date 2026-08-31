-- 063_market_us_saida_causa.sql
--
-- Por que a empresa saiu da bolsa. Sem isso, "saiu" e um rotulo unico para dois
-- desfechos opostos: quem foi comprada com premio devolveu capital ao acionista
-- e quem pediu falencia destruiu.
--
-- A distincao nao e academica -- ela decide o numero. A medicao de retorno do
-- painel PIT descarta hoje 28% das linhas por falta de cotacao de ticker morto,
-- e descartar equivale a supor que essas empresas renderam a media das vivas.
-- Com a causa gravada, a convencao deixa de ser um chute unico e passa a ser
-- por desfecho.
--
-- Quem discrimina e o ITEM do 8-K, nao o tipo de formulario: `8-K` cobre desde
-- troca de auditor ate pedido de falencia. Numa sondagem de 60 saidas, 34
-- carregavam item 2.01 (aquisicao) e apenas 3 carregavam 1.03 (falencia) --
-- classificar por tipo de formulario deixava 34 aquisicoes passarem por morte.
--
-- `indefinido` e a maioria esperada, e e um valor legitimo, nao uma falha de
-- preenchimento: empurrar o nao-classificado para o lado "conservador" ja
-- inverteu uma medicao deste projeto.

ALTER TABLE market_us.assets
    ADD COLUMN IF NOT EXISTS delisting_cause TEXT NULL,
    ADD COLUMN IF NOT EXISTS delisting_cause_at TIMESTAMPTZ NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'assets_delisting_cause_ck') THEN
        ALTER TABLE market_us.assets
            ADD CONSTRAINT assets_delisting_cause_ck
            CHECK (delisting_cause IS NULL
                   OR delisting_cause IN ('adquirida', 'sumiu', 'indefinido'));
    END IF;
END $$;

COMMENT ON COLUMN market_us.assets.delisting_cause IS
    'Causa da saida da bolsa, apurada pelo item do 8-K: adquirida (item 2.01 na '
    'janela final, ou formulario de fusao/oferta arquivado pelo ALVO), sumiu '
    '(item 1.03 em qualquer momento -- falencia ou recuperacao judicial), '
    'indefinido (sem evidencia de nenhum dos dois; e o grupo majoritario e nao '
    'deve ser tratado como morte). NULL = nao classificada ainda. Regra unica '
    'em core/us_saida_causa.py.';

COMMENT ON COLUMN market_us.assets.delisting_cause_at IS
    'Quando a causa foi apurada. Reclassificar exige nova consulta a SEC, e sem '
    'esta data nao da para saber se a classificacao e anterior a um 8-K novo.';

CREATE INDEX IF NOT EXISTS assets_delisting_cause_idx
    ON market_us.assets (delisting_cause) WHERE delisting_cause IS NOT NULL;

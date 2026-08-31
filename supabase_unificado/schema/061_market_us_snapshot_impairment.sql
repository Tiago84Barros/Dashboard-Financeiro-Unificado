-- 061_market_us_snapshot_impairment.sql
--
-- Publicar o MOTIVO de o selo de decisao nao ter sido dado.
--
-- `impairment_flags` nasce em `core/us_metrics.py`, trava o `decision_grade`
-- em `core/us_score.py` (portao A-101) e morre ali: a vitrine nunca carregou a
-- coluna. O efeito na tela e uma frase falsa. Medido em 31/08/2026 sobre as
-- 2.626 empresas ativas, 731 estao em `research_grade` com TODAS as trilhas
-- criticas cobertas e confianca >= 75 -- ou seja, o dado esta completo -- e a
-- tela lhes diz "a leitura abaixo e limitada pelo que falta". Nao falta nada.
-- O que ha e patrimonio liquido negativo, EBITDA nao positivo ou capital
-- investido negativo: um veredito sobre a empresa, e nao uma lacuna sobre o
-- dado. Trocar um pelo outro faz o investidor ler "nao sei" onde a analise
-- na verdade diz "sei, e e ruim".

ALTER TABLE market_us.company_snapshots
    ADD COLUMN IF NOT EXISTS impairment_flags JSONB;

COMMENT ON COLUMN market_us.company_snapshots.impairment_flags IS
    'Marcas de balanco quebrado (A-101): patrimonio_liquido_negativo, '
    'ebitda_nao_positivo, capital_investido_negativo. Nao vazia = o selo de '
    'decisao foi travado por VEREDITO sobre a empresa, e nao por falta de '
    'dado. Vazia com critical_missing vazio = o dado sustenta a decisao.';

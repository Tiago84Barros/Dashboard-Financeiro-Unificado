-- 054_market_us_field_filings.sql
-- Data de arquivamento POR CAMPO nas demonstracoes EUA (A-159).
--
-- Ja existia `available_at`, que carimba a linha inteira com o filing mais
-- tardio entre seus campos. E conservador, mas de um jeito que depende do
-- FUTURO da empresa: um campo do exercicio de 2012 que so estreou no 10-K de
-- 2015 torna a linha de 2012 inteira invisivel para toda safra anterior a 2015.
-- Quem seguiu arquivando ate hoje teve dez anos de chances de estrear uma tag
-- nova; quem morreu em 2013 nao teve nenhuma. Medido na coorte de 2012, a
-- cobertura media ficou em 36% para sobreviventes contra 51% para quem sumiu --
-- o painel enxerga MENOS dado de quem chegou ate hoje.
--
-- `filed_at` guarda `{"revenue": "2013-02-28", ...}`, o que permite a regra
-- point-in-time por campo (`core.us_pit`) responder "o que se sabia neste dia"
-- sem consultar nada posterior a ele. Fica FORA do `content_hash`: o hash
-- identifica os insumos financeiros da linha, e misturar procedencia nele faria
-- a base inteira parecer alterada na proxima ingestao.
--
-- Idempotente. Linhas ingeridas antes desta coluna ficam com NULL, e a regra
-- por campo cai de volta para a regra por linha nelas -- dado antigo nao pode
-- fingir uma procedencia que nao foi registrada.

ALTER TABLE market_us.income_statements
    ADD COLUMN IF NOT EXISTS filed_at JSONB;
ALTER TABLE market_us.balance_sheets
    ADD COLUMN IF NOT EXISTS filed_at JSONB;
ALTER TABLE market_us.cash_flow_statements
    ADD COLUMN IF NOT EXISTS filed_at JSONB;

COMMENT ON COLUMN market_us.income_statements.filed_at IS
    'Data de arquivamento SEC por campo. NULL = ingerido antes de 054; use available_at.';
COMMENT ON COLUMN market_us.balance_sheets.filed_at IS
    'Data de arquivamento SEC por campo. NULL = ingerido antes de 054; use available_at.';
COMMENT ON COLUMN market_us.cash_flow_statements.filed_at IS
    'Data de arquivamento SEC por campo. NULL = ingerido antes de 054; use available_at.';

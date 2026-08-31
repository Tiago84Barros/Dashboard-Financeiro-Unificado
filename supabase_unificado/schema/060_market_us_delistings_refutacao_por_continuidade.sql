-- 060_market_us_delistings_refutacao_por_continuidade.sql
--
-- Uma segunda porta de refutacao, porque a primeira nao alcanca o caso mais
-- comum.
--
-- `refuted_form` guarda relatorio anual arquivado sob o MESMO CIK em ano igual
-- ou posterior ao da ausencia. Na reorganizacao societaria isso nunca aparece:
-- o registrante antigo para de arquivar para sempre, e quem passa a arquivar e
-- um CIK novo. Medido no armazem em 31/08/2026, das 60 saidas ja nomeadas que
-- tinham cotacao, 60 seguiam negociando -- BlackRock (1364742 -> 2012383),
-- Bunge (1144519 -> 1996862), Ferguson (1832433 -> 2011641), Noble (1169055 ->
-- 1895262), Apollo (1411494). Uma unica delas estava marcada como refutada.
-- O CIK morreu; a empresa nao. O acionista trocou de papel um-para-um.
--
-- `refuted_by` diz QUAL evidencia derrubou a saida, e passa a ser o filtro
-- canonico -- `refuted_form IS NULL` deixa de bastar porque a refutacao por
-- continuidade nao tem forma de relatorio para citar.

ALTER TABLE market_us.delistings
    ADD COLUMN IF NOT EXISTS refuted_by TEXT NULL;

UPDATE market_us.delistings
   SET refuted_by = 'relatorio_anual_posterior'
 WHERE refuted_form IS NOT NULL AND refuted_by IS NULL;

COMMENT ON COLUMN market_us.delistings.refuted_by IS
    'Qual evidencia de vida derrubou a saida: relatorio_anual_posterior (mesmo '
    'CIK volta a arquivar) ou ticker_negociado_apos_saida (o papel seguiu '
    'negociando com continuidade em volta da data -- sucessao de registrante, '
    'nao morte da empresa). NULL = nao refutada. Assimetrica: encontrar vida '
    'refuta; nao encontrar nao confirma a morte, porque o armazem so tem preco '
    'de quem sobreviveu.';

CREATE INDEX IF NOT EXISTS delistings_refutadas_por_idx
    ON market_us.delistings (refuted_by) WHERE refuted_by IS NOT NULL;

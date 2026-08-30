-- 058_market_us_delistings_simbolo.sql
--
-- Nomear a saida, e registrar quando ela foi refutada.
--
-- `market_us.delistings` nasceu com 12.107 linhas e DUAS com simbolo. Saida sem
-- simbolo nao acha preco nem safra: ela conta, mas nao entra no backtest -- o
-- painel segue 100% sobrevivente com o registro da mortalidade ao lado.
--
-- As colunas abaixo separam tres coisas que estavam juntas em `symbol`:
--
--   * `symbol`          -- o ticker, venha ele do cadastro ou da capa do 10-K;
--   * `symbol_source`   -- de onde ele veio. Importa porque `tickers` do
--                          `submissions.json` da SEC e esvaziado quando a
--                          empresa para de arquivar: resolver por ali nomearia
--                          so quem sobreviveu, o vies que este trabalho desfaz;
--   * `symbol_as_of`    -- a data do documento de onde o simbolo saiu, para que
--                          um ticker reciclado por outra empresa depois da
--                          saida nao seja confundido com o desta.
--
-- `refuted_form`/`refuted_date` guardam a evidencia de VIDA encontrada em ano
-- igual ou posterior ao da ausencia. Guardar, e nao apagar a linha: a saida foi
-- derivada de um indice, a refutacao vem do historico da propria entidade, e
-- quem re-derivar amanha precisa saber que esta linha ja foi contestada -- se
-- ela sumisse, a proxima varredura a recriaria identica.

ALTER TABLE market_us.delistings
    ADD COLUMN IF NOT EXISTS symbol_source TEXT NULL,
    ADD COLUMN IF NOT EXISTS symbol_as_of  DATE NULL,
    ADD COLUMN IF NOT EXISTS refuted_form  TEXT NULL,
    ADD COLUMN IF NOT EXISTS refuted_date  DATE NULL,
    ADD COLUMN IF NOT EXISTS checked_at    TIMESTAMPTZ NULL;

COMMENT ON COLUMN market_us.delistings.symbol_source IS
    'Procedencia do ticker: dei:TradingSymbol (capa em XBRL inline do relatorio '
    'anual) ou cadastro. Nunca o campo tickers do submissions.json, que a SEC '
    'esvazia quando a empresa para de arquivar.';

COMMENT ON COLUMN market_us.delistings.refuted_form IS
    'Forma do relatorio anual arquivado em ano igual ou posterior ao da '
    'ausencia. Preenchida = a saida foi contestada pela propria SEC e nao deve '
    'entrar em backtest como perda.';

COMMENT ON COLUMN market_us.delistings.checked_at IS
    'Quando a linha foi confrontada com o submissions.json da entidade. NULL = '
    'nunca confrontada; a ausencia continua valendo pelo indice, apenas sem '
    'segunda fonte.';

CREATE INDEX IF NOT EXISTS delistings_symbol_idx
    ON market_us.delistings (symbol) WHERE symbol IS NOT NULL;
CREATE INDEX IF NOT EXISTS delistings_refutadas_idx
    ON market_us.delistings (refuted_date) WHERE refuted_form IS NOT NULL;

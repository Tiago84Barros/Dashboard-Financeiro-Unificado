-- 089: cotacao corrente das acoes/units da B3 em market.b3_cotacao_atual
-- (11/11/2026). A cota gratuita da brapi acabou em 29/10 e o "preco atual" da
-- acao, que e a ultima linha de market.historical_prices, congelou naquele dia
-- enquanto o COTAHIST seguia diario. historical_prices e retroajustada por
-- split e o COTAHIST e cru: o close cru NAO entra naquela serie. Ele ocupa este
-- slot, e o leitor (`cotacao_pregao.preco_vigente`) escolhe o mais novo entre a
-- ultima barra e o slot. Escrito por `run_market_ingest.py acoes-cotahist`
-- (armazem local) e levado ao Supabase por scripts/publish_b3_cotacao_from_local.py.
--
-- ADITIVA e idempotente (CREATE ... IF NOT EXISTS). NAO APLICADA: exige
-- autorizacao humana, no armazem local E no Supabase. ~500 linhas, desprezivel
-- frente aos 500 MB do plano free.
-- Backup: nada a fazer (tabela nova). Se ja existir de outra origem:
--   pg_dump -t market.b3_cotacao_atual antes de aplicar.
-- Rollback: DROP TABLE IF EXISTS market.b3_cotacao_atual;
-- Compatibilidade: todo leitor testa to_regclass('market.b3_cotacao_atual') e,
-- sem a tabela, cai na ultima barra como antes, com a ausencia nomeada no log.

CREATE TABLE IF NOT EXISTS market.b3_cotacao_atual (
    ticker        TEXT PRIMARY KEY,
    price         NUMERIC(18,6) NOT NULL CHECK (price > 0),
    price_at      TIMESTAMPTZ   NOT NULL,
    trade_date    DATE          NOT NULL,
    price_source  TEXT          NOT NULL DEFAULT 'b3_cotahist',
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market.b3_cotacao_atual IS
  'Fechamento cru mais novo do COTAHIST (preco por unidade) por acao/unit; vence a ultima barra de historical_prices quando mais novo. Nao e serie.';
COMMENT ON COLUMN market.b3_cotacao_atual.price_at IS
  'Instante do close: o dia do pregao as 18h de Sao Paulo.';
DROP TRIGGER IF EXISTS trg_b3_cotacao_atual_updated ON market.b3_cotacao_atual;
CREATE TRIGGER trg_b3_cotacao_atual_updated BEFORE UPDATE ON market.b3_cotacao_atual
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();

-- 074_preco_medio_informado.sql — preço médio que o INVESTIDOR declara.
--
-- Rodar À MÃO no SQL Editor do Supabase. Idempotente: pode rodar de novo.
--
-- Por que uma tabela nova, e não uma coluna:
--
-- As duas fontes de custo que o app tem são reescritas por inteiro a cada
-- ciclo. `portfolio_positions` é recalculada do zero a partir das notas
-- (`positions.recompute_for_user`, que desde 2026-09-23 também APAGA o que
-- deixou de qualificar), e as tabelas de snapshot são substituídas a cada
-- planilha subida. Um campo em qualquer uma das duas seria apagado no
-- próximo upload — exatamente o que não pode acontecer com um número que só
-- existe porque a pessoa o digitou.
--
-- Por que a chave é o TICKER e não o asset_id:
--
-- `assets` ganha linhas novas quando uma planilha traz um papel com grafia
-- diferente, e o app agrega a carteira por ticker-base (BBAS3F entra em
-- BBAS3). Amarrar ao asset_id faria a declaração do usuário sumir no dia em
-- que a corretora mudasse a grafia. O ticker aqui é sempre a forma base,
-- MAIÚSCULA e sem o sufixo `F` do fracionário.
--
-- O que este número é e o que ele NÃO é:
--
-- É a estimativa do próprio investidor, para o caso em que nem o extrato da
-- B3 nem as notas importadas sabem o preço médio real -- o BBAS3 é o caso
-- que originou isto: o valor que aparecia como declarado pela B3 tinha sido
-- digitado à mão numa planilha. Quem lê isto na tela vê "informado por
-- você", nunca "declarado pela B3". A procedência viaja com o número.

CREATE TABLE IF NOT EXISTS investment_manual_costs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID          NOT NULL,
    ticker         TEXT          NOT NULL,
    average_price  NUMERIC(20,6) NOT NULL,
    note           TEXT,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT investment_manual_costs_preco_positivo
        CHECK (average_price > 0),
    CONSTRAINT investment_manual_costs_uk
        UNIQUE (user_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_investment_manual_costs_user
    ON investment_manual_costs (user_id);

COMMENT ON TABLE investment_manual_costs IS
    'Preço médio declarado pelo próprio investidor, por ticker-base. '
    'Sobrevive a recálculo de posições e a upload de planilha. '
    'Sempre exibido como "informado por você".';
COMMENT ON COLUMN investment_manual_costs.ticker IS
    'Ticker-base em MAIÚSCULAS, sem o sufixo F do fracionário.';
COMMENT ON COLUMN investment_manual_costs.average_price IS
    'Preço médio em BRL -- a mesma moeda em que o card exibe o custo.';

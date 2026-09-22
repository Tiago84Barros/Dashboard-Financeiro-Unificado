-- 073_provisioned_income.sql
-- Proventos PROVISIONADOS (declarados, ainda nao pagos).
--
-- Por que nao vai em `dividends`
-- ------------------------------
-- `dividends` guarda provento RECEBIDO: dinheiro que entrou, com data de
-- pagamento no passado. O extrato "Posicao Detalhada" da B3 traz outra coisa:
-- o que a empresa DECLAROU e ainda vai pagar -- PSSA3 com previsao para
-- 31/12/2027, por exemplo. Misturar os dois inflaria o rendimento realizado
-- de qualquer tela que some `dividends`, e o numero ficaria certo hoje e
-- errado amanha, sem erro visivel.
--
-- Esta tabela e INFORMATIVA: nada em `core/` decide com ela. Ela existe para
-- a tela poder mostrar "R$ 863,71 provisionados" ao lado do patrimonio.
--
-- Uma provisao nao e um fato estavel: a empresa remarca data e valor, e o
-- extrato seguinte traz a versao nova. Por isso a linha e amarrada a
-- `report_date` -- a foto do dia em que aquela provisao foi observada --, e
-- nao a uma identidade do evento. Duas fotos do mesmo provento convivem, e a
-- tela le a mais recente.
--
-- DEPENDENCIAS: 003_investment_tables.sql (profiles, portfolios, assets)
-- Sem RLS, espelhando 010_portfolio_position_snapshots.sql.

CREATE TABLE IF NOT EXISTS portfolio_provisioned_income (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID         NOT NULL REFERENCES profiles(id)   ON DELETE CASCADE,
    portfolio_id UUID         NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    asset_id     UUID         NOT NULL REFERENCES assets(id)     ON DELETE RESTRICT,

    report_date      DATE     NOT NULL,
    payment_forecast DATE,

    event_label  VARCHAR(80)  NOT NULL,
    income_type  VARCHAR(30)  NOT NULL,

    quantity     NUMERIC(18,8),
    gross_amount NUMERIC(15,2),
    net_amount   NUMERIC(15,2),

    currency     CHAR(3)      NOT NULL DEFAULT 'BRL',

    source_system VARCHAR(30) NOT NULL DEFAULT 'app4',
    source_table  VARCHAR(50) NOT NULL DEFAULT 'b3_posicao_detalhada',
    source_id     TEXT        NOT NULL,
    imported_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (portfolio_id, asset_id, report_date,
            source_system, source_table, source_id)
);

COMMENT ON TABLE portfolio_provisioned_income IS
    'Proventos declarados e ainda nao pagos, por foto de extrato. INFORMATIVA: '
    'nunca somar com `dividends`, que guarda provento recebido.';
COMMENT ON COLUMN portfolio_provisioned_income.report_date IS
    'Data do extrato que observou a provisao. A provisao muda; a foto nao.';
COMMENT ON COLUMN portfolio_provisioned_income.payment_forecast IS
    'Previsao de pagamento informada pelo extrato. Pode ser remarcada.';
COMMENT ON COLUMN portfolio_provisioned_income.event_label IS
    'Texto do extrato, sem normalizar: DIVI, JURO, DIVIDENDO, JUROS SOBRE '
    'CAPITAL PROPRIO. `income_type` e a leitura derivada dele.';
COMMENT ON COLUMN portfolio_provisioned_income.source_id IS
    'Hash de (data, ticker, evento, previsao, valor bruto) -- separa as duas '
    'linhas de PETR3 do mesmo dia (DIVI e JURO).';

CREATE INDEX IF NOT EXISTS idx_provisioned_income_user_date
    ON portfolio_provisioned_income (user_id, report_date DESC);

CREATE INDEX IF NOT EXISTS idx_provisioned_income_forecast
    ON portfolio_provisioned_income (payment_forecast);

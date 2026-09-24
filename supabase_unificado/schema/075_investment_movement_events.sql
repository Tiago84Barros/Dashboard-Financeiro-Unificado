-- 075_investment_movement_events.sql — cada linha da Movimentação da B3, crua.
--
-- NÃO precisa rodar à mão: o importador `b3_movimentacao` cria a tabela
-- sozinho (CREATE TABLE IF NOT EXISTS) no primeiro upload. Este arquivo existe
-- para o schema ficar versionado. Idempotente.
--
-- Por que guardar a linha crua, e não mais um tipo em investment_transactions:
--
-- Bonificação, desdobro, grupamento, fração, subscrição e transferência mudam
-- a quantidade SEM o dinheiro de uma compra. Gravados como buy/sell com preço
-- zero, eles derrubavam o preço médio (PSSA3 caiu para R$ 3,44 em 2026-05) e
-- por isso eram descartados no upload -- e o arquivo não fica guardado em
-- lugar nenhum. Resultado: o extrato de negociação fechava com a posição em só
-- 6 de 15 ativos, e não havia como descobrir por quê sem pedir o arquivo de
-- novo.
--
-- Aqui fica o FATO (rótulo, sentido, quantidade, preço e valor como a B3
-- publicou). A INTERPRETAÇÃO -- o que conta como quantidade, o que é caixa --
-- mora em `core/movimentacao_b3.py`, que pode ser corrigida sem novo upload.

CREATE TABLE IF NOT EXISTS investment_movement_events (
    id           BIGSERIAL PRIMARY KEY,
    user_id      UUID          NOT NULL,
    event_date   DATE          NOT NULL,
    movement     TEXT          NOT NULL,
    direction    TEXT          NOT NULL DEFAULT '',
    ticker       TEXT          NOT NULL,
    product      TEXT,
    institution  TEXT,
    quantity     NUMERIC(24,8),
    unit_price   NUMERIC(20,6),
    total_value  NUMERIC(20,2),
    external_id  VARCHAR(64)   NOT NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT investment_movement_events_external_id_key UNIQUE (external_id)
);

CREATE INDEX IF NOT EXISTS ix_investment_movement_events_user_date
    ON investment_movement_events (user_id, event_date);

-- 083_dividends_quantidade_opcional.sql — provento sem quantidade fica sem
-- quantidade.
--
-- Rodar à mão no SQL Editor do Supabase (e no warehouse local, se ele espelha
-- `dividends`). O app NÃO aplica este arquivo sozinho: migração pede
-- confirmação humana (AGENTS.md). Até ele rodar, os importadores recusam o
-- provento sem quantidade e o nomeiam no resultado da importação, em vez de
-- inventar a quantidade (`common.dividends_aceita_quantidade_ausente`).
--
-- Por que existe (06/11/2026):
--
-- `quantity` e `amount_per_unit` eram NOT NULL. Fonte que não informa a
-- quantidade tinha de pôr algum número, e dois importadores punham 1 cota com
-- o pagamento inteiro como valor por cota:
--
--   * Movimentação da B3, quando a linha vinha sem "Quantidade";
--   * Consolidado da XP, em TODO provento ("XP nao separa por cota").
--
-- A tela de Proventos exibia "Qtd. 1,00" e "R$/Unit = total" como fato. Pior:
-- a deduplicação canônica (ativo, data, tipo, total) barrava a fonte que
-- chegasse depois com a quantidade certa, então o número inventado ficava.
-- Agora a linha grava os dois vazios, e quem chega com a quantidade completa
-- a linha (`common.gravar_proventos`).
--
-- Reversão do passo 1: só é possível enquanto nenhuma linha tiver os campos
-- vazios. Se for preciso voltar, primeiro desfaça o passo 2 (abaixo) e
-- confira que `SELECT COUNT(*) FROM dividends WHERE quantity IS NULL` é 0;
-- depois:
--     ALTER TABLE dividends DROP CONSTRAINT IF EXISTS dividends_qtd_e_unitario_juntos;
--     ALTER TABLE dividends ALTER COLUMN quantity SET NOT NULL;
--     ALTER TABLE dividends ALTER COLUMN amount_per_unit SET NOT NULL;

-- ── Passo 1: aceitar o vazio (idempotente, não apaga nem altera dado) ──────

ALTER TABLE dividends ALTER COLUMN quantity        DROP NOT NULL;
ALTER TABLE dividends ALTER COLUMN amount_per_unit DROP NOT NULL;

-- Os dois andam juntos: valor por unidade sem quantidade (ou o contrário)
-- seria um número sem base.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dividends_qtd_e_unitario_juntos'
    ) THEN
        ALTER TABLE dividends ADD CONSTRAINT dividends_qtd_e_unitario_juntos
            CHECK ((quantity IS NULL) = (amount_per_unit IS NULL));
    END IF;
END $$;

COMMENT ON COLUMN dividends.quantity IS
    'Quantidade em custódia que recebeu o provento. NULL quando a fonte não '
    'informa (nunca 1 por convenção); outra fonte com a quantidade completa a linha.';
COMMENT ON COLUMN dividends.amount_per_unit IS
    'total_amount / quantity, 6 casas. NULL junto com quantity.';

-- ── Passo 2 (OPCIONAL, manual): limpar as quantidades inventadas antigas ──
--
-- Altera dado do usuário: rode só depois de conferir a contagem e com o
-- backup criado. Fica comentado de propósito.
--
-- Quais linhas são certamente inventadas:
--   * `xpcsl-inc-%`: o importador da XP gravava 1 cota em todas, sem exceção;
--   * `b3mov-%` com quantity = 1 e amount_per_unit = total cuja linha crua na
--     Movimentação (`investment_movement_events`, SQL 075) não tem
--     quantidade. Sem a linha crua (importação anterior a 24/09/2026) não dá
--     para separar o inventado de quem tinha mesmo 1 cota, e a linha fica.
--
-- Depois do passo 2, reimportar a Movimentação da B3 (e o Consolidado da XP,
-- que traz a coluna "Quantidade") completa as quantidades que as fontes têm.
--
-- 2a) Conferir:
--
-- SELECT d.external_id, a.ticker, d.payment_date, d.quantity,
--        d.amount_per_unit, d.total_amount
-- FROM dividends d JOIN assets a ON a.id = d.asset_id
-- WHERE d.quantity = 1 AND d.amount_per_unit = d.total_amount
--   AND (d.external_id LIKE 'xpcsl-inc-%'
--        OR (d.external_id LIKE 'b3mov-%' AND EXISTS (
--              SELECT 1 FROM investment_movement_events e
--              WHERE e.user_id = d.user_id AND e.ticker = a.ticker
--                AND e.event_date = d.payment_date
--                AND ROUND(e.total_value, 2) = ROUND(d.total_amount, 2)
--                AND e.quantity IS NULL)));
--
-- 2b) Backup verificável (mesma condição; conferir que o COUNT bate com 2a):
--
-- CREATE TABLE dividends_bkp_083 AS
-- SELECT d.* FROM dividends d JOIN assets a ON a.id = d.asset_id
-- WHERE <mesma condição de 2a>;
-- SELECT COUNT(*) FROM dividends_bkp_083;
--
-- 2c) Limpar:
--
-- UPDATE dividends d SET quantity = NULL, amount_per_unit = NULL
-- WHERE d.id IN (SELECT id FROM dividends_bkp_083);
--
-- Reversão do passo 2:
--
-- UPDATE dividends d SET quantity = b.quantity, amount_per_unit = b.amount_per_unit
-- FROM dividends_bkp_083 b WHERE b.id = d.id AND d.quantity IS NULL;

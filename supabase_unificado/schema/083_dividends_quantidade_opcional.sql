-- 083_dividends_quantidade_opcional.sql — provento sem quantidade fica sem
-- quantidade.
--
-- Idempotente. Roda À MÃO no SQL Editor do Supabase, depois de um backup:
-- muda o schema e limpa dados da XP, e AGENTS.md exige confirmação humana para
-- migração. O app não a executa; até ela rodar, os importadores recusam o
-- provento sem quantidade e o nomeiam em `errors`
-- (`common.dividends_aceita_quantidade_vazia`).
--
-- Por que existe: `quantity` e `amount_per_unit` eram NOT NULL, e quando o
-- arquivo não trazia a quantidade os importadores gravavam 1 cota com o
-- pagamento inteiro como valor por cota (B3 Movimentação sem "Quantidade",
-- Consolidado da XP, que ignorava a coluna que tem). A tela de Proventos
-- mostrava "Qtd. 1 · R$/Unit 346,32" como fato, e a deduplicação por chave
-- canônica impedia que a fonte com a quantidade certa corrigisse a linha.
-- AGENTS.md: "Dados ausentes permanecem ausentes."
--
-- Depois desta migração:
--   * quantidade desconhecida grava NULL nas duas colunas;
--   * `quantity_placeholder` marca, uma vez só, as linhas que o importador
--     antigo pode ter inventado (1 cota = total). Só elas podem ser
--     completadas ou esvaziadas por uma importação posterior. O padrão
--     numérico sozinho não distingue o placeholder de uma posição real de
--     1 cota, e quem preenche ou confirma a quantidade desliga a marca;
--   * `total_amount` continua obrigatório: é o único número que todas as
--     fontes publicam.
--
-- O app NÃO grava provento da XP antes desta migração (o importador recusa
-- e avisa). Por isso toda linha xpcsl-inc-* que existe na transição veio do
-- importador antigo, que gravava sempre 1 cota = total, e pode ser limpa.

ALTER TABLE dividends
    ADD COLUMN IF NOT EXISTS quantity_placeholder BOOLEAN NOT NULL DEFAULT FALSE;

-- Limpeza e marcação rodam UMA vez, na transição de NOT NULL para NULL: uma
-- segunda execução deste arquivo não pode tocar linha gravada depois.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dividends' AND column_name = 'quantity'
          AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE dividends ALTER COLUMN quantity        DROP NOT NULL;
        ALTER TABLE dividends ALTER COLUMN amount_per_unit DROP NOT NULL;

        -- Proventos da XP gravados pelo importador antigo: todos inventados.
        UPDATE dividends
           SET quantity = NULL, amount_per_unit = NULL
         WHERE left(external_id, 10) = 'xpcsl-inc-'
           AND quantity = 1
           AND amount_per_unit = total_amount;

        -- Das demais fontes, "1 cota = total" pode ser inventado ou real.
        -- Fica marcado como suspeito: a próxima fonte que trouxer a
        -- quantidade decide.
        UPDATE dividends
           SET quantity_placeholder = TRUE
         WHERE quantity = 1
           AND amount_per_unit = total_amount;
    END IF;
END $$;

-- amount_per_unit sozinho, para bancos em que só ele ficou NOT NULL.
ALTER TABLE dividends ALTER COLUMN amount_per_unit DROP NOT NULL;

COMMENT ON COLUMN dividends.quantity IS
    'Quantidade em custódia que gerou o provento. NULL quando a fonte não informa.';
COMMENT ON COLUMN dividends.amount_per_unit IS
    'total_amount / quantity. NULL quando a quantidade é desconhecida.';
COMMENT ON COLUMN dividends.quantity_placeholder IS
    'TRUE: gravado antes da 083 como 1 cota = total, talvez inventado. Uma fonte com a quantidade completa a linha e desliga a marca.';

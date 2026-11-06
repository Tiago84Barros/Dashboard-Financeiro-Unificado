-- 083_dividends_quantidade_opcional.sql — provento sem quantidade fica sem
-- quantidade.
--
-- Idempotente. O app roda o ALTER sozinho a cada importação de investimentos
-- (`data_pipeline/importers/investments/common.py::
-- ensure_dividend_quantity_nullable`); rodar à mão no SQL Editor do Supabase
-- também funciona, e é o caminho se o usuário do app não tiver permissão de
-- DDL.
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
--   * uma fonte posterior com a quantidade preenche a linha que estava NULL
--     (ou com o padrão inventado 1 cota = total), pela chave canônica;
--   * `total_amount` continua obrigatório — é o único número que todas as
--     fontes publicam.

-- A limpeza dos proventos da XP roda UMA vez, na transição de NOT NULL para
-- NULL: o importador antigo gravava toda linha da XP como 1 cota = total, mas
-- o novo lê a quantidade, e uma posição real de 1 cota gravada depois desta
-- migração não pode ser apagada por uma segunda execução deste arquivo.
DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'dividends' AND column_name = 'quantity'
          AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE dividends ALTER COLUMN quantity        DROP NOT NULL;
        ALTER TABLE dividends ALTER COLUMN amount_per_unit DROP NOT NULL;

        -- Proventos do Consolidado da XP gravados antes: todos inventados.
        -- Ficam NULL até o Consolidado (que agora lê a quantidade) ou a
        -- Movimentação da B3 preencherem pela chave canônica.
        UPDATE dividends
           SET quantity = NULL, amount_per_unit = NULL
         WHERE left(external_id, 10) = 'xpcsl-inc-'
           AND quantity = 1
           AND amount_per_unit = total_amount;
    END IF;
END $$;

-- amount_per_unit sozinho, para bancos em que só ele ficou NOT NULL.
ALTER TABLE dividends ALTER COLUMN amount_per_unit DROP NOT NULL;

COMMENT ON COLUMN dividends.quantity IS
    'Quantidade em custódia que gerou o provento. NULL quando a fonte não informa.';
COMMENT ON COLUMN dividends.amount_per_unit IS
    'total_amount / quantity. NULL quando a quantidade é desconhecida.';

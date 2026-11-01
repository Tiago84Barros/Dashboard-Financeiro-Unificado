-- 082_conciliacao_bancaria.sql — conciliação do extrato bancário importado.
--
-- Idempotente. O app roda estas instruções sozinho na primeira leitura ou
-- gravação de extrato (`core.bank_statement_import.DDL_SQL`); rodar à mão no
-- SQL Editor do Supabase também funciona.
--
-- Por que existe:
--
-- 1. Cada linha do extrato virava um lançamento novo. Quem lança à mão e
--    também importa o extrato ficava com a mesma saída duas vezes, e
--    `v_account_balance` inflava em silêncio. Agora o movimento importado
--    pode apontar (`transaction_id`) para o lançamento manual que já existia;
--    `conciliacao` diz qual dos dois caminhos ele seguiu:
--      'vinculada' -> casou com lançamento manual existente (nada foi criado)
--      'criada'    -> a importação criou o lançamento
--      NULL        -> importado antes da 082, ou ainda pendente de categoria
--
-- 2. O saldo final impresso no extrato é a prova de que o app fecha com o
--    banco. `bank_statement_balances` guarda esse saldo por conta e data; a
--    diferença contra o app (initial_balance + lançamentos pagos até a data)
--    é recalculada a cada leitura, nunca gravada -- ela muda quando um
--    pendente é classificado.

ALTER TABLE bank_statement_movements
    ADD COLUMN IF NOT EXISTS conciliacao TEXT;

CREATE TABLE IF NOT EXISTS bank_statement_balances (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    account_id     UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    banco          TEXT NOT NULL,
    data_saldo     DATE NOT NULL,
    saldo_extrato  NUMERIC(15,2) NOT NULL,
    origem_arquivo TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, account_id, data_saldo)
);

ALTER TABLE bank_statement_balances ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'bank_statement_balances'
          AND policyname = 'bank_statement_balances_owner_all'
    ) THEN
        CREATE POLICY bank_statement_balances_owner_all ON bank_statement_balances
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

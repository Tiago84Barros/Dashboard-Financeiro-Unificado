-- 080_posicao_anterior_extrato.sql — posição que o investidor JÁ TINHA antes
-- do primeiro extrato de negociação da B3.
--
-- Idempotente. O app roda este arquivo sozinho na primeira gravação
-- (`core.posicao_anterior.garantir_tabela`); rodar à mão no SQL Editor do
-- Supabase também funciona.
--
-- Por que existe:
--
-- O relatório de Negociação da B3 começa em nov/2019. Uma venda de ativo
-- comprado antes disso não tem custo conhecido, e o lucro dela fica fora do
-- "Ganho total" da Evolução Patrimonial e da apuração do IR. O investidor
-- sabe o número: é a linha do ativo em Bens e Direitos da declaração de IR
-- (quantidade e custo total em 31/12 do ano anterior ao extrato).
--
-- Por que não reaproveitar `investment_manual_costs` (074):
--
-- Lá mora o preço médio da posição ATUAL, que a carteira exibe. Aqui mora o
-- saldo de ABERTURA -- quantidade e custo antes da primeira nota -- que só
-- entra no cálculo de lucro realizado. São números diferentes do mesmo
-- ticker: misturar faria a declaração de um corromper o outro.
--
-- Chave pelo ticker-base (sem o F do fracionário), pelo mesmo motivo da 074.

CREATE TABLE IF NOT EXISTS investment_opening_positions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID          NOT NULL,
    ticker      TEXT          NOT NULL,
    quantity    NUMERIC(20,6) NOT NULL,
    total_cost  NUMERIC(20,2) NOT NULL,
    note        TEXT,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT investment_opening_positions_qtd_positiva CHECK (quantity > 0),
    CONSTRAINT investment_opening_positions_custo_positivo CHECK (total_cost > 0),
    CONSTRAINT investment_opening_positions_uk UNIQUE (user_id, ticker)
);

COMMENT ON TABLE investment_opening_positions IS
    'Posição de abertura declarada pelo investidor: o que ele tinha antes do '
    'primeiro extrato de negociação da B3. Entra só no lucro realizado.';

-- RLS no padrão da 074: sem isto a tabela nasce legível pela API REST com a
-- chave anônima. O app conecta como `postgres`, que ignora RLS.
ALTER TABLE investment_opening_positions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'investment_opening_positions'
          AND policyname = 'opening_positions_owner_all'
    ) THEN
        CREATE POLICY opening_positions_owner_all ON investment_opening_positions
            FOR ALL
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

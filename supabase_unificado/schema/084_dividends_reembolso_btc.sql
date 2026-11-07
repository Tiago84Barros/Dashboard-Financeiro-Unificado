-- 084_dividends_reembolso_btc.sql — reembolso de aluguel de ações (BTC) ganha
-- tipo próprio.
--
-- Idempotente. Roda À MÃO no SQL Editor do Supabase, depois de um backup:
-- muda a CHECK de `dividends.type` e reclassifica linhas, e AGENTS.md exige
-- confirmação humana para migração. O app não a executa
-- (`common.migracao_084_aplicada` só consulta o catálogo).
--
-- Por que existe: a Movimentação da B3 lança como "Reembolso" o valor que quem
-- tomou suas ações emprestadas paga no lugar do dividendo ou do JCP. O
-- importador gravava isso como `reit_income`, que a tela rotula "Rendimento
-- FII" -- 30 linhas de BBAS3, PETR3, SBSP3 & cia. em 06/11/2026. O total
-- recebido estava certo; o rótulo era falso, e o IRPF não tinha como
-- distinguir o reembolso de um rendimento de verdade.
--
-- Depois desta migração:
--   * `lending_reimbursement` passa a ser um tipo aceito;
--   * as linhas da Movimentação cujo lançamento cru (investment_movement_events)
--     é "reembolso" mudam de `reit_income` para `lending_reimbursement`;
--   * as demais (importadas antes de existir o lançamento cru, ou vindas da
--     XP) mudam na próxima importação do mesmo arquivo -- o importador corrige
--     o tipo pelo external_id.

DO $$
DECLARE
    nome text;
BEGIN
    -- A CHECK do tipo, seja qual for o nome que ela ganhou.
    FOR nome IN
        SELECT c.conname
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = 'public' AND t.relname = 'dividends'
           AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%reit_income%'
           AND pg_get_constraintdef(c.oid) NOT LIKE '%lending_reimbursement%'
    LOOP
        EXECUTE format('ALTER TABLE public.dividends DROP CONSTRAINT %I', nome);
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = 'public' AND t.relname = 'dividends'
           AND c.contype = 'c'
           AND pg_get_constraintdef(c.oid) LIKE '%lending_reimbursement%'
    ) THEN
        ALTER TABLE public.dividends
            ADD CONSTRAINT dividends_type_check CHECK (type IN (
                'dividend', 'jcp', 'reit_income', 'amortization', 'other',
                'lending_reimbursement'
            ));
    END IF;
END $$;

UPDATE public.dividends d
   SET type = 'lending_reimbursement'
  FROM public.assets a, public.investment_movement_events e
 WHERE a.id = d.asset_id
   AND d.type = 'reit_income'
   AND left(d.external_id, 6) = 'b3mov-'
   AND e.user_id = d.user_id
   AND e.ticker = a.ticker
   AND e.event_date = d.payment_date
   AND round(e.total_value::numeric, 2) = round(d.total_amount::numeric, 2)
   AND lower(e.movement) = 'reembolso';

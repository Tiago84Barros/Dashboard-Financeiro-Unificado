-- 051: A-147 -- marcar BDC e fundo fechado, que a SEC nao classifica por SIC.
--
-- O submissions da SEC devolve `sic` e `sicDescription` VAZIOS para toda
-- companhia de investimento registrada. A regra de universo em
-- core/us_instrumento.py reconhece a descricao SIC "closed-end management
-- investment offices", que nunca chegava -- e por isso 40 fundos de credito
-- (FS KKR, Hercules, Goldman Sachs BDC, Oaktree, Sixth Street...) disputavam
-- ranking com companhia operacional, sem receita e com lucro distribuido por
-- obrigacao legal de RIC.
--
-- O sinal que a SEC de fato fornece e o FORMULARIO (N-54A, N-2, N-CSR, NPORT).
-- Esta coluna guarda esse veredito para que a leitura da vitrine possa aplicar
-- o filtro sem refazer a consulta a SEC.
-- O destino pode ter so a vitrine (o Supabase publicado nao carrega `companies`),
-- entao cada ALTER so roda se a tabela existir ali.
DO $$
BEGIN
  IF to_regclass('market_us.companies') IS NOT NULL THEN
    ALTER TABLE market_us.companies
      ADD COLUMN IF NOT EXISTS is_investment_company boolean NOT NULL DEFAULT false;
  END IF;
  IF to_regclass('market_us.company_snapshots') IS NOT NULL THEN
    ALTER TABLE market_us.company_snapshots
      ADD COLUMN IF NOT EXISTS is_investment_company boolean NOT NULL DEFAULT false;
  END IF;
END $$;

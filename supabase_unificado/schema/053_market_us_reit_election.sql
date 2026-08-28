-- 053: A-156 -- separar REIT de operadora imobiliaria pela eleicao fiscal.
--
-- O rotulo generico "Real Estate" do cadastro excluia 22 ativos como "tipo nao
-- confirmado". A hipotese era que gravar o SIC da SEC resolveria. Medido em
-- 27/08/2026 contra o submissions: **os 22 tem SIC 6500**, REIT e operadora
-- igualmente -- a Innovative Industrial Properties (REIT) e a Forestar Group
-- (incorporadora) chegam com o mesmo codigo. O SIC nao separa. Gravar o SIC
-- teria custado uma coluna e uma varredura para nao mudar nenhuma decisao.
--
-- A evidencia que separa e a eleicao fiscal, declarada pela propria companhia
-- no 10-K: quem e REIT escreve que elegeu ser tributada como tal. Medido nos
-- 22: 10 declaram (IIPR, GTY, TRNO, UE, HPP, OPI, SRG, CURB, MRP, EFC) e 9 nao
-- (FOR, MLP, FPH, CHCI, SKYH, BEEP, AEI, AIRE, OZ) -- separacao limpa.
--
-- Tres estados, e a diferenca entre dois deles e o ponto: 'declarada' e REIT,
-- 'ausente' e operadora apurada, e NULL e nao apurado -- que continua excluido.
-- Sem o terceiro estado, "false" significaria ao mesmo tempo "verifiquei e nao
-- e" e "nunca verifiquei", e a duvida passaria a liberar.
--
-- O destino pode ter so a vitrine (o Supabase publicado nao carrega `companies`),
-- entao cada ALTER so roda se a tabela existir ali.
DO $$
BEGIN
  IF to_regclass('market_us.companies') IS NOT NULL THEN
    ALTER TABLE market_us.companies
      ADD COLUMN IF NOT EXISTS reit_election text;
  END IF;
  IF to_regclass('market_us.company_snapshots') IS NOT NULL THEN
    ALTER TABLE market_us.company_snapshots
      ADD COLUMN IF NOT EXISTS reit_election text;
  END IF;
END $$;

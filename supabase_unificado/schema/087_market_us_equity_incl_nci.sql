-- 087_market_us_equity_incl_nci.sql
-- PL consolidado (com participacao de minoritarios) nos balancos EUA.
--
-- `total_equity` passou a ser o PL do CONTROLADOR (mesmo perimetro do lucro:
-- ROE e P/VPA). A identidade ativo = passivo + PL e consolidada, entao a checagem
-- de qualidade e o capital investido do ROIC leem esta coluna.
--
-- ADITIVO e idempotente. Linhas antigas ficam NULL (ausente != zero) e a checagem
-- cai em COALESCE(total_equity_incl_nci, total_equity).
-- Backup: nada a fazer (so ADD COLUMN). Rollback:
--   ALTER TABLE market_us.balance_sheets DROP COLUMN IF EXISTS total_equity_incl_nci;
-- NAO aplicar sem autorizacao humana; aplicar ANTES da reextracao v8.

ALTER TABLE market_us.balance_sheets
    ADD COLUMN IF NOT EXISTS total_equity_incl_nci NUMERIC(24,2);

COMMENT ON COLUMN market_us.balance_sheets.total_equity_incl_nci IS
    'PL consolidado (StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest). total_equity = controlador.';

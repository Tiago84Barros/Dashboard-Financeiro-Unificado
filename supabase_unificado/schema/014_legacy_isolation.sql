-- ============================================================
-- 014_legacy_isolation.sql
-- Isolamento (marcação) do legado de dados de mercado em public
-- Criado: 2026-06-22
--
-- OBJETIVO:
--   Marcar como LEGADO as tabelas de dados de mercado em `public` que serão
--   substituídas pelo schema `market` (013). NÃO move, NÃO renomeia, NÃO
--   remove — apenas adiciona COMMENT. O app continua usando `public` até a
--   migração de leitura ser concluída (fases seguintes).
--
-- SEGURANÇA:
--   Apenas COMMENT ON. Zero DROP/ALTER de dados/estrutura. Idempotente.
--
-- POR QUE NÃO MOVER AGORA:
--   O código atual lê public.multiplos, public."Demonstracoes_Financeiras", etc.
--   Mover/renomear quebraria o app em produção. A remoção/movimentação ocorrerá
--   só após o app passar a ler de `market.*` e a paridade ser validada.
-- ============================================================

-- Dados de mercado que o schema market.* substitui:
COMMENT ON TABLE public.multiplos                       IS '[LEGADO] Substituído por market.calculated_metrics + market.income/balance. Origem yfinance. Não remover até migração de leitura.';
COMMENT ON TABLE public."multiplos_TRI"                 IS '[LEGADO] Múltiplos trimestrais — ver market.calculated_metrics (period=quarterly).';
COMMENT ON TABLE public."Demonstracoes_Financeiras"     IS '[LEGADO] Substituído por market.income_statements/balance_sheets/cash_flow_statements (period=annual).';
COMMENT ON TABLE public."Demonstracoes_Financeiras_TRI" IS '[LEGADO] Idem, period=quarterly em market.*.';
COMMENT ON TABLE public.macro                           IS '[LEGADO] Substituído por market.macro_indicators (série por indicador/data).';
COMMENT ON TABLE public.asset_quotes                    IS '[LEGADO] Substituído por market.historical_prices.';
COMMENT ON TABLE public.dividends                       IS '[LEGADO p/ mercado] Proventos passam a viver em market.dividends. (Esta tabela também atende imports pessoais — avaliar antes de aposentar.)';
COMMENT ON TABLE public.setores                         IS '[PONTE] Universo ticker→setor; será espelhado em market.companies/assets. Manter até migração.';

-- ============================================================
-- FIM 014. Tabelas de finanças pessoais (accounts, transactions, categories,
-- dividends de imports, etc.) NÃO são legado de mercado e não são marcadas aqui.
-- ============================================================

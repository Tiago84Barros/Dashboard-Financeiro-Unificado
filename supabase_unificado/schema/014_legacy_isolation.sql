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

-- Alguns objetos eram exclusivos de instalações legadas. O bootstrap de um
-- banco novo não os cria, portanto a marcação deve ser uma operação nula nesses
-- casos em vez de interromper as migrations posteriores.
DO $legacy_comments$
DECLARE
    legacy record;
    relation regclass;
BEGIN
    FOR legacy IN
        SELECT *
        FROM (VALUES
            ('public.multiplos', '[LEGADO] Substituído por market.calculated_metrics + market.income/balance. Origem yfinance. Não remover até migração de leitura.'),
            ('public."multiplos_TRI"', '[LEGADO] Múltiplos trimestrais — ver market.calculated_metrics (period=quarterly).'),
            ('public."Demonstracoes_Financeiras"', '[LEGADO] Substituído por market.income_statements/balance_sheets/cash_flow_statements (period=annual).'),
            ('public."Demonstracoes_Financeiras_TRI"', '[LEGADO] Idem, period=quarterly em market.*.'),
            ('public.macro', '[LEGADO] Substituído por market.macro_indicators (série por indicador/data).'),
            ('public.asset_quotes', '[LEGADO] Substituído por market.historical_prices.'),
            ('public.dividends', '[LEGADO p/ mercado] Proventos passam a viver em market.dividends. (Esta tabela também atende imports pessoais — avaliar antes de aposentar.)'),
            ('public.setores', '[PONTE] Universo ticker→setor; será espelhado em market.companies/assets. Manter até migração.')
        ) AS legacy(table_name, comment_text)
    LOOP
        relation := to_regclass(legacy.table_name);
        IF relation IS NOT NULL THEN
            EXECUTE format('COMMENT ON TABLE %s IS %L', relation, legacy.comment_text);
        END IF;
    END LOOP;
END
$legacy_comments$;

-- ============================================================
-- FIM 014. Tabelas de finanças pessoais (accounts, transactions, categories,
-- dividends de imports, etc.) NÃO são legado de mercado e não são marcadas aqui.
-- ============================================================

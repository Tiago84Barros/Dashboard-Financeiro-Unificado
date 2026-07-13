-- ============================================================
-- 027_data_api_rls_hardening.sql
-- Defesa em profundidade para objetos privados/backend-only.
--
-- Contexto validado em 2026-07-13:
--   * o Streamlit e o ETL usam conexão PostgreSQL direta (role postgres);
--   * o schema market não é uma API pública;
--   * tabelas public legadas sem RLS tinham grants amplos para anon/authenticated.
--
-- Estratégia:
--   1. habilitar RLS em toda tabela ainda desprotegida de public/market;
--   2. revogar grants da Data API somente nessas tabelas backend-only;
--   3. criar uma policy-deny explícita, documentando a intenção;
--   4. tornar views public security_invoker e não acessíveis anonimamente;
--   5. impedir grants automáticos inseguros em objetos futuros.
--
-- O owner postgres e service roles com BYPASSRLS continuam operacionais.
-- Não há DROP, DELETE, TRUNCATE nem alteração de dados.
-- ============================================================

DO $hardening$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT n.nspname AS schema_name, c.relname AS table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
          AND n.nspname IN ('public', 'market')
          AND NOT c.relrowsecurity
    LOOP
        EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                       obj.schema_name, obj.table_name);
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM PUBLIC, anon, authenticated',
                       obj.schema_name, obj.table_name);
        EXECUTE format(
            'CREATE POLICY data_api_private_deny ON %I.%I AS RESTRICTIVE '
            'FOR ALL TO anon, authenticated USING (false) WITH CHECK (false)',
            obj.schema_name, obj.table_name
        );
        EXECUTE format(
            'COMMENT ON POLICY data_api_private_deny ON %I.%I IS '
            '%L', obj.schema_name, obj.table_name,
            'Backend-only: acesso pela Data API anon/authenticated explicitamente bloqueado.'
        );
    END LOOP;
END
$hardening$;

-- market permanece integralmente privado mesmo se alguma tabela já tiver RLS.
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA market FROM PUBLIC, anon, authenticated;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA market FROM PUBLIC, anon, authenticated;
REVOKE ALL PRIVILEGES ON SCHEMA market FROM PUBLIC, anon, authenticated;

-- Views analíticas não podem contornar a identidade/RLS das tabelas-base.
DO $views$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT n.nspname AS schema_name, c.relname AS view_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'v' AND n.nspname = 'public'
    LOOP
        EXECUTE format('ALTER VIEW %I.%I SET (security_invoker = true)',
                       obj.schema_name, obj.view_name);
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM PUBLIC, anon, authenticated',
                       obj.schema_name, obj.view_name);
    END LOOP;
END
$views$;

-- Novos objetos criados por postgres começam fechados; acesso futuro deve ser explícito.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA market
    REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA market
    REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated;

COMMENT ON SCHEMA market IS
    'Dados de mercado e ETL backend-only; sem exposição anon/authenticated pela Data API.';

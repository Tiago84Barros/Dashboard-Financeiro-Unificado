-- ============================================================
-- 028_private_policy_and_function_hardening.sql
-- Completa o hardening de objetos que já possuíam RLS sem policy e fixa
-- search_path de funções apontadas pelo Security Advisor.
-- ============================================================

DO $private_tables$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT n.nspname AS schema_name, c.relname AS table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind IN ('r', 'p')
          AND n.nspname IN ('public', 'market')
          AND c.relrowsecurity
          AND NOT EXISTS (
              SELECT 1 FROM pg_policies p
              WHERE p.schemaname = n.nspname AND p.tablename = c.relname
          )
    LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM PUBLIC, anon, authenticated',
                       obj.schema_name, obj.table_name);
        EXECUTE format(
            'CREATE POLICY data_api_private_deny ON %I.%I AS RESTRICTIVE '
            'FOR ALL TO anon, authenticated USING (false) WITH CHECK (false)',
            obj.schema_name, obj.table_name
        );
        EXECUTE format(
            'COMMENT ON POLICY data_api_private_deny ON %I.%I IS %L',
            obj.schema_name, obj.table_name,
            'Backend-only: acesso pela Data API anon/authenticated explicitamente bloqueado.'
        );
    END LOOP;
END
$private_tables$;

ALTER FUNCTION public.match_corporate_chunks(vector, integer, text)
    SET search_path = pg_catalog, public;
ALTER FUNCTION market.set_updated_at()
    SET search_path = pg_catalog, market;

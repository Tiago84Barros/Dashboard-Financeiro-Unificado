-- 085_pj_categorias.sql — categorias criadas pelo usuário no Controle CNPJ.
--
-- Idempotente. O app roda estas instruções sozinho na primeira leitura do
-- Controle CNPJ (`core.pj.repositorio.garantir_tabelas`); rodar à mão no SQL
-- Editor do Supabase também funciona.
--
-- O plano de contas fixo mora em `core/pj/plano_contas.py`. Esta tabela guarda
-- só as categorias que o dono acrescenta, cada uma presa a um grupo da DRE: é
-- o grupo que decide onde ela soma (Receita bruta entra no DAS; Fora do
-- resultado não entra na DRE). `codigo` começa com `u_` e não muda ao renomear,
-- então `pj_lancamentos.categoria` e `pj_regras.categoria` seguem válidos.

CREATE TABLE IF NOT EXISTS pj_categorias (
    user_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    codigo     TEXT NOT NULL,
    nome       TEXT NOT NULL,
    grupo      TEXT NOT NULL CHECK (grupo IN ('receita_bruta', 'deducoes', 'custos',
                   'despesas_operacionais', 'pessoal', 'financeiro', 'fora_do_resultado')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, codigo)
);
DO $$ BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class
            WHERE relname = 'pj_categorias' AND relnamespace = 'public'::regnamespace) THEN
        ALTER TABLE pj_categorias ENABLE ROW LEVEL SECURITY;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'pj_categorias'
                     AND policyname = 'pj_categorias_owner_all') THEN
        CREATE POLICY pj_categorias_owner_all ON pj_categorias
            USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

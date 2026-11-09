-- 084_controle_cnpj.sql — financeiro da empresa (Controle CNPJ).
--
-- Idempotente. O app roda estas instruções sozinho na primeira leitura do
-- Controle CNPJ (`core.pj.repositorio.garantir_tabelas`); rodar à mão no SQL
-- Editor do Supabase também funciona.
--
-- Por que tabelas próprias e não `transactions`:
--
-- O financeiro pessoal tem dezenas de consultas sobre `transactions` (mês,
-- orçamento, saldo, IR, chat). Uma coluna "é da empresa" obrigaria cada uma a
-- lembrar de filtrar, e a que esquecesse somaria o faturamento da empresa à
-- renda da pessoa. Tabela separada não depende de ninguém lembrar.
--
-- pj_empresas       uma linha por dono: CNPJ, anexo do Simples, saldo de abertura
-- pj_lancamentos    pago ou previsto, do extrato ou manual; `chave` = FITID do OFX
-- pj_saldos_extrato saldo informado pelo banco em cada extrato (conferência)
-- pj_regras         contraparte -> categoria aprendida na importação

CREATE TABLE IF NOT EXISTS pj_empresas (
    user_id            UUID PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    cnpj               TEXT,
    razao_social       TEXT,
    anexo_simples      TEXT NOT NULL DEFAULT 'III',
    saldo_inicial      NUMERIC(15,2),
    data_saldo_inicial DATE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pj_lancamentos (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    data           DATE NOT NULL,
    descricao      TEXT NOT NULL,
    valor          NUMERIC(15,2) NOT NULL,
    categoria      TEXT,
    status         TEXT NOT NULL DEFAULT 'pago' CHECK (status IN ('pago', 'previsto')),
    origem         TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'extrato')),
    contraparte    TEXT,
    chave          TEXT,
    grupo_serie    UUID,
    parcela        INT NOT NULL DEFAULT 1,
    total_parcelas INT NOT NULL DEFAULT 1,
    arquivo        TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, chave)
);
CREATE INDEX IF NOT EXISTS idx_pj_lancamentos_user_data
ON pj_lancamentos (user_id, data);
CREATE TABLE IF NOT EXISTS pj_saldos_extrato (
    user_id    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    data       DATE NOT NULL,
    saldo      NUMERIC(15,2) NOT NULL,
    arquivo    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, data)
);
CREATE TABLE IF NOT EXISTS pj_regras (
    user_id     UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    contraparte TEXT NOT NULL,
    categoria   TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, contraparte)
);
DO $$ BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class
            WHERE relname = 'pj_empresas' AND relnamespace = 'public'::regnamespace) THEN
        ALTER TABLE pj_empresas ENABLE ROW LEVEL SECURITY;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'pj_empresas'
                     AND policyname = 'pj_empresas_owner_all') THEN
        CREATE POLICY pj_empresas_owner_all ON pj_empresas
            USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;
DO $$ BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class
            WHERE relname = 'pj_lancamentos' AND relnamespace = 'public'::regnamespace) THEN
        ALTER TABLE pj_lancamentos ENABLE ROW LEVEL SECURITY;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'pj_lancamentos'
                     AND policyname = 'pj_lancamentos_owner_all') THEN
        CREATE POLICY pj_lancamentos_owner_all ON pj_lancamentos
            USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;
DO $$ BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class
            WHERE relname = 'pj_saldos_extrato' AND relnamespace = 'public'::regnamespace) THEN
        ALTER TABLE pj_saldos_extrato ENABLE ROW LEVEL SECURITY;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'pj_saldos_extrato'
                     AND policyname = 'pj_saldos_extrato_owner_all') THEN
        CREATE POLICY pj_saldos_extrato_owner_all ON pj_saldos_extrato
            USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;
DO $$ BEGIN
    IF NOT (SELECT relrowsecurity FROM pg_class
            WHERE relname = 'pj_regras' AND relnamespace = 'public'::regnamespace) THEN
        ALTER TABLE pj_regras ENABLE ROW LEVEL SECURITY;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'public' AND tablename = 'pj_regras'
                     AND policyname = 'pj_regras_owner_all') THEN
        CREATE POLICY pj_regras_owner_all ON pj_regras
            USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

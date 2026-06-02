-- ============================================================
-- 012_bank_statement_imports.sql
-- Upload/revisao de extratos bancarios em PDF
-- Banco: Dashboard Financeiro Unificado (Supabase - schema public)
--
-- Seguro para executar multiplas vezes.
-- Nao altera o schema principal de transactions/categories/accounts.
-- ============================================================

CREATE TABLE IF NOT EXISTS bank_statement_movements (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    account_id                  UUID REFERENCES accounts(id) ON DELETE SET NULL,
    transaction_id              UUID REFERENCES transactions(id) ON DELETE SET NULL,
    banco                       TEXT NOT NULL,
    conta                       TEXT,
    data_movimento              DATE NOT NULL,
    data_lancamento             DATE,
    tipo_original_banco         TEXT,
    descricao_original          TEXT NOT NULL,
    descricao_normalizada       TEXT NOT NULL,
    valor                       NUMERIC(15,2) NOT NULL,
    direcao                     TEXT NOT NULL,
    categoria_id                UUID REFERENCES categories(id) ON DELETE SET NULL,
    subcategoria_id             UUID,
    categoria_sugerida_texto    TEXT,
    subcategoria_sugerida_texto TEXT,
    categoria_confirmada_id     UUID REFERENCES categories(id) ON DELETE SET NULL,
    subcategoria_confirmada_id  UUID,
    confianca_classificacao     NUMERIC(5,2),
    status_classificacao        TEXT NOT NULL DEFAULT 'pendente',
    origem_arquivo              TEXT,
    hash_lancamento             TEXT NOT NULL,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, hash_lancamento)
);

CREATE TABLE IF NOT EXISTS bank_statement_classification_rules (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    banco                 TEXT,
    tipo_original_banco   TEXT,
    palavra_chave         TEXT NOT NULL,
    descricao_normalizada TEXT,
    category_id           UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    subcategoria_id       UUID,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bank_statement_movements_user_date
ON bank_statement_movements (user_id, data_movimento DESC);

CREATE INDEX IF NOT EXISTS idx_bank_statement_movements_user_status
ON bank_statement_movements (user_id, status_classificacao);

CREATE INDEX IF NOT EXISTS idx_bank_statement_rules_user_bank
ON bank_statement_classification_rules (user_id, banco);

ALTER TABLE bank_statement_movements ENABLE ROW LEVEL SECURITY;
ALTER TABLE bank_statement_classification_rules ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='bank_statement_movements'
          AND policyname='bank_statement_movements_owner_all'
    ) THEN
        CREATE POLICY bank_statement_movements_owner_all ON bank_statement_movements
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname='public'
          AND tablename='bank_statement_classification_rules'
          AND policyname='bank_statement_rules_owner_all'
    ) THEN
        CREATE POLICY bank_statement_rules_owner_all ON bank_statement_classification_rules
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END; $$;

-- 076_investment_policies.sql — a política de investimentos do usuário.
--
-- Rodar À MÃO no SQL Editor do Supabase. Idempotente: pode rodar de novo.
--
-- O que é:
--
-- A resposta de "o que este usuário pretende construir com o patrimônio":
-- objetivo, horizonte, risco, liquidez, estratégia predominante, alocação
-- por classe e restrições. É a premissa que a LLM usa depois para analisar
-- a carteira e cada ativo (Configurações → Geral → Estratégia de
-- Investimentos). Montada por uma entrevista guiada, revisável à mão.
--
-- Por que uma linha por VERSÃO, e não uma linha por usuário:
--
-- A análise futura precisa saber qual política estava em vigor quando uma
-- conclusão foi tirada. Editar uma política concluída abre uma versão nova
-- (version + 1) como rascunho; a concluída continua sendo a vigente até o
-- rascunho ser concluído. Não há histórico elaborado agora, mas nada aqui
-- impede de tê-lo: as versões antigas ficam como ARCHIVED.
--
-- Por que o conteúdo é JSONB e não uma coluna por campo:
--
-- Cada campo carrega procedência (`{"value", "source", "evidence", "at"}`):
-- um valor dito na entrevista e um valor digitado no formulário não são o
-- mesmo tipo de fato, e um número que ninguém disse não pode aparecer como
-- escolha do usuário. O conjunto de campos também vai crescer com o módulo
-- de análise; `schema_version` diz com qual conjunto a linha foi validada.
--
-- Por que `status` e `completion_pct` são gravados E recalculados:
--
-- A coluna guarda o que valia na gravação (para consulta e auditoria). A
-- tela recalcula na leitura: uma política concluída que deixou de passar na
-- validação (schema novo, prazo de revisão vencido) aparece como
-- NEEDS_REVIEW mesmo que a linha diga COMPLETED.

CREATE TABLE IF NOT EXISTS investment_policies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID         NOT NULL,
    version         INTEGER      NOT NULL,
    schema_version  TEXT         NOT NULL,
    status          TEXT         NOT NULL,
    policy_json     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    interview_json  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    completion_pct  NUMERIC(5,1) NOT NULL DEFAULT 0,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT investment_policies_status_ck
        CHECK (status IN ('IN_PROGRESS', 'COMPLETED', 'NEEDS_REVIEW', 'ARCHIVED')),
    CONSTRAINT investment_policies_pct_ck
        CHECK (completion_pct BETWEEN 0 AND 100),
    CONSTRAINT investment_policies_version_ck
        CHECK (version >= 1),
    CONSTRAINT investment_policies_uk
        UNIQUE (user_id, version)
);

CREATE INDEX IF NOT EXISTS idx_investment_policies_user_status
    ON investment_policies (user_id, status);

COMMENT ON TABLE investment_policies IS
    'Política de investimentos do usuário, uma linha por versão. '
    'A vigente é a última COMPLETED; o rascunho é a IN_PROGRESS.';
COMMENT ON COLUMN investment_policies.policy_json IS
    'Campos da política com procedência: {campo: {value, source, evidence, at}}.';
COMMENT ON COLUMN investment_policies.interview_json IS
    'Transcrição da entrevista guiada: [{role, content, at}], limitada.';
COMMENT ON COLUMN investment_policies.status IS
    'NOT_STARTED não é gravado: é a ausência de linha. ARCHIVED = versão substituída.';

-- RLS no mesmo padrão das demais tabelas por usuário. O app conecta como
-- `postgres`, que ignora RLS; o filtro real é o WHERE user_id do código.
ALTER TABLE investment_policies ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename  = 'investment_policies'
          AND policyname = 'investment_policies_owner_all'
    ) THEN
        CREATE POLICY investment_policies_owner_all ON investment_policies
            FOR ALL
            USING (user_id = auth.uid())
            WITH CHECK (user_id = auth.uid());
    END IF;
END $$;

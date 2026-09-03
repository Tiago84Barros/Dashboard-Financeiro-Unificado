-- 067_recomendacao_auditoria.sql
--
-- A trilha que responde "por que o APP4 recomendou essa mudanca naquele
-- momento?".
--
-- O projeto ja tem quatro tabelas de auditoria -- market.fii_audit_events,
-- market_us.data_quality_audit, market.b3_validation_runs e
-- market.b3_data_readiness_snapshots -- e nenhuma delas responde a essa
-- pergunta: todas auditam QUALIDADE DE DADO, nao RECOMENDACAO. Auditar a
-- entrada e nao a saida deixa sem registro exatamente o elo que o usuario
-- questiona.
--
-- Fica no schema public porque o dado e do app, nao de um provedor de mercado,
-- e porque a Streamlit Cloud so alcanca o Supabase -- a gravacao acontece na
-- sessao do usuario, e o armazem local nao esta la para receber. O volume e
-- pequeno por construcao: uma linha por recomendacao, texto curto, e expurgo em
-- 365 dias por core.auditoria.trilha.expurgar. Sem o expurgo, uma tabela que so
-- cresce e divida com data marcada num banco que ja opera em 425 MB de 500 MB.

CREATE TABLE IF NOT EXISTS public.recomendacao_auditoria (
    id                     TEXT PRIMARY KEY,
    momento                TIMESTAMPTZ NOT NULL,

    -- "essa mudanca"
    acao                   TEXT NOT NULL,
    ativo                  TEXT NOT NULL DEFAULT '',
    percentual             DOUBLE PRECISION,
    valor                  DOUBLE PRECISION,

    -- "por que"
    motivo                 TEXT NOT NULL DEFAULT '',
    evidencias             JSONB NOT NULL DEFAULT '[]'::JSONB,
    motor                  TEXT NOT NULL DEFAULT '',
    nivel_crise            SMALLINT,

    -- "naquele momento": sem as versoes, a linha diz o que foi recomendado e
    -- nao permite reconstruir com que dado e com que modelo.
    versao_modelo          TEXT NOT NULL DEFAULT '',
    versao_dados           TEXT NOT NULL DEFAULT '',
    frescor_horas          DOUBLE PRECISION,

    -- o desfecho
    decisao                TEXT NOT NULL DEFAULT 'proposta',
    bloqueios              JSONB NOT NULL DEFAULT '[]'::JSONB,
    travas_nao_verificadas JSONB NOT NULL DEFAULT '[]'::JSONB,

    -- o que foi MOSTRADO. Guardado ao lado das evidencias e nunca no lugar
    -- delas: a explicacao do modelo e apresentacao, o motivo e o que o backend
    -- calculou.
    explicacao_llm         TEXT NOT NULL DEFAULT '',
    llm_aprovada           BOOLEAN,
    llm_motivo             TEXT NOT NULL DEFAULT '',

    registrado_em          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'recomendacao_auditoria_decisao_ck') THEN
        ALTER TABLE public.recomendacao_auditoria
            ADD CONSTRAINT recomendacao_auditoria_decisao_ck
            CHECK (decisao IN ('proposta', 'confirmada', 'recusada', 'bloqueada'));
    END IF;

    -- nivel_crise segue os cinco niveis do motor de eventos extraordinarios
    -- (0 Normal a 4 Sistemico). NULL e "nao avaliado", que e diferente de 0.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'recomendacao_auditoria_nivel_ck') THEN
        ALTER TABLE public.recomendacao_auditoria
            ADD CONSTRAINT recomendacao_auditoria_nivel_ck
            CHECK (nivel_crise IS NULL OR nivel_crise BETWEEN 0 AND 4);
    END IF;
END $$;

-- A leitura real e sempre "as ultimas N", com ou sem filtro de ativo.
CREATE INDEX IF NOT EXISTS recomendacao_auditoria_momento_idx
    ON public.recomendacao_auditoria (momento DESC);
CREATE INDEX IF NOT EXISTS recomendacao_auditoria_ativo_idx
    ON public.recomendacao_auditoria (ativo, momento DESC)
    WHERE ativo <> '';

COMMENT ON TABLE public.recomendacao_auditoria IS
    'Trilha das recomendacoes do APP4. Responde por que, o que e quando. '
    'Retencao de 365 dias via core.auditoria.trilha.expurgar.';

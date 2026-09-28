-- 077_app_lacunas.sql — lacunas que o app declara na Streamlit Cloud.
--
-- Rodar À MÃO no SQL Editor do Supabase. Idempotente: pode rodar de novo.
--
-- O que é:
--
-- Tudo o que o app admite não saber -- limitação de motor, aviso de tela que
-- diz que falta dado, bloco <lacunas> do LLM, exceção na fronteira de uma rota
-- -- gravado por core/lacunas/destino.py quando o app roda na Cloud. Na máquina
-- local o mesmo evento vai para local_staging/lacunas/eventos.jsonl, e
-- scripts/lacunas_sincronizar.py funde os dois para o agente de correção.
--
-- Por que uma linha por impressão digital, e não uma por evento:
--
-- O Supabase free está perto dos 500 MB. O que a triagem precisa é de
-- frequência e recência, e isso cabe num contador. Estimativa: centenas de
-- linhas, dezenas de KB.
--
-- Por que a mensagem não é a chave:
--
-- A impressão digital é sha1(fonte|modulo|codigo|entidade). O texto traz
-- números que mudam a cada execução; se fosse chave, a mesma lacuna viraria
-- uma linha por dia e a frequência nunca passaria de 1.

CREATE TABLE IF NOT EXISTS app_lacunas (
    impressao        TEXT         PRIMARY KEY,
    fonte            TEXT         NOT NULL,
    modulo           TEXT         NOT NULL DEFAULT '',
    codigo           TEXT         NOT NULL DEFAULT '',
    entidade         TEXT         NOT NULL DEFAULT '',
    ultima_mensagem  TEXT         NOT NULL DEFAULT '',
    contexto         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    primeira_vez     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ultima_vez       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ocorrencias      INTEGER      NOT NULL DEFAULT 1,
    status           TEXT         NOT NULL DEFAULT 'aberta',
    reincidente      BOOLEAN      NOT NULL DEFAULT FALSE,
    pr_url           TEXT,
    nota_triagem     TEXT,
    CONSTRAINT app_lacunas_fonte_ck
        CHECK (fonte IN ('motor', 'llm', 'tela', 'excecao')),
    CONSTRAINT app_lacunas_status_ck
        CHECK (status IN ('aberta', 'legitima', 'em_pr', 'resolvida', 'incerta')),
    CONSTRAINT app_lacunas_ocorrencias_ck
        CHECK (ocorrencias >= 1),
    CONSTRAINT app_lacunas_mensagem_ck
        CHECK (char_length(ultima_mensagem) <= 500)
);

CREATE INDEX IF NOT EXISTS idx_app_lacunas_status_ultima
    ON app_lacunas (status, ultima_vez DESC);

COMMENT ON TABLE app_lacunas IS
    'Lacunas declaradas pelo app na Cloud, uma linha por impressão digital. '
    'Lida pelo scripts/lacunas_sincronizar.py; triada pelo agente corrigir-lacuna.';
COMMENT ON COLUMN app_lacunas.status IS
    'aberta = na fila; legitima = aviso correto, nada a corrigir; em_pr = PR aberto; '
    'resolvida = PR mergeado; incerta = triagem inconclusiva.';
COMMENT ON COLUMN app_lacunas.reincidente IS
    'Voltou a aparecer depois de resolvida: a correção não pegou.';

-- RLS ligada sem política: o app conecta como `postgres`, que ignora RLS, e
-- nenhum papel anônimo precisa ler lacunas.
ALTER TABLE app_lacunas ENABLE ROW LEVEL SECURITY;

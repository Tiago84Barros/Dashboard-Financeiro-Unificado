-- 071_confianca_snapshots.sql
-- Ultima medicao do Grau de Confianca, para que a tela deixe de remedir tudo
-- a cada abertura de Configuracoes.
--
-- Sem user_id de proposito: a medicao e do APLICATIVO, nao da pessoa. Ela le a
-- qualidade de dado compartilhado (vitrines, safras, universos), e so o admin
-- alcanca a aba. Uma copia por usuario multiplicaria o mesmo numero.

CREATE TABLE IF NOT EXISTS confianca_snapshots (
    id         BIGSERIAL    PRIMARY KEY,
    medido_em  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    payload    JSONB        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_confianca_snapshots_medido_em
    ON confianca_snapshots (medido_em DESC);

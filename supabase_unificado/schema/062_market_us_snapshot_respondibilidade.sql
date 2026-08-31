-- 062_market_us_snapshot_respondibilidade.sql
--
-- Publicar a trilha MUDA -- a que a metodologia mal consegue perguntar.
--
-- `coverage` responde "das perguntas respondiveis, quantas foram respondidas",
-- e desde 0.7.1 a razao indefinida sai do numerador E do denominador. Isso esta
-- certo, e abre um buraco: a trilha em que sobrou UMA pergunta respondivel, e
-- ela foi respondida, marca cobertura de 100%. E o mesmo defeito de "quem
-- pergunta menos tira nota maior", uma camada abaixo -- no nivel da metrica em
-- vez do motor.
--
-- `unanswerable_tracks` nomeia as trilhas criticas em que a empresa consegue
-- ter, no maximo, metade das metricas que a METODOLOGIA define. Desde a versao
-- 0.8.0 e este piso, e nao mais a marca de balanco quebrado, que trava o selo
-- de decisao. Sem a coluna na vitrine a tela nao consegue dizer POR QUE o selo
-- faltou, e cairia de volta na frase generica de cobertura.

ALTER TABLE market_us.company_snapshots
    ADD COLUMN IF NOT EXISTS unanswerable_tracks JSONB;

COMMENT ON COLUMN market_us.company_snapshots.unanswerable_tracks IS
    'Trilhas criticas mudas (A-160): a empresa consegue definir <= 50% das '
    'metricas que a metodologia pede para a trilha. Nao vazia = o selo de '
    'decisao caiu porque a trilha nao pode ser PERGUNTADA, e nao porque a '
    'resposta se perdeu. Distinta de critical_missing, que e resposta faltando.';

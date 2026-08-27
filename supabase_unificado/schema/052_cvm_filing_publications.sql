-- 052: A-155 -- quando o mercado soube, e nao quando o ETL rodou.
--
-- `market.calculated_metric_vintages.availability_quality` so sabia produzir
-- dois valores, e nenhum sustenta backtest:
--
--   migration_baseline (97.236 linhas)  "nao sei quando ficou disponivel"
--   first_seen_proxy   ( 2.918 linhas)  "foi a primeira vez que EU vi"
--
-- O proxy mede o dia em que o ETL rodou. Se ele rodou hoje, o proxy afirma que
-- o balanco de 2019 ficou disponivel hoje -- e o backtest compra o exercicio
-- inteiro no dia 1o de janeiro dele. Por isso `validation_readiness` reprovava
-- a B3 com "PIT estrito sem published_at/revisoes CVM": o bloqueador nao tinha
-- como sair, porque a terceira qualidade nao existia.
--
-- Esta tabela guarda o `DT_RECEB` do arquivo-cabecalho anual da CVM: a data em
-- que a companhia protocolou a DFP/ITR. `disponivel_em` e o MAIOR DT_RECEB do
-- exercicio (reapresentacao manda: o numero guardado hoje e o da ultima
-- versao, logo foi nela que ficou conhecivel) e `primeira_entrega_em` guarda o
-- menor, para que a escolha conservadora seja auditavel e reversivel.
CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.cvm_filing_publications (
    codigo_cvm          integer NOT NULL,
    exercicio           integer NOT NULL,
    categoria           text    NOT NULL,
    disponivel_em       date    NOT NULL,
    primeira_entrega_em date    NOT NULL,
    versoes             integer NOT NULL DEFAULT 1,
    atualizado_em       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (codigo_cvm, exercicio, categoria),
    CONSTRAINT cvm_filing_publications_ordem
        CHECK (primeira_entrega_em <= disponivel_em)
);

CREATE INDEX IF NOT EXISTS cvm_filing_publications_exercicio_idx
    ON market.cvm_filing_publications (exercicio, categoria);

-- 056_market_us_sec_entidade.sql
--
-- Identidade da entidade que arquiva na SEC, por CIK -- inclusive a que morreu.
--
-- Por que uma tabela nova em vez de uma coluna em `companies`: `companies` foi
-- montada a partir de quem esta listado hoje (3.153 linhas) e por construcao
-- nunca tera as 12.107 saidas de `delistings`. Medido em 28/08/2026, o
-- cruzamento das duas devolve DUAS linhas. Uma coluna em `companies` so
-- descreveria os sobreviventes, que e exatamente a populacao sobre a qual nao
-- ha duvida.
--
-- O que esta tabela resolve: hoje a tela afirma ao usuario que "70% das
-- empresas desapareceram". Esse numero foi medido sobre 9.686 CIKs que
-- arquivaram QUALQUER relatorio anual em 2010 -- trust de leasing, emissor de
-- ABS, subsidiaria de seguradora, fundo fechado e emissor estrangeiro de 20-F
-- entram todos na conta. O painel analisa ACAO OPERACIONAL americana. Medir a
-- mortalidade numa populacao e exibi-la como se fosse de outra e o mesmo
-- defeito de [[medir-a-fonte-que-a-decisao-le]]: o numero nao esta errado, esta
-- respondendo outra pergunta. O SIC e o entityType desta tabela sao o que
-- permite refazer a conta na populacao certa.
--
-- Fonte: https://data.sec.gov/submissions/CIK##########.json, que responde 200
-- para CIK morto -- ao contrario de `company_tickers.json`, que so lista quem
-- esta vivo. Ver [[sec-company-tickers-incompleto]].
--
-- `tickers` vem quase sempre vazio para quem morreu: a SEC remove o simbolo
-- quando a empresa para de arquivar. Aferido em 28/08/2026 sobre 40 CIKs
-- sorteados de `delistings`: ZERO devolveram ticker. O identificador em que o
-- painel inteiro e chaveado e, ele proprio, enviesado por sobrevivencia. Por
-- isso a coluna existe e por isso nao se pode exigir que esteja preenchida.

CREATE TABLE IF NOT EXISTS market_us.sec_entidade (
    cik                 BIGINT      PRIMARY KEY,
    nome                TEXT        NULL,
    sic                 TEXT        NULL,
    sic_descricao       TEXT        NULL,
    entity_type         TEXT        NULL,
    exchanges           TEXT        NULL,
    tickers             TEXT        NULL,
    estado_incorporacao TEXT        NULL,
    http_status         INTEGER     NOT NULL,
    fonte               TEXT        NOT NULL DEFAULT 'sec_submissions',
    apurado_em          TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE market_us.sec_entidade IS
    'Identidade por CIK lida em data.sec.gov/submissions, viva ou morta. Existe '
    'para medir mortalidade na populacao que o painel de fato analisa, em vez '
    'de sobre todo arquivador de relatorio anual.';

COMMENT ON COLUMN market_us.sec_entidade.tickers IS
    'Simbolos que a SEC ainda associa ao CIK, separados por virgula. Vazio para '
    'quase toda empresa morta: a SEC remove o simbolo quando ela para de '
    'arquivar. Ausencia aqui nao e evidencia de que nunca houve ticker.';

COMMENT ON COLUMN market_us.sec_entidade.http_status IS
    'Status da consulta. Linha gravada mesmo em falha, para que a proxima '
    'execucao saiba distinguir "nao consultado" de "consultado e sem resposta".';

CREATE INDEX IF NOT EXISTS sec_entidade_sic_idx ON market_us.sec_entidade (sic);

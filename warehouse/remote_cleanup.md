# Limpeza segura do Supabase remoto

O warehouse local e o Supabase remoto são bancos diferentes. Migrar a ingestão
não libera espaço no Supabase por si só; é preciso remover as tabelas históricas
que não fazem parte da vitrine.

## Estado conhecido

Na última medição, o projeto remoto tinha aproximadamente 1,46 GB. As maiores
relações eram `market.fii_metric_observations`,
`market.fii_b3_security_history`, `market.fii_lineage_edges`,
`market.fii_exposures` e `market.cri_security_observations`.

## Pré-requisitos

1. Confirmar que o commit que remove as cargas pesadas do workflow está ativo.
2. Confirmar que o warehouse local está saudável e contém os dados históricos.
3. Publicar no Supabase somente as tabelas listadas em
   `warehouse/tables_vitrine.txt`.
4. Executar o diagnóstico abaixo no SQL Editor, sem alterações:

```sql
SELECT n.nspname AS schema_name, c.relname AS table_name,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'm')
  AND n.nspname IN ('market', 'public')
ORDER BY pg_total_relation_size(c.oid) DESC;
```

## Tabelas candidatas a remoção

Somente após a validação acima, as tabelas abaixo podem ser removidas do
Supabase, pois são armazém e não vitrine. As quatro tabelas auxiliares de
documentos também entram no descarte porque dependem de `fii_documents` ou
`fii_parser_versions` e possuem cópia no warehouse local:

```sql
DROP TABLE IF EXISTS market.fii_lineage_edges;
DROP TABLE IF EXISTS market.fii_metric_observations;
DROP TABLE IF EXISTS market.fii_b3_security_history;
DROP TABLE IF EXISTS market.cri_security_observations;
DROP TABLE IF EXISTS market.fii_exposures;
DROP TABLE IF EXISTS market.historical_price_observations;
DROP TABLE IF EXISTS market.calculated_metric_vintages;
DROP TABLE IF EXISTS market.fii_extraction_evidence;
DROP TABLE IF EXISTS market.fii_extraction_runs;
DROP TABLE IF EXISTS market.fii_parser_calibrations;
DROP TABLE IF EXISTS market.fii_document_versions;
DROP TABLE IF EXISTS market.fii_documents;
DROP TABLE IF EXISTS market.fii_parser_versions;
DROP TABLE IF EXISTS market.fii_registry_observations;
DROP TABLE IF EXISTS market.fii_cvm_archive_loads;
DROP TABLE IF EXISTS market.fii_b3_archive_loads;
DROP TABLE IF EXISTS market.fii_cri_archive_loads;
DROP TABLE IF EXISTS market.fii_source_releases;
DROP TABLE IF EXISTS public."multiplos_TRI";
```

`market.brapi_raw_payloads` permanece no Supabase nesta etapa. Ela é referenciada
por tabelas vitrine preservadas, como `historical_prices`, `income_statements`,
`balance_sheets`, `cash_flow_statements` e `fii_universe_history`; removê-la
exigiria apagar ou enfraquecer essas chaves estrangeiras. O custo restante é
aceitável e preserva a integridade do aplicativo.

> **Desatualizado desde 2026-08-16.** Aquele parágrafo descrevia o schema de
> julho. Hoje a única chave estrangeira que aponta para `brapi_raw_payloads` é a
> auto-referência `brapi_raw_payloads_supersedes_id_fkey`; as FKs vindas de
> `historical_prices`, `income_statements`, `balance_sheets`,
> `cash_flow_statements` e `fii_universe_history` **não existem mais**. Confirme
> antes de agir — a consulta abaixo é a fonte da verdade, não este texto:
>
> ```sql
> SELECT conrelid::regclass, conname FROM pg_constraint
> WHERE confrelid = 'market.brapi_raw_payloads'::regclass AND contype = 'f';
> ```
>
> Ou seja, o `DROP TABLE ... CASCADE` de `scripts/compact_remote_brapi_raw.py`
> hoje derruba apenas aquela auto-referência, e a compactação é bem menos
> arriscada do que o parágrafo acima sugere. O que continua valendo é o
> pré-requisito real: **arquivar antes de compactar**. O script se recusa a rodar
> se algum hash remoto faltar no warehouse local, e é assim que deve ser.

Não remover automaticamente `public.docs_corporativos` ou
`public.docs_corporativos_chunks`: elas não estão na lista armazém e podem
conter documentos usados pelo aplicativo. Também não remover nenhuma tabela de
finanças pessoais.

Depois do `DROP`, aguardar o Supabase atualizar o uso e executar novamente o
diagnóstico. A operação é destrutiva no banco remoto; este arquivo documenta o
procedimento executado após a validação do backup local.

## Índices mortos — 14,6 MB (levantado em 2026-08-16)

Seis índices com `idx_scan = 0`. As estatísticas são confiáveis: `stats_reset` é
NULL (nunca foram zeradas) e o índice mais usado do banco acumula 1,49 mi de
scans. Cada um foi verificado por raciocínio, não só pelo contador:

| índice | MB | por que não serve |
|---|---|---|
| `idx_metric_vintages_lookup` | 9,4 | duplicata **exata** de `uq_metric_vintage_artifact` (mesma lista de colunas), que tem 214.080 scans |
| `idx_calcmetric_conf` | 2,9 | `confidence_score` tem 2 valores distintos em 66.848 linhas; o planejador nunca escolhe |
| `idx_chunks_ticker_date` | 0,8 | o RAG filtra por `LEFT(UPPER(ticker),4)`; `EXPLAIN` confirma Seq Scan |
| `docs_chunks_ix_ticker` | 0,7 | idem — btree em `ticker` puro não atende a expressão |
| `idx_dul_job_name` | 0,5 | `data_update_logs` tem 2,4 MB; varredura é trivial |
| `idx_dul_started_at` | 0,3 | idem |

`CONCURRENTLY` para não travar a tabela (não roda dentro de transação):

```sql
DROP INDEX CONCURRENTLY IF EXISTS market.idx_metric_vintages_lookup;
DROP INDEX CONCURRENTLY IF EXISTS market.idx_calcmetric_conf;
DROP INDEX CONCURRENTLY IF EXISTS public.idx_chunks_ticker_date;
DROP INDEX CONCURRENTLY IF EXISTS public.docs_chunks_ix_ticker;
DROP INDEX CONCURRENTLY IF EXISTS public.idx_dul_job_name;
DROP INDEX CONCURRENTLY IF EXISTS public.idx_dul_started_at;
```

Reversão, se algum fizer falta:

```sql
CREATE INDEX idx_metric_vintages_lookup ON market.calculated_metric_vintages
    USING btree (ticker, period, year, quarter, metric_name, available_at, recorded_at);
CREATE INDEX idx_calcmetric_conf ON market.calculated_metrics USING btree (confidence_score);
CREATE INDEX idx_chunks_ticker_date ON public.docs_corporativos_chunks
    USING btree (ticker, document_date DESC);
CREATE INDEX docs_chunks_ix_ticker ON public.docs_corporativos_chunks USING btree (ticker);
CREATE INDEX idx_dul_job_name ON public.data_update_logs USING btree (job_name, started_at DESC);
CREATE INDEX idx_dul_started_at ON public.data_update_logs USING btree (started_at DESC);
```

Separado disso, `public.docs_corporativos` tem **três** índices únicos sobre a
mesma coluna `doc_hash` — `docs_corporativos_uq_hash` e
`docs_corporativos_doc_hash_uq` são idênticos, e `uq_docs_corporativos_doc_hash`
é a versão parcial (`WHERE doc_hash IS NOT NULL`). São ~1,2 MB e três
verificações de unicidade a cada inserção. Manter **um**; qual manter é decisão
de quem conhece a intenção original, por isso não está no bloco acima.

Nota de desempenho descoberta no caminho: como o RAG filtra por
`LEFT(UPPER(ticker), 4)`, toda busca faz Seq Scan em 93.498 chunks. O índice que
resolveria é de expressão — `ON public.docs_corporativos_chunks
(LEFT(UPPER(ticker), 4))` — mas ele custa espaço, que é justamente o que se está
tentando poupar. Decisão pendente.

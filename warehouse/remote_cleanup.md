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
```

`market.brapi_raw_payloads` permanece no Supabase nesta etapa. Ela é referenciada
por tabelas vitrine preservadas, como `historical_prices`, `income_statements`,
`balance_sheets`, `cash_flow_statements` e `fii_universe_history`; removê-la
exigiria apagar ou enfraquecer essas chaves estrangeiras. O custo restante é
aceitável e preserva a integridade do aplicativo.

Não remover automaticamente `public.docs_corporativos` ou
`public.docs_corporativos_chunks`: elas não estão na lista armazém e podem
conter documentos usados pelo aplicativo. Também não remover nenhuma tabela de
finanças pessoais.

Depois do `DROP`, aguardar o Supabase atualizar o uso e executar novamente o
diagnóstico. A operação é destrutiva no banco remoto; este arquivo documenta o
procedimento executado após a validação do backup local.

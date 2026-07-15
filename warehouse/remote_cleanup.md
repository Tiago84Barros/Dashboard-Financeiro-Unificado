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
Supabase, pois são armazém e não vitrine:

```sql
DROP TABLE IF EXISTS market.fii_lineage_edges;
DROP TABLE IF EXISTS market.fii_metric_observations;
DROP TABLE IF EXISTS market.fii_b3_security_history;
DROP TABLE IF EXISTS market.cri_security_observations;
DROP TABLE IF EXISTS market.fii_exposures;
DROP TABLE IF EXISTS market.historical_price_observations;
DROP TABLE IF EXISTS market.calculated_metric_vintages;
DROP TABLE IF EXISTS market.fii_documents;
DROP TABLE IF EXISTS market.fii_parser_versions;
DROP TABLE IF EXISTS market.fii_registry_observations;
DROP TABLE IF EXISTS market.fii_cvm_archive_loads;
DROP TABLE IF EXISTS market.fii_b3_archive_loads;
DROP TABLE IF EXISTS market.fii_cri_archive_loads;
DROP TABLE IF EXISTS market.fii_source_releases;
DROP TABLE IF EXISTS market.brapi_raw_payloads;
```

Não remover automaticamente `public.docs_corporativos` ou
`public.docs_corporativos_chunks`: elas não estão na lista armazém e podem
conter documentos usados pelo aplicativo. Também não remover nenhuma tabela de
finanças pessoais.

Depois do `DROP`, aguardar o Supabase atualizar o uso e executar novamente o
diagnóstico. A operação é destrutiva no banco remoto; este arquivo documenta o
procedimento, mas não o executa automaticamente.

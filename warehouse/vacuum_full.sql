-- Alívio imediato de espaço no Supabase: recompacta o inchaço (dead tuples)
-- acumulado pelas semanas do cron a cada 5 min. NÃO apaga nenhum dado.
--
-- Requer conexão DIRETA (porta 5432), não o pooler (6543) — VACUUM FULL precisa
-- de sessão. Desliga o timeout porque as tabelas grandes demoram.
SET statement_timeout = 0;

VACUUM (FULL, ANALYZE) market.fii_metric_observations;       -- 418 MB
VACUUM (FULL, ANALYZE) market.fii_b3_security_history;       -- 243 MB
VACUUM (FULL, ANALYZE) market.fii_lineage_edges;             -- 168 MB
VACUUM (FULL, ANALYZE) public.docs_corporativos_chunks;      -- 168 MB
VACUUM (FULL, ANALYZE) market.cri_security_observations;     -- 166 MB
VACUUM (FULL, ANALYZE) market.fii_exposures;                 -- 105 MB
VACUUM (FULL, ANALYZE) market.brapi_raw_payloads;            --  89 MB
VACUUM (FULL, ANALYZE) market.historical_price_observations; --  54 MB
VACUUM (FULL, ANALYZE) market.historical_prices;             --  55 MB
VACUUM (FULL, ANALYZE) market.calculated_metric_vintages;    --  27 MB
VACUUM (FULL, ANALYZE) market.calculated_metrics;            --  25 MB

-- Novo tamanho total:
SELECT pg_size_pretty(SUM(pg_total_relation_size(c.oid))) AS total_banco
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname IN ('public','market');

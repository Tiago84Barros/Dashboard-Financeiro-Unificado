-- Restaura, no Supabase, os DEFAULT que a 021 ja declara para
-- market.calculated_metric_vintages. A tabela remota foi criada antes da 021 e
-- o CREATE TABLE IF NOT EXISTS de la nao a alterou: recorded_at e
-- availability_quality ficaram NOT NULL sem default.
--
-- O efeito nao era cosmetico. A ingestao diaria nao escreve essas duas colunas
-- (conta com o default, como no armazem local), entao TODO ticker abortava com
-- NotNullViolation no passo de metricas. Foi assim que o feed de precos da B3
-- parou: a mediana de idade do preco chegou a 34 dias com a ingestao "rodando".
--
-- Idempotente e nao destrutivo: so define default para linhas futuras.

ALTER TABLE market.calculated_metric_vintages
    ALTER COLUMN recorded_at SET DEFAULT now(),
    ALTER COLUMN availability_quality SET DEFAULT 'first_seen_proxy';

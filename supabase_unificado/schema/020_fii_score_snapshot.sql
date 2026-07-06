-- ============================================================
-- 020_fii_score_snapshot.sql
-- Snapshot mensal point-in-time do score de FIIs e seus inputs
-- Banco: Dashboard Financeiro Unificado (Supabase - schema market)
--
-- Auditoria FII 2026-07-05: market.fiis é snapshot mutável (upsert
-- sobrescreve score/preço/DY/P/VP) — sem histórico dos INPUTS da
-- seleção, a metodologia FII nunca poderá ser validada (um rank-IC
-- como o das Empresas B3 exige score e inputs de cada mês).
--
-- Estende market.fii_metrics_monthly (chave ticker+ref_month, já
-- usada pela série CVM) com as colunas do score. O ETL grava no run
-- diário quando o ranking é aplicado; runs no mesmo mês sobrescrevem
-- ("último cálculo do mês"). As colunas CVM da mesma linha são
-- preservadas (o upsert só atualiza colunas presentes na linha).
--
-- SEGURANCA:
--   Nao contem DROP TABLE, TRUNCATE ou DELETE.
--   Comandos idempotentes (ADD COLUMN IF NOT EXISTS).
-- ============================================================

ALTER TABLE market.fii_metrics_monthly
    ADD COLUMN IF NOT EXISTS score NUMERIC(8,2);
ALTER TABLE market.fii_metrics_monthly
    ADD COLUMN IF NOT EXISTS score_version TEXT;
ALTER TABLE market.fii_metrics_monthly
    ADD COLUMN IF NOT EXISTS price NUMERIC(18,6);
ALTER TABLE market.fii_metrics_monthly
    ADD COLUMN IF NOT EXISTS dy_12m NUMERIC(10,6);
ALTER TABLE market.fii_metrics_monthly
    ADD COLUMN IF NOT EXISTS pvp NUMERIC(10,4);
ALTER TABLE market.fii_metrics_monthly
    ADD COLUMN IF NOT EXISTS liquidez_diaria NUMERIC(18,2);

COMMENT ON COLUMN market.fii_metrics_monthly.score IS
    'Score do ranking FII no mês (último cálculo do mês) — ponto-no-tempo p/ validação futura.';
COMMENT ON COLUMN market.fii_metrics_monthly.score_version IS
    'Versão da metodologia (data_pipeline/market/fii.py SCORE_VERSION) usada no cálculo.';
COMMENT ON COLUMN market.fii_metrics_monthly.pvp IS
    'P/VP EFETIVO usado no score (preço ÷ VPA CVM quando disponível; senão brapi).';
COMMENT ON COLUMN market.fii_metrics_monthly.dy_12m IS
    'DY 12m ex-amortizações usado no score (fração, ex.: 0.095).';
COMMENT ON COLUMN market.fii_metrics_monthly.liquidez_diaria IS
    'Liquidez diária mediana (R$/dia, janela de 6 meses) usada no score.';

-- ============================================================
-- FIM 020.
-- ============================================================

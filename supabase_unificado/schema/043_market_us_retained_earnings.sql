-- ============================================================
-- 043_market_us_retained_earnings.sql
-- Análise Avançada — lucros acumulados no balanço (Altman Z-Score, termo X2).
--
-- ADITIVO e idempotente: só ADD COLUMN IF NOT EXISTS. Linhas já ingeridas ficam
-- com NULL (ausente ≠ zero) — o Z-Score retorna None até um refresh de
-- fundamentos preencher a coluna. Sem DROP/TRUNCATE/DELETE.
-- ============================================================

ALTER TABLE market_us.balance_sheets
    ADD COLUMN IF NOT EXISTS retained_earnings NUMERIC(24,2);

COMMENT ON COLUMN market_us.balance_sheets.retained_earnings IS
    'Lucros acumulados (FMP retainedEarnings). Usado no termo X2 do Altman Z-Score.';

-- ============================================================
-- FIM 043.
-- ============================================================

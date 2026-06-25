-- 015_market_fiis.sql
-- Snapshot de FIIs (fundos imobiliários) p/ a seção de seleção.
-- FII não tem DRE/ROE: guarda só as métricas de seleção (DY 12m, P/VP, liquidez)
-- + score. Fonte: BRAPI Pro. Idempotente.

CREATE TABLE IF NOT EXISTS market.fiis (
    ticker          TEXT        PRIMARY KEY,
    name            TEXT,
    segmento        TEXT,
    price           NUMERIC(18,4),
    pvp             NUMERIC(12,4),
    dy_12m          NUMERIC(10,6),
    liquidez_diaria NUMERIC(20,2),
    score           NUMERIC(6,2),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE market.fiis IS 'Snapshot/ranking de FIIs (BRAPI). Upsert por ticker.';
CREATE INDEX IF NOT EXISTS idx_fiis_score ON market.fiis (score DESC);
CREATE INDEX IF NOT EXISTS idx_fiis_segmento ON market.fiis (segmento);

DROP TRIGGER IF EXISTS trg_fiis_updated ON market.fiis;
CREATE TRIGGER trg_fiis_updated BEFORE UPDATE ON market.fiis
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();

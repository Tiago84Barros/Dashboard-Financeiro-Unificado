-- Vitrine compacta para o App 4.
-- O payload é gerado no warehouse local e publicado sem transportar as
-- tabelas históricas volumosas para o Supabase.
CREATE TABLE IF NOT EXISTS market.fii_selection_inputs (
    ticker              TEXT PRIMARY KEY,
    payload_json        JSONB NOT NULL,
    as_of_date          DATE NOT NULL,
    available_at        TIMESTAMPTZ NOT NULL,
    knowledge_at        TIMESTAMPTZ NOT NULL,
    reference_date      DATE,
    vintage             TEXT NOT NULL,
    source              TEXT NOT NULL,
    quality_status      TEXT NOT NULL CHECK (quality_status IN ('published', 'accepted', 'quarantined')),
    schema_version      TEXT NOT NULL,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload_sha256      TEXT NOT NULL,
    coverage_json       JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_fii_selection_inputs_generated
    ON market.fii_selection_inputs (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_fii_selection_inputs_quality
    ON market.fii_selection_inputs (quality_status, generated_at DESC);

COMMENT ON TABLE market.fii_selection_inputs IS
    'Snapshot compacto, auditável e PIT dos inputs da seleção de FIIs publicado do warehouse local.';
COMMENT ON COLUMN market.fii_selection_inputs.payload_json IS
    'Payload final por ticker produzido por core.market_read.load_fii_methodology_inputs.';

ALTER TABLE market.fii_selection_inputs ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE market.fii_selection_inputs FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS data_api_private_deny ON market.fii_selection_inputs;
CREATE POLICY data_api_private_deny
    ON market.fii_selection_inputs AS RESTRICTIVE
    FOR ALL TO anon, authenticated
    USING (false) WITH CHECK (false);

-- Cobertura CVM estruturada e separação entre prontidão dos dados e validação PIT.
-- O schema market permanece privado e acessível somente pelo backend/ETL.

ALTER TABLE market.fii_score_snapshots
    ADD COLUMN IF NOT EXISTS data_readiness_status text NOT NULL DEFAULT 'insufficient';

ALTER TABLE market.fii_score_snapshots
    DROP CONSTRAINT IF EXISTS fii_score_snapshots_data_readiness_status_check;
ALTER TABLE market.fii_score_snapshots
    ADD CONSTRAINT fii_score_snapshots_data_readiness_status_check
    CHECK (data_readiness_status IN ('ready','insufficient'));

CREATE INDEX IF NOT EXISTS idx_fii_score_data_readiness
    ON market.fii_score_snapshots
       (methodology_version, data_readiness_status, reference_date DESC);

INSERT INTO market.fii_methodology_versions
    (methodology_version, formula_version, manifest_json, status)
VALUES
    ('4.1.0', 'br-fii-income-resilience-4.1.0',
     '{"objective":"renda recorrente com crescimento patrimonial e resiliência","confidence_formula":"weighted_geometric_mean","data_sources":["brapi_pro","cvm_monthly","cvm_quarterly","cvm_annual","cvm_dfin"],"publication_policy":"data_ready_and_pit_validation_required"}'::jsonb,
     'validation')
ON CONFLICT (methodology_version) DO UPDATE SET
    formula_version=EXCLUDED.formula_version,
    manifest_json=EXCLUDED.manifest_json,
    status=CASE WHEN market.fii_methodology_versions.status='passed'
                THEN 'passed' ELSE EXCLUDED.status END;

COMMENT ON COLUMN market.fii_score_snapshots.data_readiness_status IS
    'Prontidão individual dos dados; não equivale à aprovação estatística PIT da metodologia.';

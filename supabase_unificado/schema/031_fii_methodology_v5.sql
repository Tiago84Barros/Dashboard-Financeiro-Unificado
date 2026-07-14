-- Registro idempotente da metodologia integrada usada pelo codigo e por todas
-- as FKs de snapshots, validacoes e eventos de rebalanceamento.

INSERT INTO market.fii_methodology_versions
    (methodology_version, formula_version, manifest_json, status)
VALUES (
    '5.0.0',
    'br-fii-integrated-income-resilience-5.0.0',
    '{
      "objective":"renda recorrente com crescimento patrimonial e resiliencia",
      "stages":["eligibility","type_score","data_confidence","scenario_and_correlation_optimization"],
      "missing_data_policy":"missing_reduces_coverage_and_confidence; never_zero_or_neutral",
      "publication_policy":"diligence_only_until_point_in_time_validation_passes"
    }'::jsonb,
    'validation'
)
ON CONFLICT (methodology_version) DO UPDATE SET
    formula_version=EXCLUDED.formula_version,
    manifest_json=EXCLUDED.manifest_json,
    status=CASE WHEN market.fii_methodology_versions.status='passed'
                THEN 'passed' ELSE EXCLUDED.status END;

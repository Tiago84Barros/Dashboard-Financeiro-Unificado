-- Evidencia reproduzivel para validacoes da metodologia Empresas B3.
-- Tabelas backend-only: sem exposicao pela Data API.

CREATE TABLE IF NOT EXISTS market.b3_validation_runs (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    methodology_version TEXT NOT NULL,
    score_version TEXT NOT NULL,
    validation_mode TEXT NOT NULL,
    data_as_of TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed'
        CHECK (status IN ('completed','blocked','failed')),
    input_params JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_hash TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_b3_validation_runs_created
    ON market.b3_validation_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_b3_validation_runs_methodology
    ON market.b3_validation_runs (methodology_version, score_version, created_at DESC);

CREATE TABLE IF NOT EXISTS market.b3_data_readiness_snapshots (
    id BIGSERIAL PRIMARY KEY,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    universe_definition TEXT NOT NULL,
    snapshot_json JSONB NOT NULL,
    artifact_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_b3_data_readiness_observed
    ON market.b3_data_readiness_snapshots (observed_at DESC);

COMMENT ON TABLE market.b3_validation_runs IS
    'Manifestos imutaveis de validacoes B3. PIT estrito exige published_at, nao first_seen_proxy ou migration_baseline.';
COMMENT ON TABLE market.b3_data_readiness_snapshots IS
    'Cobertura, lineage e qualidade de disponibilidade dos dados B3 em cada observacao.';

DO $$
DECLARE table_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['b3_validation_runs','b3_data_readiness_snapshots'] LOOP
    EXECUTE format('ALTER TABLE market.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE market.%I FROM PUBLIC, anon, authenticated', table_name);
    EXECUTE format('DROP POLICY IF EXISTS data_api_private_deny ON market.%I', table_name);
    EXECUTE format(
      'CREATE POLICY data_api_private_deny ON market.%I AS RESTRICTIVE '
      'FOR ALL TO anon, authenticated USING (false) WITH CHECK (false)', table_name
    );
  END LOOP;
END $$;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA market FROM PUBLIC, anon, authenticated;

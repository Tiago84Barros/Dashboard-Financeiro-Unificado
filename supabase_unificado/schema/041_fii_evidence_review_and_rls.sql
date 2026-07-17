-- Revisão auditável de evidências FII e hardening das tabelas backend-only.
-- Rejeições produzidas pelo próprio parser não são revisão humana e, portanto,
-- não podem calibrar a precisão empírica do parser.

CREATE TABLE IF NOT EXISTS market.fii_schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE market.fii_schema_migrations ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE market.fii_schema_migrations
    FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS data_api_private_deny ON market.fii_schema_migrations;
CREATE POLICY data_api_private_deny ON market.fii_schema_migrations AS RESTRICTIVE
    FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);

DO $$
BEGIN
  IF to_regclass('market.fii_extraction_evidence') IS NOT NULL THEN
    EXECUTE 'ALTER TABLE market.fii_extraction_evidence '
            'ADD COLUMN IF NOT EXISTS validation_method text NOT NULL DEFAULT ''pending'', '
            'ADD COLUMN IF NOT EXISTS promoted_observation_id bigint '
            'REFERENCES market.fii_metric_observations(id) ON DELETE SET NULL, '
            'ADD COLUMN IF NOT EXISTS review_hash text';
    EXECUTE 'UPDATE market.fii_extraction_evidence SET validation_method = CASE '
            'WHEN reviewed_at IS NOT NULL AND reviewer_id IS NOT NULL THEN ''human'' '
            'WHEN validation_status = ''rejected'' THEN ''parser_rule'' ELSE ''pending'' END '
            'WHERE validation_method = ''pending''';
    EXECUTE 'ALTER TABLE market.fii_extraction_evidence '
            'DROP CONSTRAINT IF EXISTS fii_extraction_evidence_validation_method_check';
    EXECUTE 'ALTER TABLE market.fii_extraction_evidence '
            'ADD CONSTRAINT fii_extraction_evidence_validation_method_check '
            'CHECK (validation_method IN (''pending'',''human'',''cross_source'',''parser_rule''))';
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_fii_evidence_review_queue '
            'ON market.fii_extraction_evidence '
            '(validation_status,validation_method,confidence DESC,id) '
            'WHERE validation_status=''pending''';
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_fii_evidence_promoted_observation '
            'ON market.fii_extraction_evidence (promoted_observation_id) '
            'WHERE promoted_observation_id IS NOT NULL';
  END IF;
  IF to_regclass('market.fii_documents') IS NOT NULL THEN
    EXECUTE 'CREATE INDEX IF NOT EXISTS idx_fii_documents_ticker_review '
            'ON market.fii_documents (ticker,reference_date DESC,processing_status)';
  END IF;
  -- Remove calibrações contaminadas. A rotina reconstruirá somente revisões
  -- com reviewer_id e reviewed_at.
  IF to_regclass('market.fii_parser_calibrations') IS NOT NULL THEN
    EXECUTE 'DELETE FROM market.fii_parser_calibrations';
  END IF;
END $$;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'fii_documents','fii_document_versions','fii_extraction_runs',
    'fii_extraction_evidence','fii_metric_observations','fii_audit_events',
    'fii_lineage_edges','fii_parser_versions','fii_parser_calibrations',
    'fii_reconciliation_issues','fii_exposures','fii_schema_migrations'
  ] LOOP
    IF to_regclass(format('market.%I', table_name)) IS NOT NULL THEN
      EXECUTE format('ALTER TABLE market.%I ENABLE ROW LEVEL SECURITY', table_name);
      EXECUTE format(
        'REVOKE ALL PRIVILEGES ON TABLE market.%I FROM PUBLIC, anon, authenticated',
        table_name
      );
      EXECUTE format('DROP POLICY IF EXISTS data_api_private_deny ON market.%I', table_name);
      EXECUTE format(
        'CREATE POLICY data_api_private_deny ON market.%I AS RESTRICTIVE '
        'FOR ALL TO anon, authenticated USING (false) WITH CHECK (false)',
        table_name
      );
    END IF;
  END LOOP;
END $$;

REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA market
    FROM PUBLIC, anon, authenticated;

DO $$ BEGIN
  IF to_regclass('market.fii_extraction_evidence') IS NOT NULL THEN
    EXECUTE 'COMMENT ON COLUMN market.fii_extraction_evidence.validation_method IS '
            '''Origem da decisão: human é a única elegível para calibração empírica do parser.''';
    EXECUTE 'COMMENT ON COLUMN market.fii_extraction_evidence.promoted_observation_id IS '
            '''Observação aceita/corrigida gerada por uma decisão auditável.''';
  END IF;
END $$;

INSERT INTO market.fii_schema_migrations(version) VALUES ('041')
ON CONFLICT (version) DO UPDATE SET applied_at=EXCLUDED.applied_at;

-- Indices de suporte para FKs e filas do pipeline FII.
-- Mantem delecoes/reconciliacoes previsiveis conforme o volume historico cresce.

CREATE INDEX IF NOT EXISTS idx_brapi_raw_payloads_supersedes
    ON market.brapi_raw_payloads (supersedes_id)
    WHERE supersedes_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cri_security_source_release
    ON market.cri_security_observations (source_release_id)
    WHERE source_release_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fii_documents_current_version
    ON market.fii_documents (current_version_id)
    WHERE current_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fii_document_versions_supersedes
    ON market.fii_document_versions (supersedes_id)
    WHERE supersedes_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fii_extraction_runs_document_version
    ON market.fii_extraction_runs (document_version_id, finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_fii_extraction_runs_parser
    ON market.fii_extraction_runs (parser_name, parser_version);
CREATE INDEX IF NOT EXISTS idx_fii_extraction_evidence_run
    ON market.fii_extraction_evidence (extraction_run_id);
CREATE INDEX IF NOT EXISTS idx_fii_exposures_raw_payload
    ON market.fii_exposures (raw_payload_id)
    WHERE raw_payload_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fii_exposures_source_release
    ON market.fii_exposures (source_release_id)
    WHERE source_release_id IS NOT NULL;

ALTER FUNCTION market.set_updated_at()
    SET search_path = pg_catalog, market;

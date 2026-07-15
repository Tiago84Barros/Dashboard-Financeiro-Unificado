-- Checkpoint por hash/parser dos arquivos públicos de CRI da CVM.
CREATE TABLE IF NOT EXISTS market.fii_cri_archive_loads (
    archive_year integer NOT NULL CHECK (archive_year BETWEEN 2016 AND 2200),
    archive_sha256 text NOT NULL,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    source_url text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','completed','failed')),
    raw_payload_id bigint REFERENCES market.brapi_raw_payloads(id) ON DELETE SET NULL,
    source_release_id bigint REFERENCES market.fii_source_releases(id) ON DELETE SET NULL,
    security_observation_count integer NOT NULL DEFAULT 0 CHECK (security_observation_count >= 0),
    fii_observation_count integer NOT NULL DEFAULT 0 CHECK (fii_observation_count >= 0),
    fii_exposure_count integer NOT NULL DEFAULT 0 CHECK (fii_exposure_count >= 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error_message text,
    PRIMARY KEY (archive_year, archive_sha256, parser_name, parser_version)
);

CREATE INDEX IF NOT EXISTS idx_fii_cri_archive_status
    ON market.fii_cri_archive_loads (parser_name, parser_version, status, archive_year DESC);

ALTER TABLE market.fii_cri_archive_loads ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE market.fii_cri_archive_loads FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS data_api_private_deny ON market.fii_cri_archive_loads;
CREATE POLICY data_api_private_deny ON market.fii_cri_archive_loads
    AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);

COMMENT ON TABLE market.fii_cri_archive_loads IS
    'Checkpoint por hash/parser dos arquivos de CRI; evita reprocessamento e preserva revisões.';

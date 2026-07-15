-- Releases e checkpoints reproduziveis dos arquivos estruturados de FII da CVM.
-- O hash do artefato identifica revisoes retroativas; a versao do parser
-- determina quando o mesmo arquivo precisa ser reprocessado.

CREATE TABLE IF NOT EXISTS market.fii_cvm_archive_loads (
    archive_kind text NOT NULL CHECK (archive_kind IN
        ('monthly','quarterly','annual','financials','eventual')),
    archive_year integer NOT NULL CHECK (archive_year BETWEEN 2016 AND 2200),
    archive_sha256 text NOT NULL,
    parser_name text NOT NULL,
    parser_version text NOT NULL,
    source_url text NOT NULL,
    status text NOT NULL CHECK (status IN ('running','completed','failed')),
    raw_payload_id bigint REFERENCES market.brapi_raw_payloads(id) ON DELETE SET NULL,
    source_release_id bigint REFERENCES market.fii_source_releases(id) ON DELETE SET NULL,
    observation_count integer NOT NULL DEFAULT 0 CHECK (observation_count >= 0),
    exposure_count integer NOT NULL DEFAULT 0 CHECK (exposure_count >= 0),
    document_count integer NOT NULL DEFAULT 0 CHECK (document_count >= 0),
    context_count integer NOT NULL DEFAULT 0 CHECK (context_count >= 0),
    started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error_message text,
    PRIMARY KEY (archive_kind, archive_year, archive_sha256,
                 parser_name, parser_version)
);

CREATE INDEX IF NOT EXISTS idx_fii_cvm_archive_status
    ON market.fii_cvm_archive_loads (status, archive_kind, archive_year DESC);
CREATE INDEX IF NOT EXISTS idx_fii_cvm_archive_release
    ON market.fii_cvm_archive_loads (source_release_id)
    WHERE source_release_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fii_cvm_archive_raw
    ON market.fii_cvm_archive_loads (raw_payload_id)
    WHERE raw_payload_id IS NOT NULL;

ALTER TABLE market.fii_cvm_archive_loads ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE market.fii_cvm_archive_loads
    FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS data_api_private_deny ON market.fii_cvm_archive_loads;
CREATE POLICY data_api_private_deny ON market.fii_cvm_archive_loads
    AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);

COMMENT ON TABLE market.fii_cvm_archive_loads IS
    'Checkpoint por hash e parser dos arquivos oficiais CVM; permite retomada, revisoes retroativas e reprocessamento reproduzivel.';

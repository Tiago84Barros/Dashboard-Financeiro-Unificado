-- Checkpoints por arquivo oficial B3: retomada segura e observável sem varrer a fato.
CREATE TABLE IF NOT EXISTS market.fii_b3_archive_loads (
    archive_year integer NOT NULL CHECK (archive_year BETWEEN 1986 AND 2200),
    archive_sha256 text NOT NULL,
    source_url text NOT NULL,
    expected_rows integer NOT NULL CHECK (expected_rows >= 0),
    loaded_rows integer NOT NULL DEFAULT 0 CHECK (loaded_rows >= 0),
    status text NOT NULL CHECK (status IN ('running','completed','failed')),
    raw_payload_id bigint REFERENCES market.brapi_raw_payloads(id) ON DELETE SET NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    error_message text,
    PRIMARY KEY (archive_year, archive_sha256)
);

CREATE INDEX IF NOT EXISTS idx_fii_b3_archive_status
    ON market.fii_b3_archive_loads (status, archive_year DESC);
CREATE INDEX IF NOT EXISTS idx_fii_b3_archive_raw
    ON market.fii_b3_archive_loads (raw_payload_id)
    WHERE raw_payload_id IS NOT NULL;

ALTER TABLE market.fii_b3_archive_loads ENABLE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE market.fii_b3_archive_loads FROM PUBLIC, anon, authenticated;
DROP POLICY IF EXISTS data_api_private_deny ON market.fii_b3_archive_loads;
CREATE POLICY data_api_private_deny ON market.fii_b3_archive_loads
    AS RESTRICTIVE FOR ALL TO anon, authenticated USING (false) WITH CHECK (false);

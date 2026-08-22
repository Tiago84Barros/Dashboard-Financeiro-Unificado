-- O mesmo COTAHIST deve ser reprocessado quando o parser mudar.
ALTER TABLE market.fii_b3_archive_loads
    ADD COLUMN IF NOT EXISTS parser_name text NOT NULL DEFAULT 'b3_cotahist',
    ADD COLUMN IF NOT EXISTS parser_version text NOT NULL DEFAULT '1.0.0';

ALTER TABLE market.fii_b3_archive_loads
    DROP CONSTRAINT IF EXISTS fii_b3_archive_loads_pkey;
ALTER TABLE market.fii_b3_archive_loads
    ADD CONSTRAINT fii_b3_archive_loads_pkey PRIMARY KEY
        (archive_year,archive_sha256,parser_name,parser_version);

DROP INDEX IF EXISTS market.idx_fii_b3_archive_status;
CREATE INDEX IF NOT EXISTS idx_fii_b3_archive_parser_status
    ON market.fii_b3_archive_loads
       (parser_name,parser_version,status,archive_year DESC);

COMMENT ON COLUMN market.fii_b3_archive_loads.parser_version IS
    'Versao imutavel do parser fixed-width; mudancas exigem novo checkpoint para o mesmo hash.';

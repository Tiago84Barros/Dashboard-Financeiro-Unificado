-- Permite backfill documental auditavel sem reter dezenas de GB em binarios.
-- source_hash significa: URL publica no documento + SHA-256/tamanho/MIME na versao.

DO $$
BEGIN
  IF to_regclass('market.fii_document_versions') IS NOT NULL THEN
    ALTER TABLE market.fii_document_versions
      DROP CONSTRAINT IF EXISTS fii_document_versions_storage_backend_check;
    ALTER TABLE market.fii_document_versions
      ADD CONSTRAINT fii_document_versions_storage_backend_check
      CHECK (storage_backend IN (
        'supabase_storage','local_cache','remote_only','source_hash'
      ));
    CREATE INDEX IF NOT EXISTS idx_fii_document_versions_source_hash
      ON market.fii_document_versions (content_sha256)
      WHERE storage_backend='source_hash';
    COMMENT ON COLUMN market.fii_document_versions.storage_backend IS
      'source_hash preserva URL, SHA-256, tamanho e MIME sem reter o binario local.';
  END IF;
END $$;

INSERT INTO market.fii_schema_migrations(version) VALUES ('042')
ON CONFLICT (version) DO UPDATE SET applied_at=EXCLUDED.applied_at;

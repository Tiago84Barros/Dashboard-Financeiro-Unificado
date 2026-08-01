-- ROLLBACK DE EMERGÊNCIA — executar somente após backup e confirmação humana.
-- As tabelas abaixo contêm evidência/auditoria e o DROP é destrutivo.

DROP INDEX IF EXISTS market.idx_fii_evidence_priority_queue;
ALTER TABLE market.fii_extraction_evidence
    DROP COLUMN IF EXISTS review_priority,
    DROP COLUMN IF EXISTS value_nature;
DROP TABLE IF EXISTS market.fii_document_findings;
DROP TABLE IF EXISTS market.fii_project_observations;
DROP TABLE IF EXISTS market.fii_projects;
DROP TABLE IF EXISTS market.fii_document_sources;
DELETE FROM market.fii_schema_migrations WHERE version='046';

-- Índices de cobertura para relacionamentos introduzidos/ativados pela metodologia FII v6.
CREATE INDEX IF NOT EXISTS idx_fii_exposures_canonical_entity
    ON market.fii_exposures (canonical_entity_id)
    WHERE canonical_entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_fii_validation_methodology
    ON market.fii_validation_runs (methodology_version);

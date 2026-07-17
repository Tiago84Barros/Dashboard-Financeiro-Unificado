-- Preserva a trilha de auditoria B3 como append-only.
-- Correcao de uma execucao deve gerar novo snapshot/run, nunca alterar evidencia.

CREATE OR REPLACE FUNCTION market.prevent_b3_audit_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'market.% e append-only; gere um novo registro de auditoria', TG_TABLE_NAME;
END;
$$;

REVOKE ALL ON FUNCTION market.prevent_b3_audit_mutation() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_b3_validation_runs_append_only ON market.b3_validation_runs;
CREATE TRIGGER trg_b3_validation_runs_append_only
    BEFORE UPDATE OR DELETE ON market.b3_validation_runs
    FOR EACH ROW EXECUTE FUNCTION market.prevent_b3_audit_mutation();

DROP TRIGGER IF EXISTS trg_b3_readiness_append_only ON market.b3_data_readiness_snapshots;
CREATE TRIGGER trg_b3_readiness_append_only
    BEFORE UPDATE OR DELETE ON market.b3_data_readiness_snapshots
    FOR EACH ROW EXECUTE FUNCTION market.prevent_b3_audit_mutation();

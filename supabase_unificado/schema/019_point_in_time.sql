-- ============================================================
-- 019_point_in_time.sql
-- Point-in-time nas demonstrações (auditoria 2026-07, pendência #1)
-- Banco: Dashboard Financeiro Unificado (Supabase - schema market)
--
-- OBJETIVO:
--   Rastrear QUANDO cada linha de demonstração ficou disponível no NOSSO
--   banco e DE QUAL payload bruto ela veio, em income_statements,
--   balance_sheets e cash_flow_statements:
--     - period_end_date  : fim do exercício (endDate do payload BRAPI);
--     - first_seen_at    : quando a linha entrou no nosso banco (proxy de
--                          available_at — NÃO é a data de publicação CVM);
--     - raw_payload_id   : FK p/ market.brapi_raw_payloads (proveniência).
--
-- LIMITAÇÃO DOCUMENTADA:
--   Linhas que JÁ existiam antes desta migração recebem first_seen_at =
--   NOW() da migração (o DEFAULT preenche no ADD COLUMN) — ou seja, a data
--   da migração, não a de ingestão original. O published_at real da CVM e
--   o versionamento de republicações (restatements) seguem pendentes.
--
-- SEGURANCA:
--   Nao contem DROP TABLE, TRUNCATE ou DELETE.
--   Comandos idempotentes (ADD COLUMN IF NOT EXISTS).
--
-- ETL: first_seen_at NUNCA entra nas colunas de UPDATE do ON CONFLICT
--   (re-ingestão não pode apagar o histórico de disponibilidade);
--   period_end_date e raw_payload_id podem atualizar.
-- ============================================================

-- ── income_statements ───────────────────────────────────────
ALTER TABLE market.income_statements
    ADD COLUMN IF NOT EXISTS period_end_date DATE;
ALTER TABLE market.income_statements
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE market.income_statements
    ADD COLUMN IF NOT EXISTS raw_payload_id BIGINT
        REFERENCES market.brapi_raw_payloads(id) ON DELETE SET NULL;
COMMENT ON COLUMN market.income_statements.period_end_date IS
    'Fim do exercício (endDate do payload BRAPI).';
COMMENT ON COLUMN market.income_statements.first_seen_at IS
    'Quando a linha entrou no NOSSO banco (proxy de available_at). NÃO é a data de publicação CVM. Linhas anteriores à migração 019 têm a data da migração.';
COMMENT ON COLUMN market.income_statements.raw_payload_id IS
    'Payload bruto (market.brapi_raw_payloads) de onde a linha foi normalizada.';

-- ── balance_sheets ──────────────────────────────────────────
ALTER TABLE market.balance_sheets
    ADD COLUMN IF NOT EXISTS period_end_date DATE;
ALTER TABLE market.balance_sheets
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE market.balance_sheets
    ADD COLUMN IF NOT EXISTS raw_payload_id BIGINT
        REFERENCES market.brapi_raw_payloads(id) ON DELETE SET NULL;
COMMENT ON COLUMN market.balance_sheets.period_end_date IS
    'Fim do exercício (endDate do payload BRAPI).';
COMMENT ON COLUMN market.balance_sheets.first_seen_at IS
    'Quando a linha entrou no NOSSO banco (proxy de available_at). NÃO é a data de publicação CVM. Linhas anteriores à migração 019 têm a data da migração.';
COMMENT ON COLUMN market.balance_sheets.raw_payload_id IS
    'Payload bruto (market.brapi_raw_payloads) de onde a linha foi normalizada.';

-- ── cash_flow_statements ────────────────────────────────────
ALTER TABLE market.cash_flow_statements
    ADD COLUMN IF NOT EXISTS period_end_date DATE;
ALTER TABLE market.cash_flow_statements
    ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE market.cash_flow_statements
    ADD COLUMN IF NOT EXISTS raw_payload_id BIGINT
        REFERENCES market.brapi_raw_payloads(id) ON DELETE SET NULL;
COMMENT ON COLUMN market.cash_flow_statements.period_end_date IS
    'Fim do exercício (endDate do payload BRAPI).';
COMMENT ON COLUMN market.cash_flow_statements.first_seen_at IS
    'Quando a linha entrou no NOSSO banco (proxy de available_at). NÃO é a data de publicação CVM. Linhas anteriores à migração 019 têm a data da migração.';
COMMENT ON COLUMN market.cash_flow_statements.raw_payload_id IS
    'Payload bruto (market.brapi_raw_payloads) de onde a linha foi normalizada.';

-- Índices p/ auditoria/joins de proveniência (leves, opcionais em consulta).
CREATE INDEX IF NOT EXISTS idx_income_raw_payload   ON market.income_statements (raw_payload_id);
CREATE INDEX IF NOT EXISTS idx_balance_raw_payload  ON market.balance_sheets (raw_payload_id);
CREATE INDEX IF NOT EXISTS idx_cashflow_raw_payload ON market.cash_flow_statements (raw_payload_id);

-- ============================================================
-- FIM 019.
-- ============================================================

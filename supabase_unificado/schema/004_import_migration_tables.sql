-- ============================================================
-- 004_import_migration_tables.sql
-- Tabelas de preferências, alertas, importação e migração
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE, DROP SCHEMA.
--   Todos os CREATE usam IF NOT EXISTS.
--   Seguro para executar múltiplas vezes.
--
-- DEPENDÊNCIAS: 001_core_tables.sql (profiles)
-- TABELAS: alerts, user_settings, import_batches,
--          import_logs, migration_source_map
-- PRÓXIMO ARQUIVO: 005_indexes.sql
-- ============================================================

-- ── alerts ───────────────────────────────────────────────────
-- Alertas configuráveis por preço de ativo, orçamento ou meta.
-- Tabela nova — sem dados de migração disponíveis.

CREATE TABLE IF NOT EXISTS alerts (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID          NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    type           VARCHAR(50)   NOT NULL
                     CHECK (type IN ('price_target','budget_exceeded','goal_reached','debt_due','custom')),
    reference_id   UUID,
    reference_type VARCHAR(50)   CHECK (reference_type IN ('asset','budget','goal','debt')),
    condition      VARCHAR(50)   CHECK (condition IN ('above','below','equals','percentage')),
    trigger_value  NUMERIC(15,6),
    message        TEXT,
    active         BOOLEAN       NOT NULL DEFAULT TRUE,
    triggered      BOOLEAN       NOT NULL DEFAULT FALSE,
    triggered_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  alerts               IS 'Alertas configuráveis. Tabela nova — população manual.';
COMMENT ON COLUMN alerts.reference_id  IS 'UUID do objeto alvo (ativo, orçamento, meta, dívida). Sem FK estrutural por flexibilidade.';
COMMENT ON COLUMN alerts.triggered     IS 'TRUE = alerta já disparou. Reset manual pelo usuário.';

-- ── user_settings ────────────────────────────────────────────
-- Preferências do usuário. Relação 1:1 com profiles (user_id = PK e FK).
-- Seed em 008_seed_reference_data.sql (1 linha padrão após criar profiles).

CREATE TABLE IF NOT EXISTS user_settings (
    user_id               UUID        PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    default_currency      CHAR(3)     NOT NULL DEFAULT 'BRL',
    language              VARCHAR(10) NOT NULL DEFAULT 'pt-BR',
    theme                 VARCHAR(20) NOT NULL DEFAULT 'dark'
                            CHECK (theme IN ('dark','light')),
    notifications_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    month_start_day       SMALLINT    NOT NULL DEFAULT 1
                            CHECK (month_start_day BETWEEN 1 AND 28),
    extra_settings        JSONB,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  user_settings                IS 'Preferências do usuário. 1:1 com profiles. Seed em 008.';
COMMENT ON COLUMN user_settings.extra_settings IS 'Configurações futuras em JSONB. Evita ALTER TABLE para novas preferências.';
COMMENT ON COLUMN user_settings.month_start_day IS 'Dia de início do mês financeiro (1–28). Padrão 1.';

-- ── import_batches ───────────────────────────────────────────
-- Rastreia cada lote de importação (CSV, banco de origem, ETL).
-- Criado automaticamente pelo ETL — não inserir manualmente.

CREATE TABLE IF NOT EXISTS import_batches (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    source           VARCHAR(50) NOT NULL
                       CHECK (source IN ('app1_dashboard','app2_investments','app3_controle','csv','manual','api')),
    filename         VARCHAR(500),
    status           VARCHAR(20) NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','processing','completed','error')),
    total_records    INTEGER     NOT NULL DEFAULT 0,
    imported_records INTEGER     NOT NULL DEFAULT 0,
    error_records    INTEGER     NOT NULL DEFAULT 0,
    dry_run          BOOLEAN     NOT NULL DEFAULT TRUE,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at     TIMESTAMPTZ,
    notes            TEXT
);

COMMENT ON TABLE  import_batches         IS 'Rastreia lotes de importação. Criado automaticamente pelo ETL.';
COMMENT ON COLUMN import_batches.dry_run IS 'TRUE = simulação sem gravação. Padrão TRUE por segurança.';
COMMENT ON COLUMN import_batches.source  IS 'Origem do lote: app1/app2/app3 para migração histórica; csv/manual para novos dados.';

-- ── import_logs ──────────────────────────────────────────────
-- Log por registro dentro de um lote de importação.
-- Sem RLS — dados de controle interno, não dado pessoal.
-- Criado automaticamente pelo ETL.

CREATE TABLE IF NOT EXISTS import_logs (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id       UUID         NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    target_table   VARCHAR(100) NOT NULL,
    source_id      TEXT,
    destination_id UUID,
    action         VARCHAR(20)  NOT NULL CHECK (action IN ('inserted','skipped','error')),
    message        TEXT,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  import_logs              IS 'Log por registro dentro de um lote. Sem RLS — controle interno.';
COMMENT ON COLUMN import_logs.source_id    IS 'ID original na fonte (pode ser qualquer tipo, armazenado como TEXT).';
COMMENT ON COLUMN import_logs.destination_id IS 'UUID gerado no banco destino após inserção.';

-- ── migration_source_map ─────────────────────────────────────
-- Mapa de idempotência: garante que cada registro de origem
-- seja migrado exatamente uma vez, mesmo em reexecuções.
-- Sem RLS — dados de controle interno.
-- UNIQUE (source, source_table, source_id) é a chave de idempotência.

CREATE TABLE IF NOT EXISTS migration_source_map (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    target_table VARCHAR(100) NOT NULL,
    target_id    UUID         NOT NULL,
    source       VARCHAR(50)  NOT NULL CHECK (source IN ('app1','app2','app3','csv','manual')),
    source_table VARCHAR(100) NOT NULL,
    source_id    TEXT         NOT NULL,
    migrated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (target_table, target_id, source),
    UNIQUE (source, source_table, source_id)
);

COMMENT ON TABLE  migration_source_map          IS 'Mapa origem→destino para idempotência da migração. Sem RLS — controle interno.';
COMMENT ON COLUMN migration_source_map.source_id IS 'ID original (UUID, INTEGER, string). TEXT para compatibilidade universal.';
COMMENT ON COLUMN migration_source_map.source    IS 'Identificador da fonte: app1 (Dashboard), app2 (Investimentos SQLite), app3 (Controle Financeiro).';

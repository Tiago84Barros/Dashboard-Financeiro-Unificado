-- 018_fiis_hardening.sql
-- Integridade, rastreabilidade e isolamento por usuário da seção Seleção de FIIs.
-- Execute após 015, 016 e 017. Idempotente.

ALTER TABLE market.fiis
    ADD COLUMN IF NOT EXISTS score_version TEXT,
    ADD COLUMN IF NOT EXISTS score_calculated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS metrics_fetched_at TIMESTAMPTZ;

ALTER TABLE market.fiis
    ALTER COLUMN cvm_ref_date TYPE DATE USING (
        CASE WHEN cvm_ref_date::TEXT ~ '^\d{4}-\d{2}-\d{2}'
             THEN LEFT(cvm_ref_date::TEXT, 10)::DATE END
    ),
    ALTER COLUMN vacancia_ref_date TYPE DATE USING (
        CASE WHEN vacancia_ref_date::TEXT ~ '^\d{4}-\d{2}-\d{2}'
             THEN LEFT(vacancia_ref_date::TEXT, 10)::DATE END
    );

ALTER TABLE market.fiis
    DROP CONSTRAINT IF EXISTS ck_fiis_price_positive,
    ADD CONSTRAINT ck_fiis_price_positive CHECK (price IS NULL OR price > 0) NOT VALID,
    DROP CONSTRAINT IF EXISTS ck_fiis_pvp_plausible,
    ADD CONSTRAINT ck_fiis_pvp_plausible CHECK (pvp IS NULL OR pvp > 0) NOT VALID,
    DROP CONSTRAINT IF EXISTS ck_fiis_dy_ratio,
    ADD CONSTRAINT ck_fiis_dy_ratio CHECK (dy_12m IS NULL OR dy_12m BETWEEN 0 AND 1) NOT VALID,
    DROP CONSTRAINT IF EXISTS ck_fiis_score_range,
    ADD CONSTRAINT ck_fiis_score_range CHECK (score IS NULL OR score BETWEEN 0 AND 100) NOT VALID,
    DROP CONSTRAINT IF EXISTS ck_fiis_tipo,
    ADD CONSTRAINT ck_fiis_tipo
        CHECK (tipo IS NULL OR tipo IN ('tijolo', 'papel', 'fof', 'hibrido')) NOT VALID;

ALTER TABLE market.fii_metrics_monthly
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    DROP CONSTRAINT IF EXISTS ck_fii_monthly_dy,
    ADD CONSTRAINT ck_fii_monthly_dy
        CHECK (dy_patrimonial_mes IS NULL OR dy_patrimonial_mes BETWEEN 0 AND 0.20) NOT VALID,
    DROP CONSTRAINT IF EXISTS fk_fii_monthly_ticker,
    ADD CONSTRAINT fk_fii_monthly_ticker FOREIGN KEY (ticker)
        REFERENCES market.fiis(ticker) ON DELETE CASCADE NOT VALID;

ALTER TABLE market.fii_imoveis
    DROP CONSTRAINT IF EXISTS ck_fii_imoveis_vacancia,
    ADD CONSTRAINT ck_fii_imoveis_vacancia
        CHECK (vacancia IS NULL OR vacancia BETWEEN 0 AND 1) NOT VALID,
    DROP CONSTRAINT IF EXISTS ck_fii_imoveis_receita,
    ADD CONSTRAINT ck_fii_imoveis_receita
        CHECK (pct_receita IS NULL OR pct_receita BETWEEN 0 AND 1) NOT VALID,
    DROP CONSTRAINT IF EXISTS fk_fii_imoveis_ticker,
    ADD CONSTRAINT fk_fii_imoveis_ticker FOREIGN KEY (ticker)
        REFERENCES market.fiis(ticker) ON DELETE CASCADE NOT VALID;

DROP TRIGGER IF EXISTS trg_fii_metrics_monthly_updated ON market.fii_metrics_monthly;
CREATE TRIGGER trg_fii_metrics_monthly_updated
    BEFORE UPDATE ON market.fii_metrics_monthly
    FOR EACH ROW EXECUTE FUNCTION market.set_updated_at();

CREATE TABLE IF NOT EXISTS public.fii_portfolio_models (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    name         VARCHAR(160) NOT NULL,
    status       VARCHAR(20) NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'archived')),
    source       VARCHAR(80) NOT NULL DEFAULT 'selecao_fiis',
    plan_hash    TEXT NOT NULL,
    params_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    notes        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.fii_portfolio_model_items (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id   UUID NOT NULL REFERENCES public.fii_portfolio_models(id) ON DELETE CASCADE,
    ticker     VARCHAR(16) NOT NULL,
    nome       VARCHAR(200),
    tipo       TEXT,
    segmento   TEXT,
    weight     NUMERIC(12,8) NOT NULL CHECK (weight BETWEEN 0 AND 1),
    dy_12m     NUMERIC(18,8),
    pvp        NUMERIC(18,8),
    score      NUMERIC(18,8),
    meta_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_id, ticker)
);

UPDATE public.fii_portfolio_model_items SET weight = 0 WHERE weight IS NULL;
ALTER TABLE public.fii_portfolio_model_items
    ALTER COLUMN weight SET NOT NULL,
    DROP CONSTRAINT IF EXISTS ck_fii_model_item_weight,
    ADD CONSTRAINT ck_fii_model_item_weight CHECK (weight BETWEEN 0 AND 1) NOT VALID;

-- Corrige eventual legado antes de impor um único modelo ativo por usuário.
WITH duplicated AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY user_id ORDER BY updated_at DESC, created_at DESC, id DESC
    ) AS position
    FROM public.fii_portfolio_models
    WHERE status = 'active'
)
UPDATE public.fii_portfolio_models AS model
SET status = 'archived', updated_at = NOW()
FROM duplicated
WHERE model.id = duplicated.id AND duplicated.position > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_fii_portfolio_models_one_active
    ON public.fii_portfolio_models(user_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_fii_portfolio_models_user_status
    ON public.fii_portfolio_models(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fii_portfolio_items_model_weight
    ON public.fii_portfolio_model_items(model_id, weight DESC);

ALTER TABLE public.fii_portfolio_models ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fii_portfolio_model_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fii_models_owner_all ON public.fii_portfolio_models;
CREATE POLICY fii_models_owner_all ON public.fii_portfolio_models
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS fii_model_items_owner_all ON public.fii_portfolio_model_items;
CREATE POLICY fii_model_items_owner_all ON public.fii_portfolio_model_items
    FOR ALL TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.fii_portfolio_models model
            WHERE model.id = model_id AND model.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.fii_portfolio_models model
            WHERE model.id = model_id AND model.user_id = auth.uid()
        )
    );

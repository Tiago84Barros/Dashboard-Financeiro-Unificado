-- ============================================================
-- 020_b3_portfolio_hardening.sql
-- Invariantes persistidos das carteiras Empresas B3.
-- Idempotente e não destrutivo.
-- ============================================================

ALTER TABLE public.b3_portfolio_model_items
    DROP CONSTRAINT IF EXISTS b3_portfolio_model_items_weight_valid;
ALTER TABLE public.b3_portfolio_model_items
    ADD CONSTRAINT b3_portfolio_model_items_weight_valid
    CHECK (weight IS NOT NULL AND weight >= 0 AND weight <= 1) NOT VALID;

ALTER TABLE public.b3_portfolio_model_items
    VALIDATE CONSTRAINT b3_portfolio_model_items_weight_valid;

ALTER TABLE public.b3_portfolio_model_items
    DROP CONSTRAINT IF EXISTS b3_portfolio_model_items_score_finite;
ALTER TABLE public.b3_portfolio_model_items
    ADD CONSTRAINT b3_portfolio_model_items_score_finite
    CHECK (score IS NULL OR score BETWEEN -1000000 AND 1000000) NOT VALID;

ALTER TABLE public.b3_portfolio_model_items
    VALIDATE CONSTRAINT b3_portfolio_model_items_score_finite;

CREATE INDEX IF NOT EXISTS idx_b3_models_methodology_version
    ON public.b3_portfolio_models
    ((params_json->>'score_version'), created_at DESC);


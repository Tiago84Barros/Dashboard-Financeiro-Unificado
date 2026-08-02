-- Fixture exclusivamente sintética para validação visual local do App4.
CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE market.fii_selection_inputs (
    ticker text PRIMARY KEY,
    payload_json jsonb NOT NULL,
    as_of_date date NOT NULL,
    available_at timestamptz NOT NULL,
    knowledge_at timestamptz NOT NULL,
    reference_date date,
    vintage text NOT NULL,
    source text NOT NULL,
    quality_status text NOT NULL,
    schema_version text NOT NULL,
    generated_at timestamptz NOT NULL,
    payload_sha256 text NOT NULL,
    coverage_json jsonb NOT NULL
);

CREATE TABLE market.fii_validation_runs (
    id bigserial PRIMARY KEY,
    methodology_version text NOT NULL,
    as_of_date date NOT NULL,
    status text NOT NULL,
    metrics_json jsonb NOT NULL,
    blockers_json jsonb NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz
);

CREATE TABLE market.historical_prices (
    ticker text NOT NULL,
    date date NOT NULL,
    close numeric,
    adjusted_close numeric,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE profiles (id uuid PRIMARY KEY);
INSERT INTO profiles VALUES ('11111111-1111-1111-1111-111111111111');

CREATE TABLE fii_portfolio_models (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES profiles(id),
    name text NOT NULL,
    status text NOT NULL,
    source text NOT NULL,
    plan_hash text NOT NULL,
    params_json jsonb NOT NULL,
    metrics_json jsonb NOT NULL,
    notes text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE fii_portfolio_model_items (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id uuid NOT NULL REFERENCES fii_portfolio_models(id),
    ticker text NOT NULL,
    nome text,
    tipo text,
    segmento text,
    weight numeric NOT NULL,
    dy_12m numeric,
    pvp numeric,
    score numeric,
    meta_json jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_id, ticker)
);

WITH synthetic AS (
    SELECT
        number,
        'F' || lpad(number::text, 3, '0') || '11' AS ticker,
        CASE (number - 1) % 4
            WHEN 0 THEN 'tijolo'
            WHEN 1 THEN 'papel'
            WHEN 2 THEN 'fof'
            ELSE 'hibrido'
        END AS fii_type
    FROM generate_series(1, 12) AS number
), payloads AS (
    SELECT
        ticker,
        jsonb_build_object(
            'ticker', ticker,
            'name', 'FII Sintético ' || number,
            'tipo', fii_type,
            'sector', CASE WHEN fii_type IN ('tijolo', 'hibrido')
                           THEN 'Setor sintético ' || number END,
            'manager', 'Gestor sintético ' || number,
            'dy_12m', .10 + number * .0005,
            'income_growth_per_share_3y', .04,
            'income_recurrence', .95,
            'pvp', .94,
            'liquidez_diaria', 3000000 + number * 10000,
            'total_return_trend', .06,
            'max_drawdown', -.18,
            'issuance_discipline', .9,
            'issuance_price_discipline', .9,
            'management_efficiency', .85,
            'fee_efficiency', .8,
            'conflict_alignment', .9,
            'mandate_adherence', .95,
            'cvm_event_quality', .9,
            'related_party_exposure', .02
        ) || jsonb_build_object(
            'governance_disclosure_quality', .95,
            'governance_integrity', .95,
            'auditor_opinion_quality', 1,
            'vacancia_fisica', .05,
            'vacancia_financeira', .04,
            'wault_anos', 5,
            'tenant_concentration', .12,
            'geographic_diversification', .8,
            'implied_cap_rate', .09,
            'asset_quality', .9,
            'contract_quality', .85,
            'lease_expiry_concentration_24m', .15,
            'leverage', .05,
            'duration_anos', 3,
            'indexer_diversification', .7,
            'credit_spread_adequacy', .8,
            'ltv', .45,
            'rating_quality', .85,
            'subordination_protection', .8,
            'delinquency', .01,
            'debtor_diversification', .8,
            'issuance_concentration', .12,
            'issuer_diversification', .8
        ) || jsonb_build_object(
            'nav_discount', .1,
            'double_fee_burden', .02,
            'holdings_overlap', .1,
            'invested_portfolio_liquidity', .8,
            'holdings_quality', .9,
            'underlying_manager_concentration', .15,
            'portfolio_income_recurrence', .9,
            'history_months', 60,
            'data_consistency', 1,
            'parser_calibration', 1,
            'updated_at', '2026-07-29T12:00:00+00:00',
            'cvm_ref_date', '2026-06-30',
            'vacancia_ref_date', '2026-07-20',
            'tenants', CASE WHEN fii_type IN ('tijolo', 'hibrido')
                            THEN jsonb_build_object('Locatário sintético ' || number, 1.0) END,
            'regions', CASE WHEN fii_type IN ('tijolo', 'hibrido')
                            THEN jsonb_build_object(
                                CASE number % 5
                                    WHEN 0 THEN 'AM'
                                    WHEN 1 THEN 'BA'
                                    WHEN 2 THEN 'DF'
                                    WHEN 3 THEN 'SP'
                                    ELSE 'RS'
                                END,
                                1.0
                            ) END,
            'issuers', CASE WHEN fii_type IN ('papel', 'hibrido')
                            THEN jsonb_build_object('Emissor sintético ' || number, 1.0) END,
            'debtors', CASE WHEN fii_type IN ('papel', 'hibrido')
                            THEN jsonb_build_object('Devedor sintético ' || number, 1.0) END,
            'indexers', CASE WHEN fii_type IN ('papel', 'hibrido')
                             THEN jsonb_build_object('IPCA', .7, 'CDI', .3) END,
            'metric_metadata', '{}'::jsonb
        ) AS payload
    FROM synthetic
)
INSERT INTO market.fii_selection_inputs
    (ticker, payload_json, as_of_date, available_at, knowledge_at,
     reference_date, vintage, source, quality_status, schema_version,
     generated_at, payload_sha256, coverage_json)
SELECT
    ticker, payload, DATE '2026-07-29', TIMESTAMPTZ '2026-07-29 12:00:00+00',
    TIMESTAMPTZ '2026-07-29 12:00:00+00', DATE '2026-06-30',
    'synthetic_browser_fixture', 'synthetic_test_only', 'published',
    'fii_selection_inputs.v2', TIMESTAMPTZ '2026-07-29 12:00:00+00',
    md5(payload::text), '{"coverage_pct": 100}'::jsonb
FROM payloads;

INSERT INTO market.fii_validation_runs
    (methodology_version, as_of_date, status, metrics_json, blockers_json,
     started_at, finished_at)
VALUES (
    '6.6.0', DATE '2026-07-29', 'passed',
    '{"strategy_id":"fii_integrated_robust_optimizer.v6.6",
      "backtest":{"strategy_id":"fii_integrated_robust_optimizer.v6.6",
                  "periods":65,"verified_snapshot_fraction":1,
                  "return_observation_coverage":1}}',
    '[]', TIMESTAMPTZ '2026-07-24 10:00:00+00',
    TIMESTAMPTZ '2026-07-24 12:00:00+00'
);

INSERT INTO market.historical_prices (ticker, date, close, adjusted_close)
SELECT
    'F' || lpad(fund::text, 3, '0') || '11',
    (DATE '2024-08-31' + month * INTERVAL '1 month')::date,
    90 + fund + month * (1 + fund * .01),
    90 + fund + month * (1 + fund * .01)
FROM generate_series(1, 12) AS fund
CROSS JOIN generate_series(0, 23) AS month;

INSERT INTO fii_portfolio_models
    (id,user_id,name,status,source,plan_hash,params_json,metrics_json,
     created_at,updated_at)
VALUES
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
     '11111111-1111-1111-1111-111111111111',
     'Carteira sintética ativa','active','synthetic_test','fixture-active',
     '{"methodology_version":"6.6.0",
       "strategy_id":"fii_integrated_robust_optimizer.v6.6"}',
     '{}','2026-07-24 12:00:00+00','2026-07-24 12:00:00+00'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
     '11111111-1111-1111-1111-111111111111',
     'Carteira sintética arquivada','archived','synthetic_test','fixture-archived',
     '{"methodology_version":"6.6.0",
       "strategy_id":"fii_integrated_robust_optimizer.v6.6"}',
     '{}','2026-06-24 12:00:00+00','2026-06-24 12:00:00+00');

INSERT INTO fii_portfolio_model_items
    (model_id,ticker,nome,tipo,segmento,weight,dy_12m,pvp,score)
SELECT
    model_id,
    'F' || lpad(number::text, 3, '0') || '11',
    'FII Sintético ' || number,
    CASE (number - 1) % 4
        WHEN 0 THEN 'tijolo'
        WHEN 1 THEN 'papel'
        WHEN 2 THEN 'fof'
        ELSE 'hibrido'
    END,
    'Segmento sintético',
    .08333333, .10, .94, 80
FROM (
    SELECT 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'::uuid AS model_id,
           generate_series(1, 12) AS number
    UNION ALL
    SELECT 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'::uuid AS model_id,
           generate_series(1, 12) AS number
) AS versions;

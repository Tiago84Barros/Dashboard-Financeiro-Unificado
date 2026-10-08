-- 081_views_investimento_em_reais.sql — v_investment_summary e v_net_worth
-- passam a somar posição em dólar convertida para reais.
--
-- Idempotente (CREATE OR REPLACE VIEW). Rodar à mão no SQL Editor do
-- Supabase, depois da 007 e da 027.
--
-- Por que existe:
--
-- A 007 somava `pp.total_invested` e `quantity * close` de todas as posições
-- como se fossem reais. Uma AAPL de US$ 1.000 entrava no patrimônio como
-- R$ 1.000. E agrupava só por `assets.class`, então ação americana caía em
-- "stock" junto com as da B3 (ou em "etf", pelo cadastro errado da Nomad).
--
-- Regra de câmbio, a mesma de core/investimentos.py e core/financeiro.py:
--   * mercado pelo câmbio de hoje: último USDBRL de asset_quotes, válido se
--     >= 2.0 (abaixo disso é cotação corrompida, não câmbio);
--   * custo pelo câmbio da compra: média das taxas USDBRL das compras
--     (janela de 5 dias para trás, como core/fx_aquisicao.py), ponderada pelo
--     custo, e só quando TODAS as compras do ativo acharam taxa -- cobertura
--     parcial daria um custo que não existiu; sem ela, câmbio de hoje;
--   * sem câmbio de hoje válido, a posição em dólar fica FORA da soma, e
--     v_net_worth conta quantas ficaram em `usd_positions_without_fx`.
--     O app tem o fallback do yfinance; a view não tem como buscá-lo.
--
-- Classe: em dólar, `stock` vira `stock_us` e `etf` vira `etf_intl`, as
-- chaves de exibição do app. A view confia no cadastro (corrigido por
-- scripts/corrige_classe_acoes_eua.py); o classificador por ticker e nome de
-- core/classe_exterior.py só existe em Python.
--
-- Mesmas colunas, na mesma ordem e com os mesmos tipos da 007 -- exigência
-- de CREATE OR REPLACE VIEW. v_net_worth ganha uma coluna no fim.
--
-- `WITH (security_invoker = true)` repete a 027: CREATE OR REPLACE VIEW
-- substitui as opções da view, e sem isto a 027 seria desfeita.
--
-- Desfazer: rodar de novo os blocos das views 5 e 6 da 007. Para isso, a
-- coluna nova de v_net_worth precisa sair antes (DROP VIEW v_net_worth e
-- recriar), porque CREATE OR REPLACE não remove coluna.

CREATE OR REPLACE VIEW v_investment_summary
WITH (security_invoker = true) AS
WITH usd_brl_hoje AS (
    SELECT q.close AS taxa
    FROM asset_quotes q
    JOIN assets fa ON fa.id = q.asset_id
    WHERE fa.ticker = 'USDBRL'
    ORDER BY q.timestamp DESC
    LIMIT 1
),
compras_usd AS (
    SELECT it.user_id,
           upper(a.ticker)               AS ticker,
           it.quantity * it.unit_price   AS custo_usd,
           fx.close                      AS taxa
    FROM investment_transactions it
    JOIN assets a ON a.id = it.asset_id
    LEFT JOIN LATERAL (
        SELECT q.close
        FROM asset_quotes q
        JOIN assets fa ON fa.id = q.asset_id
        WHERE fa.ticker = 'USDBRL'
          AND q.close > 0
          AND q.timestamp::date <= it.transaction_date
          AND q.timestamp::date >= it.transaction_date - 5
        ORDER BY q.timestamp DESC
        LIMIT 1
    ) fx ON TRUE
    WHERE lower(it.type) = 'buy'
      AND upper(coalesce(a.currency, 'BRL')) = 'USD'
      AND it.quantity > 0
      AND it.unit_price > 0
),
usd_brl_compra AS (
    SELECT user_id, ticker,
           sum(custo_usd * taxa) / sum(custo_usd) AS taxa
    FROM compras_usd
    GROUP BY user_id, ticker
    HAVING count(*) = count(taxa)
),
posicoes AS (
    SELECT
        pp.user_id,
        a.id AS asset_id,
        (CASE
            WHEN upper(a.currency) = 'USD' AND a.class = 'stock' THEN 'stock_us'
            WHEN upper(a.currency) = 'USD' AND a.class = 'etf'   THEN 'etf_intl'
            ELSE a.class
        END)::VARCHAR(50) AS asset_class,
        pp.total_invested
            * CASE WHEN upper(a.currency) = 'USD'
                   THEN COALESCE(fc.taxa, fx.taxa) ELSE 1 END AS investido,
        pp.quantity * COALESCE(lq.close, pp.average_price)
            * CASE WHEN upper(a.currency) = 'USD'
                   THEN fx.taxa ELSE 1 END                    AS mercado
    FROM portfolio_positions pp
    JOIN assets a ON a.id = pp.asset_id
    LEFT JOIN LATERAL (
        SELECT close
        FROM asset_quotes aq
        WHERE aq.asset_id = pp.asset_id
        ORDER BY aq.timestamp DESC
        LIMIT 1
    ) lq ON TRUE
    LEFT JOIN usd_brl_hoje fx ON fx.taxa >= 2.0
    LEFT JOIN usd_brl_compra fc
           ON fc.user_id = pp.user_id AND fc.ticker = upper(a.ticker)
    WHERE upper(a.currency) <> 'USD' OR fx.taxa IS NOT NULL
)
SELECT
    user_id,
    asset_class,
    COUNT(DISTINCT asset_id)                        AS asset_count,
    SUM(investido)                                  AS total_invested,
    SUM(mercado)                                    AS current_market_value,
    SUM(mercado) - SUM(investido)                   AS unrealized_pnl,
    ROUND(
        (SUM(mercado) - SUM(investido)) * 100.0 / NULLIF(SUM(investido), 0),
        2
    )                                               AS return_pct
FROM posicoes
GROUP BY user_id, asset_class;

COMMENT ON VIEW v_investment_summary IS
    'Posição consolidada por classe de ativo, em reais. '
    'Dólar: mercado pelo último USDBRL (>= 2.0), custo pelo câmbio médio das '
    'compras (cobertura total) ou, sem ele, pelo de hoje; sem USDBRL válido a '
    'posição fica fora. Em dólar, stock -> stock_us e etf -> etf_intl. '
    'current_market_value usa a cotação mais recente; fallback = average_price.';

CREATE OR REPLACE VIEW v_net_worth
WITH (security_invoker = true) AS
WITH bank AS (
    SELECT
        user_id,
        SUM(current_balance) AS bank_balance
    FROM v_account_balance
    WHERE active = TRUE
      AND account_type != 'credit_card'
    GROUP BY user_id
),
investments AS (
    SELECT
        user_id,
        SUM(current_market_value) AS investment_total
    FROM v_investment_summary
    GROUP BY user_id
),
sem_cambio AS (
    SELECT pp.user_id, COUNT(*) AS n
    FROM portfolio_positions pp
    JOIN assets a ON a.id = pp.asset_id
    WHERE upper(a.currency) = 'USD'
      AND NOT EXISTS (
          SELECT 1
          FROM (
              SELECT q.close
              FROM asset_quotes q
              JOIN assets fa ON fa.id = q.asset_id
              WHERE fa.ticker = 'USDBRL'
              ORDER BY q.timestamp DESC
              LIMIT 1
          ) fx
          WHERE fx.close >= 2.0
      )
    GROUP BY pp.user_id
)
SELECT
    COALESCE(b.user_id, i.user_id, s.user_id) AS user_id,
    COALESCE(b.bank_balance, 0)             AS bank_balance,
    COALESCE(i.investment_total, 0)         AS investment_total,
    COALESCE(b.bank_balance, 0)
        + COALESCE(i.investment_total, 0)   AS net_worth,
    COALESCE(s.n, 0)                        AS usd_positions_without_fx
FROM bank b
FULL OUTER JOIN investments i ON i.user_id = b.user_id
FULL OUTER JOIN sem_cambio s ON s.user_id = COALESCE(b.user_id, i.user_id);

COMMENT ON VIEW v_net_worth IS
    'Patrimônio líquido total: bank_balance (contas ativas) + investment_total '
    '(v_investment_summary, em reais). usd_positions_without_fx conta as '
    'posições em dólar fora da soma por falta de USDBRL válido.';

REVOKE ALL PRIVILEGES ON TABLE v_investment_summary FROM PUBLIC, anon, authenticated;
REVOKE ALL PRIVILEGES ON TABLE v_net_worth FROM PUBLIC, anon, authenticated;

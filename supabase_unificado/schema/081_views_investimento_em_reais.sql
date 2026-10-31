-- 081_views_investimento_em_reais.sql — v_investment_summary e v_net_worth
-- passam a somar posição e conta em dólar convertidas para reais.
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
--   * custo pelo câmbio da compra: média das taxas USDBRL (>= 2.0, janela
--     de 5 dias para trás) das compras do LOTE ATUAL, como
--     core/fx_aquisicao.py. Lote atual = o que veio depois da última venda que
--     zerou a posição; venda parcial tira a mesma fração de cada compra
--     anterior, como o custo médio de positions.py. Só vale quando TODAS as
--     compras do lote acharam taxa -- cobertura parcial daria um custo que não
--     existiu; sem ela, o custo vai pelo câmbio de hoje e o retorno da classe
--     (return_pct e unrealized_pnl) sai NULL, porque seria retorno em dólar
--     com rótulo de real;
--   * sem câmbio de hoje válido, a posição em dólar fica FORA da soma, e
--     v_net_worth conta quantas ficaram em `usd_positions_without_fx`.
--     O app tem o fallback do yfinance; a view não tem como buscá-lo.
--
-- Contas: a 007 somava `current_balance` de toda conta como real; conta em
-- dólar entrava no saldo bancário pelo valor de face (US$ 1.000 = R$ 1.000).
-- Agora conta em USD vai pelo câmbio de hoje (mesma guarda >= 2.0); moeda
-- sem cotação no banco (só existe USDBRL) ou USD sem câmbio válido fica fora
-- da soma, e v_net_worth conta quantas em `accounts_without_fx`. Saldo de conta não tem
-- "câmbio da compra": é dinheiro, vale o de hoje.
--
-- Classe: em dólar, `stock` vira `stock_us` e `etf` vira `etf_intl`, as
-- chaves de exibição do app. A view confia no cadastro (corrigido por
-- scripts/corrige_classe_acoes_eua.py); o classificador por ticker e nome de
-- core/classe_exterior.py só existe em Python.
--
-- Mesmas colunas, na mesma ordem e com os mesmos tipos da 007 -- exigência
-- de CREATE OR REPLACE VIEW. v_net_worth ganha duas colunas no fim.
--
-- `WITH (security_invoker = true)` repete a 027: CREATE OR REPLACE VIEW
-- substitui as opções da view, e sem isto a 027 seria desfeita.
--
-- Desfazer: rodar de novo os blocos das views 5 e 6 da 007. Para isso, as
-- colunas novas de v_net_worth precisam sair antes (DROP VIEW v_net_worth e
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
movs_usd AS (
    -- Compras e vendas em dólar na ordem de positions.py::_compute.
    SELECT it.user_id,
           upper(a.ticker)               AS ticker,
           lower(it.type)                AS tipo,
           it.quantity                   AS qtd,
           it.quantity * it.unit_price   AS custo_usd,
           it.transaction_date           AS data,
           row_number() OVER w           AS ordem,
           sum(CASE WHEN lower(it.type) = 'buy' THEN it.quantity
                    ELSE -it.quantity END)
               OVER (w ROWS UNBOUNDED PRECEDING) AS saldo_bruto
    FROM investment_transactions it
    JOIN assets a ON a.id = it.asset_id
    WHERE lower(it.type) IN ('buy', 'sell')
      AND upper(coalesce(a.currency, 'BRL')) = 'USD'
      AND it.quantity > 0
    WINDOW w AS (PARTITION BY it.user_id, upper(a.ticker)
                 ORDER BY it.transaction_date, it.created_at, it.id)
),
saldo_usd AS (
    -- Quantidade corrente com piso em zero: venda sem cobertura zera, como
    -- em positions.py, em vez de deixar a quantidade negativa.
    SELECT m.*,
           m.saldo_bruto - LEAST(0, min(m.saldo_bruto) OVER (
               PARTITION BY m.user_id, m.ticker ORDER BY m.ordem
               ROWS UNBOUNDED PRECEDING))  AS saldo
    FROM movs_usd m
),
lote_usd AS (
    -- Só o lote atual: o que veio depois da última venda que zerou.
    SELECT s.*,
           sum(CASE WHEN s.tipo = 'sell' THEN ln(s.saldo / (s.saldo + s.qtd))
                    ELSE 0 END) OVER (
               PARTITION BY s.user_id, s.ticker ORDER BY s.ordem
               ROWS UNBOUNDED PRECEDING)   AS ln_retido
    FROM saldo_usd s
    WHERE s.ordem > COALESCE((
        SELECT max(z.ordem) FROM saldo_usd z
        WHERE z.user_id = s.user_id AND z.ticker = s.ticker
          AND z.tipo = 'sell' AND z.saldo <= 0.0001), 0)
),
compras_usd AS (
    -- Venda parcial tira a mesma fração de tudo o que havia: o peso de uma
    -- compra é seu custo vezes a fração retida pelas vendas posteriores,
    -- exp(ln_retido final - ln_retido da compra).
    SELECT l.user_id,
           l.ticker,
           l.custo_usd * exp(
               last_value(l.ln_retido) OVER (
                     PARTITION BY l.user_id, l.ticker ORDER BY l.ordem
                     ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
               - l.ln_retido)            AS peso,
           l.tipo,
           fx.close                      AS taxa
    FROM lote_usd l
    LEFT JOIN LATERAL (
        SELECT q.close
        FROM asset_quotes q
        JOIN assets fa ON fa.id = q.asset_id
        WHERE l.tipo = 'buy'
          AND fa.ticker = 'USDBRL'
          AND q.close >= 2.0
          AND q.timestamp::date <= l.data
          AND q.timestamp::date >= l.data - 5
        ORDER BY q.timestamp DESC
        LIMIT 1
    ) fx ON TRUE
),
usd_brl_compra AS (
    SELECT user_id, ticker,
           sum(peso * taxa) / NULLIF(sum(peso), 0) AS taxa
    FROM compras_usd
    WHERE tipo = 'buy' AND peso > 0
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
                   THEN fx.taxa ELSE 1 END                    AS mercado,
        (upper(a.currency) = 'USD' AND fc.taxa IS NULL)       AS custo_estimado
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
    CASE WHEN NOT bool_or(custo_estimado)
         THEN SUM(mercado) - SUM(investido) END     AS unrealized_pnl,
    CASE WHEN NOT bool_or(custo_estimado) THEN ROUND(
        (SUM(mercado) - SUM(investido)) * 100.0 / NULLIF(SUM(investido), 0),
        2
    ) END                                           AS return_pct
FROM posicoes
GROUP BY user_id, asset_class;

COMMENT ON VIEW v_investment_summary IS
    'Posição consolidada por classe de ativo, em reais. '
    'Dólar: mercado pelo último USDBRL (>= 2.0), custo pelo câmbio médio das '
    'compras do lote atual (cobertura total) ou, sem ele, pelo de hoje -- e '
    'então unrealized_pnl e return_pct da classe saem NULL; sem USDBRL válido '
    'a posição fica fora. Em dólar, stock -> stock_us e etf -> etf_intl. '
    'current_market_value usa a cotação mais recente; fallback = average_price.';

CREATE OR REPLACE VIEW v_net_worth
WITH (security_invoker = true) AS
WITH usd_brl_hoje AS (
    SELECT q.close AS taxa
    FROM asset_quotes q
    JOIN assets fa ON fa.id = q.asset_id
    WHERE fa.ticker = 'USDBRL'
    ORDER BY q.timestamp DESC
    LIMIT 1
),
contas AS (
    SELECT
        ab.user_id,
        ab.current_balance,
        CASE upper(coalesce(ab.currency, 'BRL'))
            WHEN 'BRL' THEN 1
            WHEN 'USD' THEN fx.taxa
        END AS taxa
    FROM v_account_balance ab
    LEFT JOIN usd_brl_hoje fx ON fx.taxa >= 2.0
    WHERE ab.active = TRUE
      AND ab.account_type != 'credit_card'
),
bank AS (
    SELECT
        user_id,
        SUM(current_balance * taxa)   AS bank_balance,
        COUNT(*) - COUNT(taxa)        AS sem_cambio
    FROM contas
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
      AND NOT EXISTS (SELECT 1 FROM usd_brl_hoje WHERE taxa >= 2.0)
    GROUP BY pp.user_id
)
SELECT
    COALESCE(b.user_id, i.user_id, s.user_id) AS user_id,
    COALESCE(b.bank_balance, 0)             AS bank_balance,
    COALESCE(i.investment_total, 0)         AS investment_total,
    COALESCE(b.bank_balance, 0)
        + COALESCE(i.investment_total, 0)   AS net_worth,
    COALESCE(s.n, 0)                        AS usd_positions_without_fx,
    COALESCE(b.sem_cambio, 0)               AS accounts_without_fx
FROM bank b
FULL OUTER JOIN investments i ON i.user_id = b.user_id
FULL OUTER JOIN sem_cambio s ON s.user_id = COALESCE(b.user_id, i.user_id);

COMMENT ON VIEW v_net_worth IS
    'Patrimônio líquido total, em reais: bank_balance (contas ativas, exceto '
    'cartão; conta em dólar pelo último USDBRL >= 2.0) + investment_total '
    '(v_investment_summary). usd_positions_without_fx e accounts_without_fx '
    'contam posições e contas fora da soma por falta de câmbio válido.';

REVOKE ALL PRIVILEGES ON TABLE v_investment_summary FROM PUBLIC, anon, authenticated;
REVOKE ALL PRIVILEGES ON TABLE v_net_worth FROM PUBLIC, anon, authenticated;

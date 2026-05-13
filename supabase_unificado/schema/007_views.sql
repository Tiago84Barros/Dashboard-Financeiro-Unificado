-- ============================================================
-- 007_views.sql
-- Views analíticas do Dashboard Financeiro Unificado
-- Banco: Dashboard Financeiro Unificado (Supabase — schema public)
-- Fase 4.3 — Scripts SQL não destrutivos
-- Criado: 2026-05-13
--
-- SEGURANÇA:
--   Não contém DROP TABLE, TRUNCATE, DELETE, DROP SCHEMA.
--   CREATE OR REPLACE VIEW é idempotente (recria sem perder dados).
--   Nenhuma credencial ou dado sensível hardcoded.
--
-- VIEWS (6):
--   v_account_balance        — saldo atual de cada conta por usuário
--   v_monthly_cashflow       — receitas, despesas e saldo líquido por mês
--   v_category_spending_mtd  — gastos por categoria no mês corrente
--   v_budget_usage_mtd       — orçamento planejado vs realizado no mês corrente
--   v_investment_summary     — posição consolidada por classe de ativo
--   v_net_worth              — patrimônio líquido total (contas + investimentos)
--
-- FILTRO DE DADOS:
--   Todas as views expõem user_id — o backend filtra WHERE user_id = :owner_id.
--   RLS (006_rls_policies.sql) adiciona camada extra para conexões via Supabase API.
--
-- DEPENDÊNCIAS: 001 a 006 (tabelas + índices devem existir)
-- PRÓXIMO ARQUIVO: 008_seed_reference_data.sql
-- ============================================================

-- ────────────────────────────────────────────────────────────
-- VIEW 1: v_account_balance
-- Saldo atual por conta.
-- saldo_atual = initial_balance + SUM(transactions.amount)
--              filtrando apenas transações com status 'settled'.
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_account_balance AS
SELECT
    a.user_id,
    a.id                                            AS account_id,
    a.name                                          AS account_name,
    a.type                                          AS account_type,
    a.currency,
    a.active,
    a.initial_balance,
    COALESCE(tx.settled_sum, 0)                     AS settled_transactions_sum,
    a.initial_balance + COALESCE(tx.settled_sum, 0) AS current_balance
FROM accounts a
LEFT JOIN (
    SELECT
        account_id,
        SUM(amount) AS settled_sum
    FROM transactions
    WHERE status = 'settled'
    GROUP BY account_id
) tx ON tx.account_id = a.id;

COMMENT ON VIEW v_account_balance IS
    'Saldo atual por conta: initial_balance + SUM(settled transactions). '
    'Filtrar sempre por user_id no código da aplicação.';

-- ────────────────────────────────────────────────────────────
-- VIEW 2: v_monthly_cashflow
-- Receitas, despesas e saldo líquido por mês e por usuário.
-- Inclui apenas transações "settled" (liquidadas).
-- Mês definido pelo campo due_date da transação.
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_monthly_cashflow AS
SELECT
    user_id,
    DATE_TRUNC('month', due_date)::DATE             AS month_year,
    SUM(CASE WHEN amount > 0 THEN amount  ELSE 0 END) AS total_income,
    SUM(CASE WHEN amount < 0 THEN amount  ELSE 0 END) AS total_expenses,
    ABS(SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)) AS total_expenses_abs,
    SUM(amount)                                     AS net_cashflow
FROM transactions
WHERE status = 'settled'
  AND type   IN ('income', 'expense')
GROUP BY user_id, DATE_TRUNC('month', due_date)::DATE;

COMMENT ON VIEW v_monthly_cashflow IS
    'Receitas, despesas e saldo líquido por mês (due_date). '
    'Apenas transações settled. total_expenses é negativo; total_expenses_abs é positivo.';

-- ────────────────────────────────────────────────────────────
-- VIEW 3: v_category_spending_mtd
-- Gastos por categoria no mês corrente (month-to-date).
-- Apenas despesas (amount < 0) com status settled.
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_category_spending_mtd AS
SELECT
    t.user_id,
    c.id                                            AS category_id,
    c.name                                          AS category_name,
    c.icon                                          AS category_icon,
    c.color                                         AS category_color,
    ABS(SUM(t.amount))                              AS total_spent,
    COUNT(*)                                        AS transaction_count
FROM transactions t
JOIN categories c ON c.id = t.category_id
WHERE t.status  = 'settled'
  AND t.amount  < 0
  AND t.type    = 'expense'
  AND DATE_TRUNC('month', t.due_date) = DATE_TRUNC('month', CURRENT_DATE)
GROUP BY t.user_id, c.id, c.name, c.icon, c.color;

COMMENT ON VIEW v_category_spending_mtd IS
    'Gastos (despesas settled) por categoria no mês corrente. '
    'total_spent é positivo (ABS). Transações sem categoria são excluídas.';

-- ────────────────────────────────────────────────────────────
-- VIEW 4: v_budget_usage_mtd
-- Orçamento planejado vs realizado no mês corrente.
-- Junta budgets com v_category_spending_mtd pelo mês vigente.
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_budget_usage_mtd AS
SELECT
    b.user_id,
    b.id                                                        AS budget_id,
    b.category_id,
    c.name                                                      AS category_name,
    c.icon                                                      AS category_icon,
    b.amount_limit,
    COALESCE(sp.total_spent, 0)                                 AS amount_spent,
    b.amount_limit - COALESCE(sp.total_spent, 0)               AS amount_remaining,
    ROUND(
        COALESCE(sp.total_spent, 0) * 100.0 / NULLIF(b.amount_limit, 0),
        2
    )                                                           AS usage_pct
FROM budgets b
JOIN categories c ON c.id = b.category_id
LEFT JOIN (
    SELECT
        t.user_id,
        t.category_id,
        ABS(SUM(t.amount)) AS total_spent
    FROM transactions t
    WHERE t.status = 'settled'
      AND t.amount  < 0
      AND t.type    = 'expense'
      AND DATE_TRUNC('month', t.due_date) = DATE_TRUNC('month', CURRENT_DATE)
    GROUP BY t.user_id, t.category_id
) sp ON sp.user_id = b.user_id AND sp.category_id = b.category_id
WHERE b.month_year = DATE_TRUNC('month', CURRENT_DATE)::DATE;

COMMENT ON VIEW v_budget_usage_mtd IS
    'Orçamento planejado vs realizado no mês corrente. '
    'usage_pct = (amount_spent / amount_limit) * 100. '
    'amount_remaining negativo = orçamento estourado.';

-- ────────────────────────────────────────────────────────────
-- VIEW 5: v_investment_summary
-- Posição consolidada por usuário e classe de ativo.
-- Usa portfolio_positions (tabela de posição calculada pelo ETL).
-- Inclui cotação mais recente via subquery lateral.
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_investment_summary AS
SELECT
    pp.user_id,
    a.class                                         AS asset_class,
    COUNT(DISTINCT a.id)                            AS asset_count,
    SUM(pp.total_invested)                          AS total_invested,
    SUM(pp.quantity * COALESCE(lq.close, pp.average_price)) AS current_market_value,
    SUM(pp.quantity * COALESCE(lq.close, pp.average_price))
        - SUM(pp.total_invested)                    AS unrealized_pnl,
    ROUND(
        (SUM(pp.quantity * COALESCE(lq.close, pp.average_price))
            - SUM(pp.total_invested))
        * 100.0
        / NULLIF(SUM(pp.total_invested), 0),
        2
    )                                               AS return_pct
FROM portfolio_positions pp
JOIN assets a ON a.id = pp.asset_id
LEFT JOIN LATERAL (
    SELECT close
    FROM asset_quotes aq
    WHERE aq.asset_id = pp.asset_id
    ORDER BY aq.timestamp DESC
    LIMIT 1
) lq ON TRUE
GROUP BY pp.user_id, a.class;

COMMENT ON VIEW v_investment_summary IS
    'Posição consolidada por classe de ativo. '
    'current_market_value usa a cotação mais recente (LATERAL); '
    'fallback = average_price quando não há cotação. '
    'return_pct = ((current - invested) / invested) * 100.';

-- ────────────────────────────────────────────────────────────
-- VIEW 6: v_net_worth
-- Patrimônio líquido total por usuário.
-- bank_balance    = SUM de current_balance de contas ativas
-- investment_total = SUM de current_market_value de todas as classes
-- net_worth       = bank_balance + investment_total
-- ────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW v_net_worth AS
WITH bank AS (
    SELECT
        user_id,
        SUM(current_balance) AS bank_balance
    FROM v_account_balance
    WHERE active = TRUE
    GROUP BY user_id
),
investments AS (
    SELECT
        user_id,
        SUM(current_market_value) AS investment_total
    FROM v_investment_summary
    GROUP BY user_id
)
SELECT
    COALESCE(b.user_id, i.user_id)          AS user_id,
    COALESCE(b.bank_balance, 0)             AS bank_balance,
    COALESCE(i.investment_total, 0)         AS investment_total,
    COALESCE(b.bank_balance, 0)
        + COALESCE(i.investment_total, 0)   AS net_worth
FROM bank b
FULL OUTER JOIN investments i ON i.user_id = b.user_id;

COMMENT ON VIEW v_net_worth IS
    'Patrimônio líquido total: bank_balance (contas ativas) + investment_total. '
    'Usa FULL OUTER JOIN para incluir usuários com apenas contas ou apenas investimentos.';

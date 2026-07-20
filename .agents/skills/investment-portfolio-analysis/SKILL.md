---
name: investment-portfolio-analysis
description: Analyze portfolio allocation, returns, income, costs, taxes, liquidity, maturity, concentration, volatility, drawdown, correlation, diversification, and benchmark fit. Use for portfolio diagnostics, investment-policy checks, or human-reviewed rebalancing analysis.
---

# Investment Portfolio Analysis

## Workflow

1. Establish valuation date, price source, currency, ownership scope, and cash-flow history.
2. Reconcile quantity, average cost, invested value, current value, income, costs, and taxes.
3. Analyze allocation by asset, class, institution, sector, country, and currency.
4. Assess liquidity, maturity, credit, market, concentration, and currency risks.
5. Calculate returns and risk metrics only when sampling and history are sufficient.
6. Select a coherent benchmark for each class, strategy, risk, currency, and horizon.
7. Compare current allocation with the approved policy and express rebalancing as a human-reviewed scenario.

## Guardrails

- Do not compare different periods directly.
- Do not treat past performance as a guarantee.
- Do not recommend an asset solely because of recent appreciation.
- State absent or stale prices and never substitute them with zero.
- Prefer allocation, risk, objectives, liquidity, cost, tax, and consistency over return chasing.
- Require human review for every buy, sell, or rebalance suggestion.

## Reference

Read [references/portfolio-criteria.md](references/portfolio-criteria.md) for benchmark mapping, sufficiency rules, risk interpretation, and rebalancing criteria.

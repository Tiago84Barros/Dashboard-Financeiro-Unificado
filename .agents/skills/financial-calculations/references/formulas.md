# Financial formulas and criteria

Use decimal rates and chronological dates. Return an explicit undefined/insufficient-data result for invalid domains.

## Returns

- Simple return: `R = (V1 - V0 + income) / V0`, with `V0 > 0`.
- Cumulative return: `R_cum = product(1 + r_t) - 1`.
- Annualized return: `(1 + R_period) ** (year_days / elapsed_days) - 1`; do not annualize zero/very short periods without disclosure.
- Real return: `(1 + R_nominal) / (1 + inflation) - 1`, where inflation must cover the same dates.
- CAGR: `(ending / beginning) ** (1 / years) - 1`, with positive beginning/ending values and `years > 0`.
- XIRR: solve `sum(CF_i / (1+r) ** ((date_i-date_0).days/365)) = 0`. Require cash flows with at least one positive and one negative value; use a documented numerical solver and failure behavior.

## Personal finance

- Savings rate: `(income - expenses) / income`.
- Investment rate: `net_contributions / income`.
- Net worth: `sum(assets) - sum(liabilities)` at one date.
- Reserve coverage (months): `eligible_liquid_reserve / average_monthly_essential_expenses`.
- Net-worth evolution: `NW_end - NW_start`; percentage evolution is `(NW_end/NW_start)-1` only when the denominator permits meaningful interpretation.

Define income, expense, eligible reserve, contribution, transfer, and liability scope in each implementation.

## Risk and allocation

- Period volatility: sample standard deviation of periodic returns (`ddof=1`). Annualized volatility: `vol_period * sqrt(periods_per_year)` only for a coherent frequency.
- Drawdown at `t`: `wealth_t / running_max_t - 1`; maximum drawdown is the minimum drawdown.
- Sharpe: `(annualized_return - annualized_risk_free_return) / annualized_volatility`; use matching frequency and convention.
- Asset concentration: weight `w_i = value_i / total_value`; also report largest weight. Optional HHI: `sum(w_i**2)` with method labeled.
- Allocation deviation: `current_weight - target_weight`; rebalance band breach occurs only outside approved minimum/maximum limits.

## Projections

- Lump sum future value: `FV = PV * (1+r)^n`.
- End-of-period contribution future value: `FV = PV*(1+r)^n + PMT*((1+r)^n - 1)/r`; for `r=0`, use `FV = PV + PMT*n`.
- Required end-of-period contribution: `PMT = (target - PV*(1+r)^n) * r / ((1+r)^n - 1)`; handle `r=0`, negative gap, costs, taxes, and contribution timing explicitly.

## Validation requirements

Align period, currency, valuation date, cash-flow sign, and compounding. Detect duplicates before aggregation. Keep raw precision, round only in presentation, and regression-test every formula change.

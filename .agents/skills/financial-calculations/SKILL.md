---
name: financial-calculations
description: Implement, review, and validate deterministic personal-finance and portfolio calculations in Python. Use when adding or changing formulas, projections, returns, cash-flow metrics, risk statistics, allocation calculations, or financial regression tests.
---

# Financial Calculations

## Required method

1. Define inputs, output, units, sign convention, period, and missing-data behavior.
2. Implement the formula in Python outside Streamlit components and LLM prompts.
3. Normalize dates and align comparable periods before calculating.
4. Preserve full precision internally and round only for presentation.
5. Test normal, boundary, missing, negative, duplicate, and incompatible-period cases.
6. Add a regression test whenever a formula changes.

## Invariants

- Keep monetary values and decimal rates distinct; `0.12` means 12%.
- Do not convert missing observations to zero unless the business meaning explicitly defines zero.
- Treat negative values according to the metric's sign convention.
- Detect duplicate transactions before aggregation.
- Respect contributions and withdrawals when measuring investment performance; prefer money-weighted return for investor experience and time-weighted return for manager performance.
- Compare returns and benchmarks over the same dates, currency, frequency, and compounding convention.
- Raise or return an explicit insufficient-data result when a calculation is undefined.

## Reference

Read [references/formulas.md](references/formulas.md) before implementing or changing any covered formula. Document deviations in code and tests.

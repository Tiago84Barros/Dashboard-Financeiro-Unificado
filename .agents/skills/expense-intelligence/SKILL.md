---
name: expense-intelligence
description: Normalize expenses and detect recurring charges, subscriptions, duplicates, installments, small frequent purchases, category increases, and other anomalies. Use for spending diagnostics, waste reviews, savings opportunities, and expense recommendation logic.
---

# Expense Intelligence

## Workflow

1. Preserve raw description and create a separate normalized merchant/description field.
2. Normalize accents, case, whitespace, installment suffixes, and known payment prefixes conservatively.
3. Compare like-for-like periods and account for seasonality, refunds, transfers, and one-off purchases.
4. Generate candidates for recurrence, subscription, duplication, installment accumulation, frequency, and category anomaly.
5. Calculate value involved, historical baseline, monthly and annualized impact, and possible savings range.
6. Assign priority and confidence from evidence strength; list likely false-positive causes.
7. Request human confirmation before changing classification or recommending cancellation.

## Guardrails

- Never classify an expense as unnecessary merely because it is not essential.
- Treat same-day/same-value transactions as duplicate candidates, not confirmed duplicates.
- Do not annualize irregular expenses without labeling the assumption.
- Do not infer service usage from payment data alone.
- Keep observed facts separate from inferred intent and recommended action.

## Reference

Read [references/detection-criteria.md](references/detection-criteria.md) when implementing thresholds, confidence, anomaly detection, or false-positive controls.

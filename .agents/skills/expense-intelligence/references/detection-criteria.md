# Expense detection criteria

Treat thresholds as configurable and calibrated on synthetic or masked historical data.

| Candidate | Minimum evidence | Common false positives |
|---|---|---|
| Duplicate | Same normalized merchant, amount, currency, and close timestamp | Split bills, repeated transit fares, family purchases |
| Recurrence | Similar merchant/amount at regular intervals across at least three cycles | Installments, seasonal fees, reimbursements |
| Subscription | Recurrence plus service-like merchant; usage remains unknown | Membership paid for another person, annual plan |
| Installment load | Parsed installment marker or documented schedule | Merchant names containing numbers |
| Category spike | Comparable-period value materially above robust historical baseline | Annual taxes, travel, medical event, category recoding |
| Small frequent spend | Low individual amount, high frequency, material aggregate | Necessary transport or meals |

Use robust baselines such as median and median absolute deviation when history permits. For short histories, prefer descriptive comparisons and low confidence over statistical labels.

Confidence should reflect record count, description quality, timing regularity, category stability, and exception signals. Report possible savings as a range or confirmed avoidable amount, never as certainty.

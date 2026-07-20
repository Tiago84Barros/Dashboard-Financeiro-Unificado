# Financial app test matrix

| Change | Minimum automated coverage | Additional verification |
|---|---|---|
| Formula | Unit, boundary, missing, negative, regression | Independent worked example |
| Classification/anomaly | Normal, duplicate, false positive, sparse history | Explain confidence |
| Repository/query | Parameter binding, ownership isolation, permission failure | Read-only database smoke test |
| Migration | Forward, idempotency, rollback, partial failure | Disposable backup/restore |
| External API | Success, timeout, malformed, stale cache, last-good value | No zero substitution |
| LLM | Valid schema, invalid JSON, invented-number rejection, disabled mode | Prompt/model/version audit |
| Streamlit view | Import/startup, empty/error state, state transitions | Real-browser responsive test |

Run the narrowest reliable suite first. Record every skipped check as pending with reason and risk.

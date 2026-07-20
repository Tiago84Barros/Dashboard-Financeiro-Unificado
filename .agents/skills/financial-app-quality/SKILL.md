---
name: financial-app-quality
description: Design and run risk-based unit, integration, regression, migration, security, API-failure, LLM-validation, and Streamlit startup tests. Use when implementing, reviewing, or releasing financial application changes.
---

# Financial App Quality

## Required verification

1. Map each changed requirement to at least one test or explicit manual check.
2. Add unit tests for formulas and classification logic.
3. Add integration tests for repositories, migrations, APIs, and structured LLM responses using fakes or synthetic data.
4. Cover missing data, negative values, duplicates, invalid dates, API timeouts, stale data, malformed LLM JSON, and permission failures.
5. Test migrations forward and rollback against disposable data.
6. Verify Streamlit imports and starts before browser validation.
7. Run targeted tests first, then the relevant regression suite, lint, formatting, and static checks configured by the repository.
8. Record exact commands, outcomes, skipped checks, and reasons.

## Quality gates

- Never use production financial data in automated tests.
- Do not weaken assertions or suppress failures to obtain a green result.
- Treat flaky network tests as design problems; isolate external services.
- A skipped browser or database test is a documented pending action, not a pass.

## Reference

Read [references/test-matrix.md](references/test-matrix.md) to select coverage proportional to the change.

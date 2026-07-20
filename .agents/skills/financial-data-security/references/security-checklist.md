# Financial data security checklist

- [ ] Scope and data owner identified.
- [ ] Least-privilege permissions documented.
- [ ] New external access starts read-only.
- [ ] Credentials remain in environment variables or Streamlit secrets.
- [ ] Logs and errors redact URLs, tokens, documents, accounts, and identifiers.
- [ ] SQL uses bound parameters and allowlisted identifiers.
- [ ] File and form inputs are validated and size-limited.
- [ ] LLM payload contains only necessary, minimized data.
- [ ] Synthetic data is used in tests, screenshots, and examples.
- [ ] Backup is created and restoration verified before migration.
- [ ] Forward and rollback migration paths are tested on disposable data.
- [ ] Destructive actions require explicit human approval.
- [ ] Audit records avoid sensitive payloads.
- [ ] No money movement, card operation, or investment order is possible.
- [ ] Secret scan, permission test, and relevant regression tests pass.

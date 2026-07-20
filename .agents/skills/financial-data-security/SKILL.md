---
name: financial-data-security
description: Review and implement security controls for financial data, databases, logs, migrations, LLM payloads, credentials, and external integrations. Use for any change touching personal financial records, Supabase/PostgreSQL, authentication, secrets, imports, exports, or AI services.
---

# Financial Data Security

## Required controls

1. Apply least privilege and start every external integration in read-only mode.
2. Keep credentials in environment variables or Streamlit secrets; never commit or log them.
3. Minimize and mask documents, account numbers, user identifiers, and financial payloads.
4. Use parameterized SQL, validate inputs, constrain file types/sizes, and reject unsafe paths.
5. Create a verified backup and rollback plan before schema or data migration.
6. Keep migrations reversible and require explicit approval for destructive or irreversible actions.
7. Record audit events without sensitive values.
8. Minimize data sent to an LLM and keep deterministic calculations local.

## Prohibited actions

- Never move money, operate cards, or execute investment orders.
- Never silently modify sensitive records.
- Never grant delete, schema-change, organization-wide, or all-repository permissions by default.
- Never display connection strings, tokens, passwords, private keys, or complete financial data in output.

## Reference

Read and complete [references/security-checklist.md](references/security-checklist.md) before merging a sensitive change.

---
name: streamlit-browser-validation
description: Validate the local App4 Streamlit interface in a real browser using the official Codex Browser skill and its Playwright API. Use for startup, navigation, filters, forms, responsive layouts, empty/error states, console errors, and visual evidence.
---

# Streamlit Browser Validation

## Prerequisite

Read and follow the official `browser:control-in-app-browser` Skill before browser work. Use its in-skill Playwright API; do not install standalone Playwright while the official capability is available.

## Workflow

1. Start Streamlit locally with test mode and synthetic data; bind to loopback only.
2. Open the local URL in the official browser tooling.
3. Verify initial loading, authentication/test gate, every changed route, filters, forms, and navigation.
4. Test desktop and narrow viewport layouts, keyboard access, loading, empty, stale, and error states.
5. Inspect visible errors and browser console output.
6. Capture screenshots only when necessary and only with synthetic or masked data.
7. Record URL, viewport, scenario, result, and evidence path; shut down the test server.

## Privacy and safety

- Never open bank or brokerage accounts, enter credentials, or capture real financial data.
- Never use a production database when synthetic data is sufficient.
- Do not claim validation passed if the server, browser, route, or scenario was unavailable.

## Reference

Read [references/browser-test-checklist.md](references/browser-test-checklist.md) before the validation run.

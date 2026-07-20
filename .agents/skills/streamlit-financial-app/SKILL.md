---
name: streamlit-financial-app
description: Build and maintain modular Streamlit financial workflows while preserving existing navigation, design, data access, and session behavior. Use for financial pages, components, filters, forms, charts, caching, loading states, and error handling in this App4 project.
---

# Streamlit Financial App

## Architecture

- Preserve `app.py` as the routing and composition entry point.
- Keep visual components in `views/` or `design/`, deterministic logic in `core/`, persistence behind repositories/services, and ingestion in `etl/` or `data_pipeline/`.
- Reuse `core.database`, authentication, theme, and existing components rather than creating parallel infrastructure.
- Do not run complex calculations, raw SQL, or LLM calls in visual components.

## UI workflow

1. Map the existing route, state keys, cache boundaries, and data source.
2. Add localized components consistent with the existing visual system.
3. Use stable, namespaced `session_state` keys and explicit defaults.
4. Cache pure/read-only data by inputs and TTL; never cache credentials or mutable user-specific results globally.
5. Show loading, empty, stale-data, insufficient-data, and recoverable-error states.
6. Keep filters consistent and give every indicator and chart a financial question, unit, period, and explanation.
7. Validate keyboard use, contrast, responsive layout, and narrow viewport behavior.

## Reference

Read [references/streamlit-checklist.md](references/streamlit-checklist.md) before changing navigation, caching, state, or financial components.

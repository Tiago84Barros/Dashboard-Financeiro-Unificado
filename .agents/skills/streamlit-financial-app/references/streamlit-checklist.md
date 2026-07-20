# Streamlit implementation checklist

- Preserve the existing `app.py` route map, authentication, theme, and component conventions.
- Keep data reads centralized and parameterized; do not duplicate connections or queries.
- Name `session_state` keys by feature and initialize them deterministically.
- Cache only safe, read-only functions with explicit inputs and suitable TTL.
- Invalidate cache after approved writes; never cache secrets.
- Provide loading, empty, stale, partial, and error states.
- Keep units, dates, sources, update timestamps, and assumptions visible.
- Use charts only when they answer a specific financial question.
- Validate narrow screens, keyboard navigation, labels, contrast, and tooltips.
- Ensure calculations and useful indicators remain available when AI is disabled.

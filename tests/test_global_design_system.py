from pathlib import Path

from design import componentes

ROOT = Path(__file__).resolve().parents[1]


def _capture_markdown(monkeypatch):
    rendered: list[str] = []

    def fake_markdown(body, **_kwargs):
        rendered.append(body)

    monkeypatch.setattr(componentes.st, "markdown", fake_markdown)
    return rendered


def test_global_theme_has_responsive_fintech_primitives():
    css = (ROOT / "design" / "tema.py").read_text(encoding="utf-8")

    assert ".app-page-hero" in css
    assert ".app-brand" in css
    assert ".app-kpi-card" in css
    assert '.stTabs [data-baseweb="tab-list"]' in css
    assert '[data-testid="stVerticalBlockBorderWrapper"]' in css
    assert "@media (max-width: 760px)" in css
    assert "prefers-reduced-motion" in css


def test_shared_page_header_escapes_dynamic_content(monkeypatch):
    rendered = _capture_markdown(monkeypatch)

    componentes.container_pagina(
        "Página <script>alert(1)</script>",
        "Descrição <b>externa</b>",
        "📊",
        metadados=[("Fonte", "Mock <img src=x>")],
    )

    html = "\n".join(rendered)
    assert "app-page-hero" in html
    assert "Página &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Descrição &lt;b&gt;externa&lt;/b&gt;" in html
    assert "Mock &lt;img src=x&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_all_primary_routes_use_global_or_dashboard_header():
    expected = {
        "controle_financeiro.py": "container_pagina(",
        "investimentos.py": "container_pagina(",
        "empresas_b3.py": "container_pagina(",
        "empresas_americanas.py": "container_pagina(",
        "fiis.py": "container_pagina(",
        "documentacao.py": "container_pagina(",
        "configuracoes.py": "container_pagina(",
        "dashboard_geral.py": "_render_dashboard_header(",
    }

    for filename, marker in expected.items():
        source = (ROOT / "views" / filename).read_text(encoding="utf-8")
        assert marker in source, f"{filename} não usa o cabeçalho moderno"

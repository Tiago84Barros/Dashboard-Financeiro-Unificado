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


def test_card_metrica_aceita_numero_sem_quebrar():
    """Regressão de produção (31/07/2026): a aba Empresas B3 caiu inteira com
    *'int' object has no attribute 'replace'*.

    A assinatura pedia `str`, mas 19 chamadas em quatro telas passam `int(...)`
    ou `len(...)` — contagem é o caso natural de um KPI. Enquanto a renderização
    era f-string isso funcionava por acidente; ao passar a escapar HTML,
    `html.escape` chamou `.replace` num inteiro.
    """
    from unittest.mock import patch

    from design.componentes import card_metrica

    with patch("design.componentes.st") as fake_st:
        card_metrica("Tickers", 426)                       # int, o caso real
        card_metrica("Peso", 0.4, delta=12)                # float e delta numérico
        card_metrica("Total", 7, ajuda=3)                  # ajuda numérica
        assert fake_st.markdown.call_count == 3
        html_int = fake_st.markdown.call_args_list[0][0][0]
        assert ">426<" in html_int


def test_card_metrica_escapa_html_do_valor():
    """A conversão para str não pode desligar o escape."""
    from unittest.mock import patch

    from design.componentes import card_metrica

    with patch("design.componentes.st") as fake_st:
        card_metrica("T", "<script>alert(1)</script>")
        html = fake_st.markdown.call_args_list[0][0][0]
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


def test_card_metrica_omite_delta_zero_string_vazia():
    """Delta ausente não deve virar um bloco vazio na tela."""
    from unittest.mock import patch

    from design.componentes import card_metrica

    with patch("design.componentes.st") as fake_st:
        card_metrica("T", "1", delta=None)
        card_metrica("T", "1", delta="")
        for chamada in fake_st.markdown.call_args_list:
            assert "app-kpi-delta" not in chamada[0][0]

import inspect
from pathlib import Path

from views import bank_statement_upload, configuracoes, credit_card_invoice_upload

ROOT = Path(__file__).resolve().parents[1]


def _capture_markdown(monkeypatch):
    rendered: list[str] = []

    def fake_markdown(body, **_kwargs):
        rendered.append(body)

    monkeypatch.setattr(configuracoes.st, "markdown", fake_markdown)
    return rendered


def test_configuracoes_has_responsive_professional_layout():
    source = (ROOT / "views" / "configuracoes.py").read_text(encoding="utf-8")

    # Os três cards "Importar / Atualizar / Proteger" saíram em 17/09/2026:
    # diziam em prosa o que as abas logo abaixo já dizem como navegação, e
    # empurravam o conteúdo real para fora da primeira tela.
    assert ".cfg-overview" not in source
    assert ".cfg-tab-intro" in source
    # Sub-abas em pílula: duas fileiras de abas idênticas empilhadas foi o
    # que se leu como tela carregada. A diferença é visual, não de posição.
    assert '.stTabs .stTabs [data-baseweb="tab-list"]' in source
    assert ".cfg-workflow-header" in source
    assert "@media (max-width: 760px)" in source
    # "Grau de Confiança" foi para a primeira posição em 17/09/2026. A ordem
    # da barra tem que bater com a ordem dos corpos: st.tabs devolve as abas
    # na ordem dos rótulos, e desencontrar as duas troca o conteúdo de lugar
    # sem levantar erro nenhum.
    ordem_rotulos = [r for r in ("🎯 Grau de Confiança", "🔁 Atualização de dados",
                                 "🔄 Dados de mercado", "🗄️ Banco de dados",
                                 "🔒 Segurança")]
    posicoes = [source.index(f'"{r}"') for r in ordem_rotulos]
    assert posicoes == sorted(posicoes), "ordem dos rótulos das abas mudou"
    assert source.index("tab_conf, tab_atualizacao") < posicoes[0]

    # Abas de topo. "Controle" e "Investimentos" deixaram de ser abas irmãs
    # aqui em 06/09/2026: viraram sub-abas de "Atualização de dados", porque
    # são o mesmo gesto (subir arquivo, conferir, gravar) e disputavam a barra
    # com abas de diagnóstico.
    for rotulo in ('"🔁 Atualização de dados"', '"🔄 Dados de mercado"',
                   '"🗄️ Banco de dados"', '"🎯 Grau de Confiança"',
                   '"🔒 Segurança"'):
        assert rotulo in source, f"aba de topo sumiu: {rotulo}"

    # As duas continuam existindo por dentro -- agrupar não podia virar perder.
    assert '"💳 Controle Financeiro"' in source
    assert '"📈 Investimentos"' in source
    assert "_render_controle_financeiro()" in source
    assert "_render_investimentos()" in source


def test_tab_intro_escapes_dynamic_content(monkeypatch):
    rendered = _capture_markdown(monkeypatch)

    configuracoes._render_tab_intro(
        "Título <b>",
        "Descrição <img>",
        "Badge <svg>",
        "#00C896",
    )

    html = "\n".join(rendered)
    assert "cfg-tab-intro" in html
    assert "Título &lt;b&gt;" in html
    assert "Descrição &lt;img&gt;" in html
    assert "Badge &lt;svg&gt;" in html
    assert "<script>" not in html


def test_embedded_upload_flows_can_hide_duplicate_headers():
    card_signature = inspect.signature(
        credit_card_invoice_upload.render_upload_fatura_cartao
    )
    bank_signature = inspect.signature(
        bank_statement_upload.render_upload_extrato_bancario
    )

    assert card_signature.parameters["show_header"].default is True
    assert bank_signature.parameters["show_header"].default is True

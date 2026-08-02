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

    assert ".cfg-overview" in source
    assert ".cfg-tab-intro" in source
    assert ".cfg-workflow-header" in source
    assert "@media (max-width: 760px)" in source
    assert '"💳 Controle"' in source
    assert '"📈 Investimentos"' in source
    assert '"🔄 Dados de mercado"' in source
    assert '"🗄️ Banco de dados"' in source
    assert '"🔒 Segurança"' in source


def test_tab_intro_escapes_dynamic_content(monkeypatch):
    rendered = _capture_markdown(monkeypatch)

    configuracoes._render_tab_intro(
        "ÁREA <script>",
        "Título <b>",
        "Descrição <img>",
        "Badge <svg>",
        "#00C896",
    )

    html = "\n".join(rendered)
    assert "cfg-tab-intro" in html
    assert "ÁREA &lt;script&gt;" in html
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

import contextlib
import inspect
from datetime import date, datetime
from pathlib import Path

import views.dashboard_geral as dashboard


def _capture_markdown(monkeypatch):
    rendered: list[str] = []

    def fake_markdown(body, **_kwargs):
        rendered.append(body)

    monkeypatch.setattr(dashboard.st, "markdown", fake_markdown)
    return rendered


def test_dashboard_header_is_responsive_and_escapes_context(monkeypatch):
    rendered = _capture_markdown(monkeypatch)

    dashboard._render_dashboard_header(
        "Jul <script>alert(1)</script>",
        "Dados <b>externos</b>",
        "#00C896",
        date(2026, 7, 31),
    )

    html = "\n".join(rendered)
    assert "@media (max-width: 720px)" in html
    assert "31/07/2026" in html
    assert "Jul &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "Dados &lt;b&gt;externos&lt;/b&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_kpi_grid_uses_deterministic_financial_inputs(monkeypatch):
    rendered = _capture_markdown(monkeypatch)

    dashboard._render_kpi_grid(
        {"total": 688_080.92, "investido": 342_924.59, "delta_mes_pct": 2.5},
        receitas=10_000,
        despesas=5_000,
        investimentos=2_000,
        carteira={
            "total_mercado": 342_924.59,
            "num_ativos": 33,
            "rentabilidade_total_pct": 7.25,
        },
    )

    html = "\n".join(rendered)
    assert 'aria-label="Indicadores financeiros principais"' in html
    assert "Patrimônio investido" in html
    assert "R$ 342.924,59" in html
    assert "R$ 688.080,92" not in html
    assert "Valor de mercado consolidado · 33 ativos" in html
    assert "R$ 3.000,00" in html
    assert "30,00%" in html
    assert "+7.25%" in html
    assert "atingida" in html


def test_kpi_grid_handles_zero_revenue_without_division_error(monkeypatch):
    rendered = _capture_markdown(monkeypatch)

    dashboard._render_kpi_grid(
        {"total": 0, "delta_mes_pct": None},
        receitas=0,
        despesas=0,
        investimentos=0,
        carteira={"total_mercado": None, "rentabilidade_total_pct": None},
    )

    html = "\n".join(rendered)
    assert "0,00%" in html
    assert "em acompanhamento" in html
    assert "N/D" in html


def test_mock_mode_does_not_load_persisted_decision_models(monkeypatch):
    monkeypatch.setattr(dashboard.settings, "MOCK_MODE", True)

    def unexpected_call():
        raise AssertionError("persisted model must not be read in mock mode")

    monkeypatch.setattr(dashboard, "load_active_b3_portfolio_model", unexpected_call)
    monkeypatch.setattr(dashboard, "load_active_us_portfolio_model", unexpected_call)
    monkeypatch.setattr(dashboard, "_fiis_carteira_modelo", unexpected_call)

    assert dashboard._load_decision_models() == ({}, {}, [], False)


def test_allocation_decisions_heading_is_gone():
    """A seção "Decisões de alocação" foi removida do Dashboard Geral."""
    fonte = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert "Decisões de alocação" not in fonte


def test_portfolio_suggestions_render_after_every_other_section():
    """Sugestões de carteira fecham o dashboard — nada é renderizado depois."""
    corpo = inspect.getsource(dashboard.render)
    posicao_sugestoes = corpo.index("_secao_sugestoes_carteira(")
    for anterior in ("_render_kpi_grid(", "_secao_resumo_modulos(",
                     "_secao_raio_x_portfolio(", "Histórico mensal (6 meses)",
                     "Comparativo Ano a Ano"):
        assert corpo.index(anterior) < posicao_sugestoes, anterior
    assert corpo.rstrip().endswith("_secao_sugestoes_carteira(modelo_b3, modelo_us, "
                                   "fiis_port, fiis_salvo)")


def test_us_portfolio_has_its_own_dashboard_section():
    """Empresas Americanas entra no dashboard pelo mesmo caminho da B3."""
    corpo = inspect.getsource(dashboard._secao_sugestoes_carteira)
    assert "_secao_portfolio_modelo_b3(modelo_b3)" in corpo
    assert "_secao_portfolio_modelo_us(modelo_us)" in corpo
    # Ambas as seções passam pelo mesmo renderizador: divergir é o defeito.
    for funcao in (dashboard._secao_portfolio_modelo_b3,
                   dashboard._secao_portfolio_modelo_us):
        assert "_secao_carteira_modelo(" in inspect.getsource(funcao)


def test_model_company_list_is_not_silently_truncated(monkeypatch):
    """Carteira maior que o teto declara quantas ficaram de fora."""
    rendered = _capture_markdown(monkeypatch)
    monkeypatch.setattr(dashboard.st, "caption", lambda *a, **k: None)
    monkeypatch.setattr(dashboard.st, "button", lambda *a, **k: False)
    monkeypatch.setattr(
        dashboard.st, "columns",
        lambda spec, **k: [contextlib.nullcontext()] * (
            spec if isinstance(spec, int) else len(spec)),
    )

    total = dashboard._MAX_TICKERS_VISIVEIS + 4
    modelo = {
        "items": [
            {"ticker": f"TK{i:02d}", "nome": f"Empresa {i}", "weight": 1 / total,
             "setor": "Setor"}
            for i in range(total)
        ],
        "metrics_json": {"score_medio": 1.0},
        "ano_compra": 2026,
        "created_at": datetime(2026, 7, 1),
    }
    dashboard._secao_portfolio_modelo_b3(modelo)

    html = "\n".join(rendered)
    assert "TK00" in html and f"TK{dashboard._MAX_TICKERS_VISIVEIS - 1:02d}" in html
    assert "+4 outras" in html
    assert f"{dashboard._MAX_PESOS_VISIVEIS} maiores pesos de {total} empresas." in html

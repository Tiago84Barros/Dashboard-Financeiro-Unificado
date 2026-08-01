from datetime import date

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
    monkeypatch.setattr(dashboard, "_fiis_carteira_modelo", unexpected_call)

    assert dashboard._load_decision_models() == ({}, [], False)

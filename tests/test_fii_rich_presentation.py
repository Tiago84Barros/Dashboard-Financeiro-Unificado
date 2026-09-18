from copy import deepcopy

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.fii_methodology import MacroScenario
from core.fii_portfolio_v4 import PortfolioPolicy
from core.fii_presentation import enrich_review_presentation
from core.llm_context_fii import build_fii_chat_context
from core.portfolio_review_routes import fii_review
from tests.fii_rich_preview import synthetic_rows


def test_presentation_preserves_weights_protection_and_metadata():
    proposal = {"items": [
        {"ticker": "TEST11", "tipo": "tijolo", "weight": .2, "dy_12m": .1,
         "income_recurrence": .8, "confidence": .9},
        {"ticker": "DEMO11", "tipo": "papel", "weight": .3, "dy_12m": .2,
         "income_recurrence": None, "confidence": .9}],
        "allocated_weight": .5, "unallocated_weight": .5,
        "reasons": ["limite preservado"], "can_publish": False}
    before = deepcopy(proposal)
    result = enrich_review_presentation(proposal, PortfolioPolicy())
    assert proposal == before
    assert result["items"] == before["items"]
    assert result["unallocated_weight"] == .5
    assert not result["can_publish"]
    assert result["blockers"] == proposal["reasons"]
    assert result["trailing_yield_12m"] == pytest.approx(.16)
    assert result["effective_assets"] == pytest.approx(1 / (.4**2 + .6**2))
    assert result["recurrent_yield_12m"] == pytest.approx(.08)
    assert result["recurrent_yield_coverage"] == pytest.approx(.4)
    result["items"][0]["dy_12m"] = None
    result = enrich_review_presentation(result, PortfolioPolicy())
    assert result["trailing_yield_12m"] is None


def test_chat_receives_invested_scope_and_total_capital_weights():
    rows = synthetic_rows()
    scenario = MacroScenario(selic=15, ipca=4)
    result = enrich_review_presentation(fii_review(rows, PortfolioPolicy(), scenario),
                                       PortfolioPolicy())
    context = build_fii_chat_context(
        user_question="Qual o saldo?", selected_items=result["items"], scored_rows=rows,
        methodology_rows=rows, portfolio_result=result, scenario=scenario,
        reports=[], prices=pd.DataFrame())
    assert "parcela investida; saldo não alocado sem retorno presumido" in context
    assert f"saldo não alocado: {result['unallocated_weight']:.2%}" in context
    assert "publicável=não" in context


def preview(mode="partial", history=True, llm=True):
    return AppTest.from_string(
        "from tests.fii_rich_preview import render_preview\n"
        f"render_preview({mode!r}, history={history!r}, llm={llm!r})",
        default_timeout=60).run()


@pytest.mark.parametrize("mode", ["partial", "full"])
def test_actual_portfolio_route_keeps_rich_panels_and_chat(mode):
    app = preview(mode)
    assert not app.exception
    body = "\n".join(element.value for element in app.markdown)
    for label in ("Cenários estruturais", "Correlação entre os FIIs",
                  "Por que estes FIIs avançaram", "Retrospectiva da seleção",
                  "Risco × número de fundos", "Chat especializado em FIIs",
                  "DY recorrente ponderado", "fii-selection-card"):
        assert label in body
    assert len(app.get("plotly_chart")) >= 3
    assert len(app.chat_input) == 1
    assert app.button(key="fii_save_model_integrated_v6_5").disabled
    assert not app.session_state["fii_portfolio_can_publish"]
    table = next(e.value for e in app.dataframe if "Ticker" in e.value.columns)
    assert {"P/VP", "DY divulgado", "DY recorrente", "Confiança", "Peso", "Tipo"}.issubset(table.columns)
    if mode == "partial":
        expected = fii_review(synthetic_rows(), PortfolioPolicy(max_assets=14, max_asset=.1),
                              MacroScenario(selic=15, ipca=4))
        assert app.session_state["fii_port"] == {
            row["ticker"]: row["weight"] for row in expected["items"]}
        assert "Capital não alocado" in body
        captions = "\n".join(e.value for e in app.caption)
        invested = sum(app.session_state["fii_port"].values())
        effective = 1 / sum((w / invested)**2 for w in app.session_state["fii_port"].values())
        assert f"Número efetivo: {effective:.1f} de" in captions
        assert "dentro da banda tática de" not in body
    else:
        assert "Capital não alocado" not in body
        assert sum(app.session_state["fii_port"].values()) == pytest.approx(1)
    app.chat_input[0].set_value("Explique os riscos").run()
    assert not app.exception
    assert "Explique os riscos" in app.session_state["fii_chat_history"][-1]["content"]
    app.slider(key="fii_pref_integrated_max_asset").set_value(5).run()
    assert not app.exception
    # O carregador persistente inicializa a lista do novo contexto como vazia.
    assert app.session_state["fii_chat_history"] == []


def test_no_history_and_no_provider_do_not_remove_details():
    app = preview(history=False, llm=False)
    assert not app.exception
    assert any("Não há pelo menos dois FIIs" in e.value for e in app.info)
    assert any("Nenhum provedor LLM" in e.value for e in app.info)
    assert any("Por que estes FIIs avançaram" in e.value for e in app.markdown)


def test_empty_universe_has_explicit_state_and_no_stale_portfolio():
    app = preview("empty")
    assert not app.exception
    assert "fii_port" not in app.session_state
    assert not app.session_state["fii_portfolio_can_publish"]

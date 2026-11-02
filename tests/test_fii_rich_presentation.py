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


def preview(mode="partial", history=True, llm=True, falha_llm=False, rodar=True):
    app = AppTest.from_string(
        "from tests.fii_rich_preview import render_preview\n"
        f"render_preview({mode!r}, history={history!r}, llm={llm!r}, "
        f"falha_llm={falha_llm!r})",
        default_timeout=60).run()
    # A seleção só roda no botão, como na Criação de Portfólio de B3 e EUA.
    return app.button(key="fii_carteira_rodar").click().run() if rodar else app


def test_carteira_nao_roda_antes_do_botao():
    app = preview(rodar=False)
    assert not app.exception
    assert "fii_port" not in app.session_state
    assert any("Rodar seleção de FIIs" in e.value for e in app.info)
    body = "\n".join(element.value for element in app.markdown)
    assert "Por que estes FIIs avançaram" not in body


def test_falha_da_llm_nao_vira_resposta_no_historico():
    # Gravada, a mensagem de falha voltava no turno seguinte como fala do
    # assistente; e a pergunta sem resposta ficava sozinha na memória.
    app = preview(falha_llm=True)
    app.chat_input[0].set_value("Explique os riscos").run()
    assert not app.exception
    assert app.session_state["fii_chat_history"] == []
    avisos = "\n".join(w.value for w in app.warning)
    assert "Não foi possível consultar a IA agora" in avisos
    tela = avisos + "\n".join(str(m.value) for m in app.markdown)
    assert "org-sintetica-segredo" not in tela


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
    carteira_antes = dict(app.session_state["fii_port"])
    app.slider(key="fii_pref_integrated_max_asset").set_value(5).run()
    assert not app.exception
    # Mexer no controle não troca a carteira: avisa e espera o botão.
    assert app.session_state["fii_port"] == carteira_antes
    assert any("Os parâmetros mudaram" in e.value for e in app.info)
    app.button(key="fii_carteira_rodar").click().run()
    assert not app.exception
    assert not any("Os parâmetros mudaram" in e.value for e in app.info)
    # O carregador persistente inicializa a lista do novo contexto como vazia.
    assert app.session_state["fii_chat_history"] == []


def test_no_history_and_no_provider_do_not_remove_details():
    app = preview(history=False, llm=False)
    assert not app.exception
    # A lacuna de correlacao sai da tela desde 05/10/2026 (vai para
    # Configuracoes -> Restricoes); o resto do detalhe continua.
    assert not any("Não há pelo menos dois FIIs" in e.value for e in app.info)
    # A falta de provedor LLM também sai da tela (vai para o registro); fica
    # só a frase neutra, sem nome de variável de ambiente.
    assert not any("Nenhum provedor LLM" in e.value for e in app.info)
    assert not any("OPENAI_API_KEY" in e.value for e in app.info)
    assert any("O chat não está disponível" in e.value for e in app.info)
    assert any("Por que estes FIIs avançaram" in e.value for e in app.markdown)


def test_empty_universe_has_explicit_state_and_no_stale_portfolio():
    app = preview("empty")
    assert not app.exception
    assert "fii_port" not in app.session_state
    assert not app.session_state["fii_portfolio_can_publish"]


def test_montagem_roda_uma_vez_por_clique_e_nao_a_cada_interacao():
    """A montagem custa dezenas de segundos; chat e controles não a repetem."""
    from unittest.mock import patch

    import views.fiis as fiis

    original = fiis.veredito.reotimizar_sem_vetados
    with patch.object(fiis.veredito, "reotimizar_sem_vetados",
                      side_effect=original) as montagem:
        app = preview("full")
        assert not app.exception
        assert montagem.call_count == 1
        carteira = dict(app.session_state["fii_port"])

        app.chat_input[0].set_value("Explique os riscos").run()
        app.slider(key="fii_pref_integrated_max_asset").set_value(5).run()
        assert not app.exception
        assert montagem.call_count == 1
        assert app.session_state["fii_port"] == carteira

        app.button(key="fii_carteira_rodar").click().run()
        assert montagem.call_count == 2
        app.button(key="fii_carteira_rodar").click().run()
        assert montagem.call_count == 2  # mesmos parâmetros: nada a refazer


def test_chave_da_montagem_muda_com_a_metrica_e_nao_com_a_procedencia():
    import views.fiis as fiis

    inputs = pd.DataFrame()
    inputs.attrs["snapshot_as_of"] = "2026-10-31"
    prefs = {"scenario": MacroScenario(selic=15, ipca=4), "macro_mode": "moderate",
             "scenario_provenance": {"selic": object()}}
    linhas = [{"ticker": "AAAA11", "dy_12m": .1}]

    def chave(linhas_, prefs_=prefs):
        return fiis._chave_da_montagem(prefs_, PortfolioPolicy(), linhas_, [],
                                       "unvalidated", {}, None, inputs)

    assert chave(linhas) == chave([dict(linhas[0])])
    # Procedência é objeto sem repr estável e não muda a carteira.
    assert chave(linhas) == chave(linhas, {**prefs, "scenario_provenance": {"selic": object()}})
    assert chave(linhas) != chave([{"ticker": "AAAA11", "dy_12m": .11}])
    assert chave(linhas) != chave(linhas, {**prefs, "macro_mode": "scenario"})

import pandas as pd

import core.llm_fii as llm_fii
from core.fii_methodology import MacroScenario
from core.llm_context_fii import build_fii_chat_context


def _fii(ticker="TEST11", fii_type="tijolo", selected=True):
    return {
        "ticker": ticker,
        "tipo": fii_type,
        "sector": "Logística",
        "weight": .10 if selected else None,
        "type_score": 72,
        "confidence": .81,
        "coverage": .76,
        "dy_12m": .12,
        "pvp": .88,
        "liquidez_diaria": 2_000_000,
        "patrimonio_liquido": 1_500_000_000,
        "property_count": 12,
        "region_count": 3,
        "vacancia_fisica": .04,
        "tenant_concentration": .18,
        "regions": {"Sudeste": .60, "Sul": .25, "Nordeste": .15},
        "missing_critical": ("wault_anos",),
        "metric_metadata": {
            "vacancia_fisica": {
                "reference_date": "2026-06-30",
                "source": "cvm_structured",
            }
        },
    }


def test_fii_context_contains_portfolio_category_evidence_and_limitations():
    selected = _fii()
    peer = _fii("PEER11", selected=False)
    context = build_fii_chat_context(
        user_question="Compare TEST11 com PEER11",
        selected_items=[selected],
        scored_rows=[selected, peer],
        methodology_rows=[selected, peer],
        portfolio_result={
            "expected_yield": .115,
            "effective_assets": 1,
            "can_publish": False,
            "blockers": ["validação PIT pendente"],
        },
        scenario=MacroScenario(selic=14, ipca=4.5, vacancy_shock=.08),
        reports=[{"ticker": "TEST11", "facts": ["P/VP 0.88"], "structure": []}],
        prices=pd.DataFrame(),
    )
    assert "FII TEST11 | selecionado=sim" in context
    assert "FII PEER11 | selecionado=não" in context
    assert "vacancia_fisica=4.00%" in context
    assert "wault_anos" in context
    assert "2026-06-30(cvm_structured)" in context
    assert "validação PIT pendente" in context
    assert "Métricas ausentes não foram imputadas" in context


def test_fii_chat_uses_specialized_guardrails_and_bounded_history(monkeypatch):
    captured = {}

    def fake_complete(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return "resposta"

    monkeypatch.setattr(llm_fii, "_chat_complete", fake_complete)
    history = [{"role": "user", "content": f"pergunta {i}"} for i in range(15)]
    answer = llm_fii.chat_com_fiis("CONTEXTO TESTE", history, "E o risco de crédito?",
                                   model="modelo-teste")
    assert answer == "resposta"
    assert len(captured["messages"]) == 12  # system + 10 históricos + pergunta atual
    system = captured["messages"][0]["content"]
    assert "mercado brasileiro" in system
    assert "Não invente WAULT" in system
    assert "Ausência de dado não significa risco zero" in system
    assert "CONTEXTO TESTE" in system
    assert captured["kwargs"]["primary_model"] == "modelo-teste"
    assert captured["kwargs"]["json_mode"] is False

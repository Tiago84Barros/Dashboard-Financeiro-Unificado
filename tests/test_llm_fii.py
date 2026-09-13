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


def test_contexto_entrega_yield_recorrente_e_declara_protecao_nao_divulgada():
    row = _fii()
    row["tenant_concentration"] = None
    row["income_recurrence"] = .75
    context = build_fii_chat_context(
        user_question="Avalie TEST11",
        selected_items=[row], scored_rows=[row], methodology_rows=[row],
        portfolio_result={"expected_yield": .12, "recurrent_yield_12m": .09,
                          "effective_assets": 1, "can_publish": False},
        scenario=MacroScenario(selic=14, ipca=4.5), prices=pd.DataFrame(),
    )
    assert "DY recorrente=9.00%" in context
    assert "DY divulgado=12.00%" in context
    assert "tenant_concentration=não divulgado pelo gestor" in context


def test_prompt_exige_declarar_protecao_nao_divulgada(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        llm_fii, "_chat_complete",
        lambda messages, **kwargs: captured.setdefault("messages", messages) and "ok",
    )
    assert llm_fii.chat_com_fiis("CONTEXTO", [], "pergunta") == "ok"
    system = captured["messages"][0]["content"].lower()
    assert "renda recorrente" in system
    assert "não divulgado pelo gestor" in system


def test_contexto_declara_a_cessao_de_protecao_na_elegibilidade():
    """A IA não pode responder sobre a carteira sem saber o que foi cedido."""
    selecionado = _fii()
    readmitido = _fii("REND11")
    context = build_fii_chat_context(
        user_question="A carteira está dentro da metodologia?",
        selected_items=[selecionado, readmitido],
        scored_rows=[selecionado],
        methodology_rows=[selecionado, readmitido],
        portfolio_result={
            "can_publish": False,
            "blockers": [],
            "viability_notes": [
                "proteção ao investidor cedida na elegibilidade para viabilizar "
                "a carteira (1 de 3 candidatos readmitidos, na ordem crescente "
                "de severidade): REND11 — renda recorrente abaixo do mínimo. "
                "Proteção cedida não é ausência de risco."
            ],
            "protecao_cedida_na_elegibilidade": [{
                "ticker": "REND11",
                "motivos": ["renda recorrente abaixo do mínimo"],
                "severidade": 1, "na_carteira": True,
            }],
        },
        scenario=MacroScenario(selic=14, ipca=4.5),
        prices=pd.DataFrame(),
    )

    assert "REND11 — renda recorrente abaixo do mínimo" in context
    assert "não é ausência de risco" in context
    assert "elegíveis no universo estrito=1 (+1 readmitidos" in context
    assert "notas de viabilidade: proteção ao investidor cedida" in context


def test_contexto_sem_cessao_diz_nenhuma():
    selecionado = _fii()
    context = build_fii_chat_context(
        user_question="ok?", selected_items=[selecionado],
        scored_rows=[selecionado], methodology_rows=[selecionado],
        portfolio_result={"can_publish": True, "blockers": []},
        scenario=MacroScenario(selic=14, ipca=4.5), prices=pd.DataFrame(),
    )

    assert "proteção ao investidor cedida na elegibilidade: nenhuma" in context


def test_bloco_do_fundo_cedido_declara_o_portao_reprovado():
    """A declaração agregada no topo não chega onde a IA lê o fundo.

    Medido no passo 7: perguntado sobre o desconto patrimonial de um fundo
    readmitido por cessão, o modelo apresentou o vencimento concentrado como
    risco comum — "concentração moderada" — porque o bloco DETALHES DOS FUNDOS
    trazia a métrica sem dizer que ela reprovou o portão. Duas linhas de
    agregado no topo não se ligam sozinhas ao fundo lá embaixo: é a própria
    proteção cedida voltando a parecer ausência de risco.
    """
    selecionado = _fii()
    readmitido = _fii("REND11")
    context = build_fii_chat_context(
        user_question="Por que REND11 negocia com desconto?",
        selected_items=[selecionado, readmitido],
        scored_rows=[selecionado],
        methodology_rows=[selecionado, readmitido],
        portfolio_result={
            "can_publish": False, "blockers": [],
            "protecao_cedida_na_elegibilidade": [{
                "ticker": "REND11",
                "motivos": ["vencimentos em 24m acima do teto"],
                "severidade": 1, "na_carteira": True,
            }],
        },
        scenario=MacroScenario(selic=14, ipca=4.5), prices=pd.DataFrame(),
    )

    bloco = context.split("FII REND11 |", 1)[1].split("\nFII ", 1)[0]
    assert "vencimentos em 24m acima do teto" in bloco
    assert "cedid" in bloco.lower()

    bloco_limpo = context.split("FII TEST11 |", 1)[1].split("\nFII ", 1)[0]
    assert "cedid" not in bloco_limpo.lower(), "fundo aprovado não carrega cessão"

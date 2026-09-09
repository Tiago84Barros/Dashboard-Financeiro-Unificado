"""O cenário macro do chat de FIIs precisa partir da observação, não de um literal.

O relatório publicado afirmava "a taxa Selic informada está em 15,0%" porque a
tela usava 15.0 como padrão do widget e o prompt rotulava esse padrão como
"CENÁRIO INFORMADO PELO USUÁRIO". A Selic observada em public.macro era 14,0%.
"""
from __future__ import annotations

import pandas as pd

import core.llm_fii as llm_fii
from core.fii_methodology import MacroScenario
from core.llm_context_fii import build_fii_chat_context
from core.macro_cenario import PADRAO_SEM_OBSERVACAO, cenario_macro_observado


def _history() -> dict[int, dict[str, float]]:
    """Escala de public.macro: selic em fração, ipca em ponto percentual."""
    return {
        2024: {"selic": .1225, "ipca": 4.83},
        2025: {"selic": .15, "ipca": 4.26},
        2026: {"selic": .14, "ipca": 3.44},
    }


def test_cenario_observado_usa_o_ano_mais_recente_em_ponto_percentual():
    cenario = cenario_macro_observado(_history())
    assert cenario.ano == 2026
    assert cenario.selic == 14.0
    assert cenario.ipca == 3.44
    assert cenario.indisponivel is None
    assert "public.macro" in cenario.fonte


def test_cenario_observado_deriva_a_variacao_de_12_meses_em_pontos():
    assert cenario_macro_observado(_history()).selic_change_12m == -1.0


def test_cenario_observado_aceita_selic_ja_gravada_em_percentual():
    assert cenario_macro_observado({2026: {"selic": 14.0}}).selic == 14.0


def test_sem_ano_anterior_a_variacao_fica_ausente_em_vez_de_zero():
    """Zero é uma afirmação de estabilidade; ausência de série não é isso."""
    assert cenario_macro_observado({2026: {"selic": .14}}).selic_change_12m is None


def test_historico_vazio_declara_indisponibilidade_e_nao_inventa_taxa():
    cenario = cenario_macro_observado({})
    assert cenario.selic is None
    assert cenario.ipca is None
    assert cenario.ano is None
    assert cenario.indisponivel
    assert cenario.padrao_selic == PADRAO_SEM_OBSERVACAO


def test_valores_nao_finitos_nao_viram_observacao():
    cenario = cenario_macro_observado({2026: {"selic": float("nan")}})
    assert cenario.selic is None
    assert cenario.indisponivel


def _fii(ticker="TEST11", selected=True):
    return {
        "ticker": ticker,
        "tipo": "papel",
        "sector": "Recebíveis",
        "weight": .10 if selected else None,
        "type_score": 72,
        "confidence": .81,
        "coverage": .76,
        "dy_12m": .12,
        "income_recurrence": .8886,
    }


def _context(**kwargs) -> str:
    base = dict(
        user_question="Quais são os riscos?",
        selected_items=[_fii()],
        scored_rows=[_fii()],
        methodology_rows=[_fii()],
        portfolio_result={"can_publish": False, "blockers": []},
        scenario=MacroScenario(selic=14.0, ipca=3.44, selic_change_12m=-1.0),
        reports=[],
        prices=pd.DataFrame(),
    )
    base.update(kwargs)
    return build_fii_chat_context(**base)


def test_contexto_nao_atribui_ao_usuario_um_cenario_que_ele_nao_informou():
    context = _context(scenario_provenance={
        "selic": "observado (public.macro, ano 2026)",
        "ipca": "observado (public.macro, ano 2026)",
    })
    assert "CENÁRIO INFORMADO PELO USUÁRIO" not in context
    assert "Selic=14%" in context
    assert "observado (public.macro, ano 2026)" in context


def test_contexto_marca_explicitamente_o_valor_ajustado_pelo_usuario():
    context = _context(scenario_provenance={"selic": "ajustado pelo usuário"})
    assert "ajustado pelo usuário" in context


def test_sem_procedencia_o_contexto_nao_afirma_origem():
    context = _context()
    assert "CENÁRIO INFORMADO PELO USUÁRIO" not in context
    assert "CENÁRIO MACRO APLICADO" in context


def test_contexto_nao_manda_procurar_fora_o_que_ele_proprio_entrega():
    """A linha de limitação empurrava a conclusão para o relatório gerencial."""
    context = _context()
    assert "não substituem leitura dos relatórios gerenciais" not in context
    assert "income_recurrence=88.86%" in context


def test_prompt_proibe_delegar_ao_usuario_dado_presente_no_contexto(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm_fii, "_chat_complete",
                        lambda messages, **kwargs: captured.setdefault("m", messages) and "ok")
    llm_fii.chat_com_fiis("CONTEXTO", [], "pergunta")
    system = captured["m"][0]["content"]
    assert "não instrua o usuário a consultar" in system.lower()
    assert "nomeando a métrica ausente" in system.lower()

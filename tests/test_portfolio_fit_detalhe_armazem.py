"""Portfolio Fit recebe o detalhe do armazém do ativo avaliado.

Era o último ponto de entrada de LLM com ativo que não o recebia. A leitura
valida cada número contra o texto que a LLM viu (``texto_ancora``); por isso o
detalhe vai colado ao bloco de mercado, que entra no prompt e na âncora juntos.
O teste que prende isso é o do número que só existe no detalhe: com o detalhe,
a leitura é aprovada; sem ele, a mesma resposta é rejeitada.
Nada aqui toca rede nem banco.
"""
import dataclasses
import json

from core import llm_context_global_armazem as ga
from core.inteligencia_ativos import leitura_llm
from core.inteligencia_ativos import portfolio_fit as pf
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx
from tests.test_inteligencia_ativos_portfolio_fit import (
    MERCADO,
    _com_fundamentos,
    _resposta,
)

DETALHE = ("DETALHE DO ARMAZÉM LOCAL (lido direto)\n  TAEE11: volume financeiro "
           "mediano R$ 87,3 mi/dia em 21 pregões.")


def _leitores_falsos(chamadas):
    return lambda: {c: (lambda s, c=c: chamadas.append((c, list(s))) or DETALHE)
                    for c in ("b3", "fii", "us")}


def _com_classe(a, classe, moeda="BRL"):
    return dataclasses.replace(a, ativo=dataclasses.replace(a.ativo, classe=classe,
                                                            moeda=moeda))


def test_detalhe_pede_o_leitor_da_classe_do_ativo(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ga, "_leitores", _leitores_falsos(chamadas))
    a = _com_classe(_analise("TAEE11", _ctx()), "Ações BR")
    assert leitura_llm.detalhe_armazem_do_ativo(a) == DETALHE
    assert chamadas == [("b3", ["TAEE11"])]

    chamadas.clear()
    leitura_llm.detalhe_armazem_do_ativo(_com_classe(a, "FII"))
    leitura_llm.detalhe_armazem_do_ativo(_com_classe(a, "ETF", moeda="USD"))
    assert [c for c, _ in chamadas] == ["fii", "us"]


def test_classe_sem_leitor_fica_sem_detalhe(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ga, "_leitores", _leitores_falsos(chamadas))
    a = _com_classe(_analise("TAEE11", _ctx()), "Tesouro")
    assert leitura_llm.detalhe_armazem_do_ativo(a) == "" and chamadas == []


def test_falha_do_leitor_vira_linha_nomeada(monkeypatch):
    def quebra():
        raise RuntimeError("sem túnel")

    monkeypatch.setattr(ga, "_leitores", quebra)
    a = _com_classe(_analise("TAEE11", _ctx()), "Ações BR")
    texto = leitura_llm.detalhe_armazem_do_ativo(a)
    assert "falha ao montar (RuntimeError)" in texto and "Não trate como dado zero" in texto


def _gerar(monkeypatch, detalhe):
    ctx = _ctx()
    a = _com_classe(_com_fundamentos(_analise("TAEE11", ctx)), "Ações BR")
    monkeypatch.setattr(leitura_llm, "contexto_mercado_do_ativo", lambda _a: MERCADO)
    monkeypatch.setattr(leitura_llm, "detalhe_armazem_do_ativo", lambda _a: detalhe)
    visto = {}
    validar = pf.validar

    def espia(dado, contexto_, ancora):
        visto["ancora"] = ancora
        return validar(dado, contexto_, ancora)

    monkeypatch.setattr(pf, "validar", espia)

    def falsa(msgs):
        visto["prompt"] = msgs[1]["content"]
        return json.dumps(_resposta(
            portfolio_impact="Liquidez mediana de R$ 87,3 mi/dia não limita o aporte."),
            ensure_ascii=False)

    return leitura_llm.gerar(a, ctx, chamar=falsa), visto


def test_detalhe_entra_no_prompt_e_na_ancora_da_validacao(monkeypatch):
    """O verificador de números é leniente (aceita somas e razões do
    contexto), então "aprovada" sozinho não prova nada. O que se prende é a
    fiação: o detalhe está no texto que a LLM viu E no que a validação usa."""
    leitura, visto = _gerar(monkeypatch, DETALHE)
    assert DETALHE in visto["prompt"] and "Selic 15,00%" in visto["prompt"]
    assert DETALHE in visto["ancora"]
    assert leitura.status == pf.APROVADA, leitura.problemas


def test_sem_detalhe_o_bloco_de_mercado_segue_sozinho(monkeypatch):
    _, visto = _gerar(monkeypatch, "")
    assert "DETALHE DO ARMAZÉM" not in visto["prompt"]
    assert visto["prompt"].rstrip().endswith("Selic 15,00% a.a. (Bacen).")


def test_prompt_explica_o_bloco():
    s = pf.sistema()
    assert "DETALHE DO ARMAZÉM LOCAL" in s and "lacuna, não zero" in s

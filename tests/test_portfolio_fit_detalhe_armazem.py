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
from core.inteligencia_ativos import armazem_fatos, leitura_llm
from core.inteligencia_ativos import portfolio_fit as pf
from tests import test_b3_detalhe_armazem as tb3
from tests.test_inteligencia_ativos_adequacao import _analise, _ctx
from tests.test_inteligencia_ativos_portfolio_fit import (
    MERCADO,
    _com_fundamentos,
    _resposta,
)

DETALHE = ("DETALHE DO ARMAZÉM LOCAL (lido direto)\n  TAEE11: volume financeiro "
           "mediano R$ 87,3 mi/dia em 21 pregões.")


def _leitores_falsos(chamadas):
    return lambda: {c: (lambda s, c=c, captura=None: chamadas.append((c, list(s)))
                        or DETALHE)
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


FATOS = {"fonte": "pregão diário da B3", "pregao_de_referencia": "26/09/2026",
         "liquidez": {"volume_financeiro_mediano_21_pregoes": "R$ 87.3 mi/dia"}}


def test_armazem_do_ativo_le_uma_vez_e_devolve_texto_e_campos(monkeypatch):
    bruto = tb3._detalhe()["WEGE3"]
    chamadas = []

    def leitor(s, captura=None):
        chamadas.append(list(s))
        captura.update({x: bruto for x in s})
        return DETALHE

    monkeypatch.setattr(ga, "_leitores", lambda: {"b3": leitor})
    a = _com_classe(_analise("TAEE11", _ctx()), "Ações BR")
    texto, fatos = leitura_llm.armazem_do_ativo(a)
    assert texto == DETALHE and chamadas == [["TAEE11"]]
    assert fatos["liquidez"]["volume_financeiro_mediano_21_pregoes"].endswith("/dia")


def test_armazem_do_ativo_sem_dado_nomeia_a_lacuna(monkeypatch):
    monkeypatch.setattr(ga, "_leitores", _leitores_falsos([]))
    a = _com_classe(_analise("TAEE11", _ctx()), "Ações BR")
    _, fatos = leitura_llm.armazem_do_ativo(a)
    assert fatos["estado"] == "indisponível" and "DETALHE DO ARMAZÉM" in fatos["motivo"]
    _, fatos = leitura_llm.armazem_do_ativo(_com_classe(a, "Tesouro"))
    assert fatos == {"estado": armazem_fatos.NAO_SE_APLICA}


def _gerar(monkeypatch, detalhe, fatos=FATOS):
    ctx = _ctx()
    a = _com_classe(_com_fundamentos(_analise("TAEE11", ctx)), "Ações BR")
    monkeypatch.setattr(leitura_llm, "contexto_mercado_do_ativo", lambda _a: MERCADO)
    monkeypatch.setattr(leitura_llm, "armazem_do_ativo", lambda _a: (detalhe, fatos))
    visto = {}
    validar = pf.validar

    def espia(dado, contexto_, ancora):
        visto["ancora"] = ancora
        visto["contexto"] = contexto_
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


def test_campos_do_armazem_entram_no_contexto_e_na_ancora(monkeypatch):
    """No texto, a LLM real não citava os números; no JSON, cita."""
    leitura, visto = _gerar(monkeypatch, DETALHE)
    assert visto["contexto"]["warehouse_market"] == FATOS
    assert '"warehouse_market"' in visto["prompt"] and "R$ 87.3 mi/dia" in visto["ancora"]
    assert leitura.status == pf.APROVADA, leitura.problemas


def test_sem_leitura_do_armazem_o_campo_diz_que_nao_ha():
    ctx = _ctx()
    c = pf.contexto(_analise("TAEE11", ctx), ctx)
    assert c["warehouse_market"] == {"estado": pf.NAO_DISPONIVEL}


def test_prompt_explica_o_bloco():
    s = pf.sistema()
    assert "DETALHE DO ARMAZÉM LOCAL" in s and "lacuna, não zero" in s


def test_numeros_do_armazem_tem_campo_proprio_na_resposta():
    """Medido com LLM real: com os números no JSON e uma regra apontando para
    campos já existentes, 0 de 4 respostas os citou; com o campo próprio no
    esquema, 4 de 4. A volatilidade do armazém que diverge da de
    peer_comparison é escrita junto com ela, não escolhida."""
    s = pf.sistema()
    assert "9. \"warehouse_market\"" in s and "\"market_behavior\"" in s
    assert "peer_comparison" in s and "não escolha um em silêncio" in s
    assert "market_behavior" in pf.CAMPOS_TEXTO


def test_resposta_sem_o_campo_nao_vira_ressalva():
    ctx = _ctx()
    a = _com_fundamentos(_analise("TAEE11", ctx))
    c = pf.contexto(a, ctx)
    le = pf.validar(_resposta(), c, pf.texto_ancora(c, MERCADO))
    assert le.textos["market_behavior"] == pf.NAO_DISPONIVEL
    assert le.status == pf.APROVADA, le.problemas
    le = pf.validar(_resposta(market_behavior="Liquidez R$ 87,3 mi/dia."), c,
                    pf.texto_ancora(c, MERCADO + "\n" + DETALHE))
    assert le.textos["market_behavior"] == "Liquidez R$ 87,3 mi/dia."

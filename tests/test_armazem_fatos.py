"""Os números do detalhe do armazém como campos do contexto do Portfolio Fit.

Medido com LLM real (30/09/2026, TAEE11): o detalhe em texto, colado ao bloco
de mercado, não era citado — a LLM citava o JSON estruturado. Os campos de
``warehouse_market`` saem das mesmas funções de formatação do texto; o teste
que prende isso é o de que cada valor do campo aparece, igual, no texto.
Nada aqui toca rede nem banco.
"""
import pytest

from core import armazem_remoto as ar
from core.inteligencia_ativos import armazem_fatos as af
from tests import test_b3_detalhe_armazem as tb3
from tests import test_fii_detalhe_armazem as tfii
from tests import test_us_detalhe_armazem as tus


def _folhas(d):
    for k, v in d.items():
        if k == "fonte":  # rótulo, não número ("B3" tem dígito)
            continue
        if isinstance(v, dict):
            yield from _folhas(v)
        elif isinstance(v, str) and any(ch.isdigit() for ch in v):
            yield v


@pytest.mark.parametrize("classe, ticker, modulo, teste", [
    ("b3", "WEGE3", "core.b3_detalhe_armazem", tb3),
    ("us", "AAA", "core.us_detalhe_armazem", tus),
    ("fii", "HGLG11", "core.fii_detalhe_armazem", tfii),
])
def test_cada_numero_do_campo_esta_igual_no_texto(classe, ticker, modulo, teste):
    import importlib

    bruto = teste._detalhe()
    texto = importlib.import_module(modulo).resumo_para_prompt(bruto, origem="lido direto")
    f = af.fatos(classe, ticker.lower(), bruto)
    assert "estado" not in f and f["fonte"]
    numeros = list(_folhas(f))
    assert len(numeros) >= 3
    for v in numeros:
        # "/dia" é do campo; o texto diz o mesmo número com outra cauda.
        assert v.removesuffix("/dia") in texto, (v, texto)


def test_b3_traz_liquidez_retornos_e_volatilidade_com_janela():
    f = af.fatos("b3", "WEGE3", tb3._detalhe())
    assert set(f["liquidez"]) >= {"volume_financeiro_mediano_21_pregoes",
                                  "volume_financeiro_mediano_63_pregoes"}
    assert set(f["retorno_de_mercado_sem_proventos"]) == {"1m", "3m", "12m"}
    assert f["volatilidade_anualizada_12m"] and "DETALHE" in f["reacao_a_resultados"]


def test_classe_sem_leitor_nao_se_aplica():
    assert af.fatos(None, "LFT", {}) == {"estado": af.NAO_SE_APLICA}


def test_ausencia_vira_lacuna_com_o_motivo_do_leitor():
    aviso = ("DETALHE DO ARMAZÉM LOCAL: indisponível agora (PC ou túnel desligado?)\n"
             "segunda linha")
    f = af.fatos("b3", "WEGE3", {}, aviso=aviso)
    assert f["estado"] == "indisponível" and f["leitura"] == "lacuna, não dado zero"
    assert "PC ou túnel desligado" in f["motivo"] and "segunda" not in f["motivo"]
    assert "não devolveu" in af.fatos("us", "AAA", None)["motivo"]


def test_ativo_sem_pregao_diz_que_nao_tem():
    assert "sem negócios" in af.fatos("b3", "WEGE3", {"WEGE3": {"pregoes": [], "x": 1}})["estado"]


@pytest.mark.parametrize("mod, leitor, teste, ticker", [
    ("core.llm_context_b3", "detalhe_b3", tb3, "WEGE3"),
    ("core.llm_context_us", "detalhe_eua", tus, "AAA"),
    ("core.llm_context_fii", "detalhe_fii", tfii, "HGLG11"),
])
def test_leitor_entrega_o_bruto_na_captura(monkeypatch, mod, leitor, teste, ticker):
    """Uma leitura só: o Portfolio Fit pega os números da mesma ida ao armazém
    (ou ao túnel) que montou o texto."""
    import importlib

    import core.us_read as ur

    ctx = importlib.import_module(mod)
    bruto = teste._detalhe()
    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, leitor, lambda t: {x: next(iter(bruto.values())) for x in t})
    captura = {}
    texto = ctx.get_warehouse_detail_context([ticker], captura=captura)
    assert "lido pelo túnel" in texto and set(captura) == {ticker}

    captura.clear()
    monkeypatch.setattr(ur, "_db_is_local", lambda: True)
    monkeypatch.setattr(ur, "_engine", lambda: object())
    monkeypatch.setattr(teste.det, "ler_detalhe", lambda _e, t: {x: next(iter(bruto.values()))
                                                          for x in t})
    ctx.get_warehouse_detail_context([ticker], captura=captura)
    assert set(captura) == {ticker}


def test_falha_do_tunel_deixa_a_captura_vazia(monkeypatch):
    import core.llm_context_b3 as ctx
    import core.us_read as ur

    def _cai(_t):
        raise ar.ArmazemRemotoIndisponivel("sem resposta")

    monkeypatch.setattr(ur, "_db_is_local", lambda: False)
    monkeypatch.setattr(ar, "detalhe_b3", _cai)
    captura = {}
    ctx.get_warehouse_detail_context(["WEGE3"], captura=captura)
    assert captura == {}

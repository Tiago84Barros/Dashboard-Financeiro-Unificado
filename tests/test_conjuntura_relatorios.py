"""Relatórios de carteira B3/EUA recebem a conjuntura que os chats já recebiam.

Antes, o chat da carteira via manchetes e macro e o relatório institucional da
mesma carteira, gerado na mesma tela, não via nada disso: as duas leituras
divergiam sem que o usuário soubesse o porquê. E o chat recebia só o macro e
as manchetes gerais — ``bloco_contexto_mercado()`` era chamado sem os ativos,
então o noticiário de cada ativo da carteira nunca chegava.

O que se prende aqui: o texto da conjuntura chega ao prompt da empresa e ao
consolidado; sem conjuntura, o prompt diz que ela faltou em vez de calar; a
carteira inteira vai para o bloco com teto que cresce com ela; e falha na
montagem vira texto que nomeia a falha. Nada aqui toca rede nem banco.
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

import core.contexto_mercado as cm
import core.portfolio_report_b3 as rb3
import core.portfolio_report_us as rus

_MARCA = "MANCHETE-SINTETICA-XYZ de 25/09/2026"
_FALTOU = "Conjuntura não montada nesta execução"


def _captura(monkeypatch, modulo, resposta: dict) -> dict:
    capturado: dict[str, str] = {}

    def falso(prompt: str, model: str | None = None) -> str:
        capturado["prompt"] = prompt
        return json.dumps(resposta, ensure_ascii=False)

    monkeypatch.setattr(modulo, "_call_llm", falso)
    return capturado


@pytest.mark.parametrize("conjuntura,esperado", [(_MARCA, _MARCA), ("", _FALTOU)])
def test_relatorio_da_empresa_b3_leva_a_conjuntura(monkeypatch, conjuntura, esperado):
    dossie = {"identificacao": {"nome": "Teste", "setor": "Petróleo",
                                "subsetor": "S", "segmento": "G"},
              "series_anuais": []}
    monkeypatch.setattr(rb3, "build_dossie", lambda tk: dossie)
    monkeypatch.setattr(rb3, "build_peer_context", lambda *a, **k: "PARES")
    cap = _captura(monkeypatch, rb3, {})
    rb3.generate_company_portfolio_report("TEST3", df_fin=pd.DataFrame(),
                                          conjuntura=conjuntura)
    assert esperado in cap["prompt"]
    # A regra que impede notícia de virar fato documentado acompanha o bloco.
    assert "segundo notícia de" in cap["prompt"]


@pytest.mark.parametrize("conjuntura,esperado", [(_MARCA, _MARCA), ("", _FALTOU)])
def test_relatorio_da_empresa_eua_leva_a_conjuntura(monkeypatch, conjuntura, esperado):
    dossie = {"name": "Test Inc", "sector": "Energy", "industry": "Oil"}
    monkeypatch.setattr(rus, "build_dossie", lambda tk: dossie)
    monkeypatch.setattr(rus, "dossie_to_text", lambda d: "DOSSIE")
    monkeypatch.setattr(rus, "build_peer_context", lambda *a, **k: "PARES")
    cap = _captura(monkeypatch, rus, {})
    rus.generate_company_us_report("TST", df_fin=pd.DataFrame(), conjuntura=conjuntura)
    assert esperado in cap["prompt"]
    assert "segundo notícia de" in cap["prompt"]


@pytest.mark.parametrize("conjuntura,esperado", [(_MARCA, _MARCA), ("", _FALTOU)])
def test_consolidados_levam_a_conjuntura(monkeypatch, conjuntura, esperado):
    cap_b3 = _captura(monkeypatch, rb3, {})
    rb3.analyze_portfolio_report([], {}, conjuntura=conjuntura)
    assert esperado in cap_b3["prompt"]

    cap_us = _captura(monkeypatch, rus, {})
    rus.analyze_us_portfolio_report([], {}, conjuntura=conjuntura)
    assert esperado in cap_us["prompt"]


def test_carteira_inteira_vai_para_o_bloco_com_teto_que_cresce(monkeypatch):
    visto: dict = {}

    def bloco(ativos, **k):
        visto.update(ativos=ativos, **k)
        return "BLOCO"

    monkeypatch.setattr(cm, "bloco_contexto_mercado", bloco)
    itens = [{"ticker": f"AAA{i}", "setor": "Bancos"} for i in range(8)]
    itens.append({"symbol": "msft", "setor": "Technology"})  # carteira americana
    itens.append({"ticker": "", "setor": "sem ticker"})
    assert cm.conjuntura_da_carteira("b3", itens) == "BLOCO"
    assert set(visto["ativos"]["b3"]) == {f"AAA{i}" for i in range(8)} | {"MSFT"}
    # Teto fixo em 10 deixava parte da carteira parecendo sem notícia.
    assert visto["max_itens_por_classe"] >= 2 * 9


def test_carteira_vazia_ainda_leva_macro_e_manchetes(monkeypatch):
    visto: dict = {}
    monkeypatch.setattr(cm, "bloco_contexto_mercado",
                        lambda ativos, **k: visto.setdefault("ativos", ativos) or "B")
    cm.conjuntura_da_carteira("us", [])
    assert visto["ativos"] == {}


def test_falha_na_carteira_e_nomeada(monkeypatch):
    def cai(*a, **k):
        raise RuntimeError("túnel fora do ar")

    monkeypatch.setattr(cm, "bloco_contexto_mercado", cai)
    texto = cm.conjuntura_da_carteira("b3", [{"ticker": "PETR4"}])
    assert "túnel fora do ar" in texto and "ausência de notícias" in texto


def test_empresa_usa_recorte_de_um_ativo_e_nomeia_falha(monkeypatch):
    import core.conjuntura as conj

    visto: dict = {}

    def bloco(**k):
        visto.update(k)
        return "RECORTE"

    monkeypatch.setattr(conj, "bloco_para_prompt", bloco)
    assert cm.conjuntura_da_empresa("b3", "petr4", "Petróleo") == "RECORTE"
    assert visto["ativos"] == {"PETR4": "Petróleo"} and visto["asset_class"] == "b3"
    assert cm.conjuntura_da_empresa("b3", "", "x") == ""

    def cai(**k):
        raise OSError("connection refused")

    monkeypatch.setattr(conj, "bloco_para_prompt", cai)
    texto = cm.conjuntura_da_empresa("us", "MSFT", None)
    assert "connection refused" in texto and "MSFT" in texto

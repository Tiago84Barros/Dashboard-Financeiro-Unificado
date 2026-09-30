"""Relatórios institucionais de Avaliação de Portfólio recebem o detalhe do armazém.

O chat da carteira B3 e o chat das empresas americanas já liam liquidez, preço,
proventos e trimestres do armazém para o ativo citado; a nota institucional da
mesma empresa, gerada na tela vizinha, não lia. As duas leituras divergiam sem
motivo visível.

O que se prende aqui: o texto do leitor chega ao prompt da empresa com a regra
de uso ao lado; sem detalhe, o prompt diz que ele faltou em vez de calar; e as
telas pedem o detalhe do ativo avaliado. Nada aqui toca rede nem banco.
"""
from __future__ import annotations

import inspect
import json

import pandas as pd
import pytest

import core.portfolio_report_b3 as rb3
import core.portfolio_report_us as rus

_MARCA = "DETALHE-SINTETICO liquidez mediana R$ 12,3 mi/dia (400 pregões)"
_FALTOU = "Detalhe do armazém não montado nesta execução"


def _captura(monkeypatch, modulo) -> dict:
    capturado: dict[str, str] = {}

    def falso(prompt: str, model: str | None = None) -> str:
        capturado["prompt"] = prompt
        return json.dumps({}, ensure_ascii=False)

    monkeypatch.setattr(modulo, "_call_llm", falso)
    return capturado


@pytest.mark.parametrize("detalhe,esperado", [(_MARCA, _MARCA), ("", _FALTOU)])
def test_nota_b3_leva_o_detalhe(monkeypatch, detalhe, esperado):
    dossie = {"identificacao": {"nome": "Teste", "setor": "Petróleo",
                                "subsetor": "S", "segmento": "G"},
              "series_anuais": []}
    monkeypatch.setattr(rb3, "build_dossie", lambda tk: dossie)
    monkeypatch.setattr(rb3, "build_peer_context", lambda *a, **k: "PARES")
    cap = _captura(monkeypatch, rb3)
    rb3.generate_company_portfolio_report("TEST3", df_fin=pd.DataFrame(),
                                          detalhe_armazem=detalhe)
    prompt = cap["prompt"]
    assert "=== DETALHE DO ARMAZÉM LOCAL" in prompt and esperado in prompt
    assert "é lacuna, não\n   dado zero nem risco" in prompt


@pytest.mark.parametrize("detalhe,esperado", [(_MARCA, _MARCA), ("", _FALTOU)])
def test_nota_eua_leva_o_detalhe(monkeypatch, detalhe, esperado):
    dossie = {"name": "Test Inc", "sector": "Energy", "industry": "Oil"}
    monkeypatch.setattr(rus, "build_dossie", lambda tk: dossie)
    monkeypatch.setattr(rus, "dossie_to_text", lambda d: "DOSSIE")
    monkeypatch.setattr(rus, "build_peer_context", lambda *a, **k: "PARES")
    cap = _captura(monkeypatch, rus)
    rus.generate_company_us_report("TST", df_fin=pd.DataFrame(), detalhe_armazem=detalhe)
    prompt = cap["prompt"]
    assert "=== DETALHE DO ARMAZÉM LOCAL" in prompt and esperado in prompt
    assert "Fonte que o bloco declara indisponível é lacuna" in prompt


def test_detalhe_vem_depois_da_conjuntura_e_antes_do_suplementar(monkeypatch):
    """O bloco suplementar é o último antes das regras; o detalhe não o desloca."""
    for texto in (rb3._PROMPT_COMPANY_PORTFOLIO, rus._PROMPT_COMPANY_PORTFOLIO):
        conj = texto.index("{conjuntura}")
        det = texto.index("{detalhe_armazem}")
        sup = texto.index("{portfolio_context}")
        assert conj < det < sup


def test_telas_pedem_o_detalhe_do_ativo_avaliado():
    from views import analise_portfolio_b3, analise_portfolio_us

    fonte_b3 = inspect.getsource(analise_portfolio_b3._executar_analise)
    assert "detalhe_armazem=get_b3_warehouse_detail([tk])" in fonte_b3
    assert "from core.llm_context_b3 import" in fonte_b3

    fonte_us = inspect.getsource(analise_portfolio_us._executar_analise)
    assert "detalhe_armazem=get_us_warehouse_detail([tk])" in fonte_us
    assert "from core.llm_context_us import" in fonte_us

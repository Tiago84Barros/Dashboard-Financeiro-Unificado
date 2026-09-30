"""Relatórios institucionais de Avaliação de Portfólio recebem o detalhe do armazém.

O chat da carteira B3 e o chat das empresas americanas já liam liquidez, preço,
proventos e trimestres do armazém para o ativo citado; a nota institucional da
mesma empresa, gerada na tela vizinha, não lia. As duas leituras divergiam sem
motivo visível.

O que se prende aqui: o texto do leitor chega ao prompt da empresa com a regra
de uso ao lado; sem detalhe, o prompt diz que ele faltou em vez de calar; e as
telas pedem o detalhe do ativo avaliado. O consolidado recebe o detalhe já
lido dos ativos de maior peso e nomeia os que ficaram de fora. Nada aqui toca rede nem banco.
"""
from __future__ import annotations

import inspect
import json

import pandas as pd
import pytest

import core.portfolio_report_b3 as rb3
import core.portfolio_report_common as common
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
    assert "get_b3_warehouse_detail([tk])" in fonte_b3
    assert "from core.llm_context_b3 import" in fonte_b3

    fonte_us = inspect.getsource(analise_portfolio_us._executar_analise)
    assert "get_us_warehouse_detail([tk])" in fonte_us
    assert "from core.llm_context_us import" in fonte_us


# ── Consolidado ──────────────────────────────────────────────────────────────

def _itens(*pares):
    return [{"ticker": tk, "peso_pct": peso, "analise": {}} for tk, peso in pares]


def test_consolidado_pega_os_de_maior_peso_e_nomeia_os_de_fora():
    detalhes = {tk: f"bloco {tk}" for tk in ("A3", "B3X", "C3", "D3")}
    itens = _itens(("A3", 10), ("B3X", 40), ("C3", 30), ("D3", 20))
    texto = common.detalhe_do_consolidado(detalhes, itens, limite=2)
    assert texto.startswith("Detalhe dos 2 ativos de maior peso com leitura do armazém (B3X, C3).")
    assert "Fora deste bloco: D3, A3" in texto and "não significa dado ausente" in texto
    assert "bloco B3X" in texto and "bloco C3" in texto and "bloco A3" not in texto


def test_ativo_sem_detalhe_nao_ocupa_vaga():
    itens = _itens(("A3", 50), ("B3X", 30), ("C3", 20))
    texto = common.detalhe_do_consolidado({"B3X": "bloco B3X", "C3": "bloco C3"},
                                          itens, limite=2)
    assert "(B3X, C3)" in texto and "Fora deste bloco: A3" in texto


def test_sem_nenhum_detalhe_o_consolidado_diz_que_faltou(monkeypatch):
    assert common.detalhe_do_consolidado({}, _itens(("A3", 1))) == ""
    cap = _captura(monkeypatch, rb3)
    rb3.analyze_portfolio_report(_itens(("A3", 100)), None)
    assert _FALTOU in cap["prompt"]


@pytest.mark.parametrize("modulo,funcao", [(rb3, "analyze_portfolio_report"),
                                           (rus, "analyze_us_portfolio_report")])
def test_consolidado_leva_o_detalhe_com_a_regra(monkeypatch, modulo, funcao):
    cap = _captura(monkeypatch, modulo)
    getattr(modulo, funcao)(_itens(("A3", 100)), None, detalhe_armazem=_MARCA)
    prompt = cap["prompt"]
    assert "=== DETALHE DO ARMAZÉM LOCAL DOS ATIVOS DE MAIOR PESO" in prompt
    assert _MARCA in prompt
    corrido = " ".join(prompt.split())
    assert "Ativo fora do bloco" in corrido and "não dado zero" in corrido


def test_telas_passam_ao_consolidado_o_detalhe_ja_lido():
    """Nenhuma leitura nova: o consolidado reaproveita o texto de cada nota."""
    from views import analise_portfolio_b3, analise_portfolio_us

    for tela in (analise_portfolio_b3, analise_portfolio_us):
        fonte = inspect.getsource(tela._executar_analise)
        assert "detalhes.setdefault(" in fonte
        assert "detalhe_armazem=detalhe_do_consolidado(detalhes, items_analisados)" in fonte
        assert fonte.count("warehouse_detail([") == 1


# --- Campo próprio no esquema ------------------------------------------------
# Medido com LLM real em 30/09/2026 (7 ações, 195 números no bloco): sem campo
# próprio o consolidado citou 0–9 números do armazém e inventou uma
# "volatilidade ponderada"; com "comportamento_de_mercado", 44–56, todos no campo.

@pytest.mark.parametrize("modulo,funcao", [(rb3, "analyze_portfolio_report"),
                                           (rus, "analyze_us_portfolio_report")])
def test_consolidado_pede_campo_proprio_para_os_numeros(monkeypatch, modulo, funcao):
    cap = _captura(monkeypatch, modulo)
    getattr(modulo, funcao)(_itens(("A3", 100)), None, detalhe_armazem=_MARCA)
    corrido = " ".join(cap["prompt"].split())
    assert 'Os números desse bloco vão em "comportamento_de_mercado"' in corrido
    assert '"comportamento_de_mercado": "números do DETALHE DO ARMAZÉM' in corrido


@pytest.mark.parametrize("modulo,funcao", [(rb3, "analyze_portfolio_report"),
                                           (rus, "analyze_us_portfolio_report")])
def test_campo_da_llm_chega_ao_relatorio(monkeypatch, modulo, funcao):
    texto = "A3 (pregão 28/09/2026): R$ 12,3 mi/dia; volatilidade 22,9%."
    monkeypatch.setattr(modulo, "_call_llm", lambda prompt, model=None: json.dumps(
        {"comportamento_de_mercado": texto}, ensure_ascii=False))
    rel = getattr(modulo, funcao)(_itens(("A3", 100)), None, detalhe_armazem=_MARCA)
    assert rel["comportamento_de_mercado"] == texto


def test_campo_estruturado_vira_texto_e_ausente_vira_vazio():
    assert common.fallback_portfolio()["comportamento_de_mercado"] == ""
    rel = common.sanitize_portfolio_report(
        {"comportamento_de_mercado": {"A3": "R$ 1 mi/dia", "B3X": ["vol 20%", "1m +2%"]}},
        _itens(("A3", 50), ("B3X", 50)))
    assert rel["comportamento_de_mercado"] == "A3: R$ 1 mi/dia B3X: vol 20% 1m +2%"
    assert common.sanitize_portfolio_report({}, _itens(("A3", 1)))["comportamento_de_mercado"] == ""


def test_telas_mostram_o_campo():
    from views import analise_portfolio_b3, analise_portfolio_us

    for tela in (analise_portfolio_b3, analise_portfolio_us):
        fonte = inspect.getsource(tela)
        assert '"comportamento_de_mercado"' in fonte
        assert "Liquidez, retorno e volatilidade (armazém)" in fonte


# Nota individual: mesma lição, medida em 30/09/2026 com LLM real. TAEE11 foi de
# 0/30 números do bloco citados (4 rodadas) para 9–25/30; KO, de 2–7/44 para
# 14–39/44 — quase tudo em relatorio.comportamento_de_mercado.

def _nota_b3(monkeypatch, resposta: dict | None = None) -> tuple[dict, str]:
    dossie = {"identificacao": {"nome": "Teste", "setor": "S", "subsetor": "S",
                                "segmento": "G"}, "series_anuais": []}
    monkeypatch.setattr(rb3, "build_dossie", lambda tk: dossie)
    monkeypatch.setattr(rb3, "build_peer_context", lambda *a, **k: "PARES")
    cap: dict[str, str] = {}

    def falso(prompt, model=None):
        cap["prompt"] = prompt
        return json.dumps(resposta or {}, ensure_ascii=False)

    monkeypatch.setattr(rb3, "_call_llm", falso)
    rel, _ = rb3.generate_company_portfolio_report("TEST3", df_fin=pd.DataFrame(),
                                                   detalhe_armazem=_MARCA)
    return rel, cap["prompt"]


def _nota_eua(monkeypatch, resposta: dict | None = None) -> tuple[dict, str]:
    monkeypatch.setattr(rus, "build_dossie", lambda tk: {"name": "T", "industry": "I"})
    monkeypatch.setattr(rus, "dossie_to_text", lambda d: "DOSSIE")
    monkeypatch.setattr(rus, "build_peer_context", lambda *a, **k: "PARES")
    cap: dict[str, str] = {}

    def falso(prompt, model=None):
        cap["prompt"] = prompt
        return json.dumps(resposta or {}, ensure_ascii=False)

    monkeypatch.setattr(rus, "_call_llm", falso)
    rel, _ = rus.generate_company_us_report("TST", df_fin=pd.DataFrame(),
                                            detalhe_armazem=_MARCA)
    return rel, cap["prompt"]


@pytest.mark.parametrize("nota", [_nota_b3, _nota_eua])
def test_nota_pede_campo_proprio_para_os_numeros(monkeypatch, nota):
    _, prompt = nota(monkeypatch)
    corrido = " ".join(prompt.split())
    assert "Os números desse bloco vão em relatorio.comportamento_de_mercado" in corrido
    assert "Não troque por estimativa de cabeça." in corrido
    assert '"comportamento_de_mercado": "números do DETALHE DO ARMAZÉM' in corrido


@pytest.mark.parametrize("nota", [_nota_b3, _nota_eua])
def test_campo_da_nota_chega_ao_relatorio_como_texto(monkeypatch, nota):
    texto = "Pregão 28/09/2026: R$ 58,5 mi/dia; volatilidade 22,9%."
    rel, _ = nota(monkeypatch, {"relatorio": {"comportamento_de_mercado": texto}})
    assert rel["relatorio"]["comportamento_de_mercado"] == texto
    rel, _ = nota(monkeypatch, {"relatorio": {"comportamento_de_mercado":
                                              {"liquidez": "R$ 1 mi/dia", "retornos": ["1m +2%"]}}})
    assert rel["relatorio"]["comportamento_de_mercado"] == "liquidez: R$ 1 mi/dia retornos: 1m +2%"


def test_telas_mostram_o_campo_da_nota():
    from views import analise_portfolio_b3, analise_portfolio_us

    assert ('("comportamento_de_mercado", "Liquidez, retorno e reação a resultados (armazém)")'
            in inspect.getsource(analise_portfolio_b3))
    assert ('("comportamento_de_mercado", "Preço, trimestres e proventos (armazém)")'
            in inspect.getsource(analise_portfolio_us))

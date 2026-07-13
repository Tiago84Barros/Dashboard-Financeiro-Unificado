"""
Testes do dossiê determinístico (core/dossie_b3) e do gate qualitativo de
seleção (views/portfolio_b3._aplicar_gate_qualitativo).

Nada aqui toca banco ou LLM: as partes determinísticas são funções puras e o
gate é testado com o cache de pareceres pré-populado em st.session_state.
"""
from __future__ import annotations

import streamlit as st

from core.dossie_b3 import (
    _checks,
    _sanitizar_parecer,
    _sensibilidade_juros,
    _valuation,
)


# ─────────────────────────────────────────────────────────────────────────────
# Sensibilidade a juros — a regra é decidida pelos dados, não pelo LLM
# ─────────────────────────────────────────────────────────────────────────────

def test_sensibilidade_juros_caixa_liquido():
    serie = [{"ano": 2025, "div_liq_mi": -424.7, "pl_mi": 1166.1}]
    sj = _sensibilidade_juros(serie)
    assert sj["posicao"] == "caixa_liquido"
    assert "AUMENTA o lucro" in sj["regra"]


def test_sensibilidade_juros_endividada():
    serie = [{"ano": 2025, "div_liq_mi": 800.0, "pl_mi": 1000.0}]
    sj = _sensibilidade_juros(serie)
    assert sj["posicao"] == "endividada"
    assert sj["div_liq_pl"] == 0.8


def test_sensibilidade_juros_sem_dado():
    assert _sensibilidade_juros([])["posicao"] == "indefinida"


# ─────────────────────────────────────────────────────────────────────────────
# Red flags determinísticas
# ─────────────────────────────────────────────────────────────────────────────

def _serie_base():
    return [
        {"ano": 2024, "pl_mi": 1315.1, "lucro_mi": 225.5, "fco_mi": 1.0,
         "ebitda_mi": 2.0},
        {"ano": 2025, "pl_mi": 1166.1, "lucro_mi": 235.3, "fco_mi": 1.0,
         "ebitda_mi": 2.0},
    ]


def test_check_pl_em_queda_com_lucro_positivo():
    flags = _checks(_serie_base(), {}, {}, {}, {"n_docs": 3}, {})
    assert any("PATRIMÔNIO EM QUEDA" in f for f in flags)


def test_check_duplicacao_dividendos():
    divs = {"suspeita_duplicacao_classe": True,
            "dy_12m_bruto_pct": 36.0, "dy_12m_pct": 17.0}
    flags = _checks(_serie_base(), {}, divs, {}, {"n_docs": 3}, {})
    assert any("duplicação por classe" in f for f in flags)


def test_check_momentum_queda_lucro():
    tris = {"yoy": {"lucro_yoy_pct": -36.3, "ref": "2026T1 vs 2025T1"}}
    flags = _checks(_serie_base(), tris, {}, {}, {"n_docs": 3}, {})
    assert any("MOMENTUM" in f for f in flags)


def test_check_cobertura_sem_docs_e_sem_fco():
    serie = [{"ano": 2025, "pl_mi": 1.0, "lucro_mi": 1.0, "fco_mi": None,
              "ebitda_mi": None}]
    flags = _checks(serie, {}, {}, {}, {"n_docs": 0}, {})
    assert any("fluxo de caixa" in f for f in flags)
    assert any("EBITDA" in f for f in flags)
    assert any("documento CVM" in f for f in flags)


# ─────────────────────────────────────────────────────────────────────────────
# Valuation calculado dos dados brutos
# ─────────────────────────────────────────────────────────────────────────────

def test_valuation_calculada():
    serie = [{"ano": 2025, "lucro_mi": 235.3, "pl_mi": 1166.1,
              "ebit_mi": 228.4, "div_liq_mi": -424.7}]
    v = _valuation(1_910_000_000.0, serie, {"preco": 25.0})
    assert v["pl_calc"] == 8.1
    assert v["pvp_calc"] == 1.6
    assert v["ev_ebit_calc"] == 6.5
    assert v["ano_base_valuation"] == 2025


def test_valuation_sem_mcap_nao_quebra():
    v = _valuation(None, [], {"preco": None})
    assert "pl_calc" not in v


# ─────────────────────────────────────────────────────────────────────────────
# Sanitização do parecer LLM
# ─────────────────────────────────────────────────────────────────────────────

def test_sanitizar_parecer_classificacao_invalida_vira_ressalva():
    p = _sanitizar_parecer({"classificacao_selecao": "explodir"}, "XXXX3")
    assert p["classificacao_selecao"] == "aprovar_com_ressalvas"
    assert isinstance(p["relatorio"], dict)


def test_sanitizar_parecer_preserva_veto_valido():
    p = _sanitizar_parecer({"classificacao_selecao": "vetar",
                            "motivo_selecao": "grave"}, "XXXX3")
    assert p["classificacao_selecao"] == "vetar"


# ─────────────────────────────────────────────────────────────────────────────
# Gate de seleção — veto com substituição (sem LLM: cache pré-populado)
# ─────────────────────────────────────────────────────────────────────────────

def _gate(selecionados, ranked, entry_guard, pesos, log):
    from views.portfolio_b3 import _aplicar_gate_qualitativo
    return _aplicar_gate_qualitativo(selecionados, ranked, entry_guard,
                                     pesos, "Seg Teste", log)


def _prime_cache():
    st.session_state["pb3_quali_cache"] = {
        "AAAA3": {"classificacao": "vetar", "motivo": "controlador drenando caixa"},
        "BBBB3": {"classificacao": "aprovar", "motivo": ""},
        "CCCC3": {"classificacao": "aprovar_com_ressalvas", "motivo": "payout > 100%"},
        "DDDD3": {"classificacao": "vetar", "motivo": "dados insuficientes"},
    }


def test_gate_veto_substitui_pelo_proximo_do_ranking():
    _prime_cache()
    ranked = [("AAAA3", 90.0), ("BBBB3", 80.0), ("CCCC3", 70.0)]
    pesos = {"AAAA3": 0.6, "CCCC3": 0.4}
    log = {"vetados": [], "substituicoes": [], "ressalvas": {}}
    finais = _gate(["AAAA3", "CCCC3"], ranked, {}, pesos, log)
    assert finais == ["BBBB3", "CCCC3"]
    # substituto herda o orçamento de peso do vetado
    assert pesos["BBBB3"] == 0.6
    assert log["vetados"][0]["tk"] == "AAAA3"
    assert log["substituicoes"][0]["entra"] == "BBBB3"
    assert "CCCC3" in log["ressalvas"]


def test_gate_sem_substituto_deixa_vaga_vazia():
    _prime_cache()
    log = {"vetados": [], "substituicoes": [], "ressalvas": {}}
    finais = _gate(["DDDD3"], [("DDDD3", 60.0), ("AAAA3", 50.0)],
                   {}, {"DDDD3": 1.0}, log)
    assert finais == []
    assert len(log["vetados"]) == 2  # vetado original + candidato também vetado


def test_gate_respeita_entry_guard_do_substituto():
    _prime_cache()
    ranked = [("AAAA3", 90.0), ("BBBB3", 80.0), ("CCCC3", 70.0)]
    eg = {"BBBB3": {"status_entrada": "Excluido", "score_entrada": 10.0}}
    log = {"vetados": [], "substituicoes": [], "ressalvas": {}}
    finais = _gate(["AAAA3"], ranked, eg, {"AAAA3": 1.0}, log)
    assert finais == ["CCCC3"]


def test_gate_falha_de_avaliacao_nao_veta():
    # fail-open: parecer indisponível chega como aprovar_com_ressalvas
    st.session_state["pb3_quali_cache"] = {
        "EEEE3": {"classificacao": "aprovar_com_ressalvas",
                  "motivo": "avaliação indisponível (timeout)"},
    }
    log = {"vetados": [], "substituicoes": [], "ressalvas": {}}
    finais = _gate(["EEEE3"], [("EEEE3", 50.0)], {}, {"EEEE3": 1.0}, log)
    assert finais == ["EEEE3"]
    assert not log["vetados"]

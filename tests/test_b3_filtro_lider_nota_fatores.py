"""Filtro nota + fatores na rota "Líder de cada segmento".

A regra (`core.b3_nota_fatores.trocar_piores`) roda em dois caminhos: a tela
(views/portfolio_b3.py) e a medição fora da amostra
(scripts/medir_lider_filtro_nota_fatores_b3.py). Estes testes fixam a regra,
as barreiras das trocas seguintes e a equivalência entre os caminhos.
"""
import numpy as np
import pandas as pd
import pytest
import streamlit as st

import core.b3_nota_fatores as nf
import views.portfolio_b3_nota_fatores as v
from scripts.medir_lider_filtro_nota_fatores_b3 import (
    aplicar_filtro_na_safra,
    decidir,
    diferenca_pareada,
)

RANKING = [("AAAA3", 90.0), ("BBBB3", 80.0), ("CCCC3", 70.0), ("DDDD3", 60.0)]


# ── piores_da_tabela ────────────────────────────────────────────────────────

def test_piores_da_tabela_sao_os_que_nao_ficam():
    tab = pd.DataFrame({"fica": [True, False, True, False]},
                       index=["A", "D", "B", "C"])
    assert nf.piores_da_tabela(tab) == ["C", "D"]


@pytest.mark.parametrize("tab", [None, pd.DataFrame(),
                                 pd.DataFrame({"sinal": [1.0]}, index=["A"])])
def test_piores_da_tabela_sem_tabela_e_vazio(tab):
    assert nf.piores_da_tabela(tab) == []


def test_piores_da_tabela_bate_com_a_carteira():
    idx = pd.date_range("2022-01-31", periods=48, freq="ME")
    tickers = list("ABCDEFGHIJ")
    precos = pd.DataFrame({t: 100 * (1 + 0.002 * k) ** np.arange(48)
                           for k, t in enumerate(tickers)}, index=idx)
    out = nf.carteira_nota_fatores(tickers, {t: float(k) for k, t in enumerate(tickers)},
                                   precos, {}, pd.Timestamp("2025-04-01"))
    piores = set(nf.piores_da_tabela(out["tabela"]))
    assert len(piores) == int(np.floor(nf.CORTE * len(tickers)))
    assert piores.isdisjoint(out["pesos"])
    assert piores | set(out["pesos"]) == set(tickers)


# ── trocar_piores ───────────────────────────────────────────────────────────

def test_troca_pelo_proximo_do_segmento_e_herda_o_peso():
    pesos = {"AAAA3": 0.6, "CCCC3": 0.4}
    finais, pesos_out, trocas = nf.trocar_piores(["AAAA3", "CCCC3"], RANKING, pesos,
                                                 {"AAAA3"})
    assert finais == ["BBBB3", "CCCC3"]
    assert pesos_out["BBBB3"] == 0.6
    assert trocas == [{"sai": "AAAA3", "entra": "BBBB3"}]
    assert pesos == {"AAAA3": 0.6, "CCCC3": 0.4}  # não muda o que recebeu


def test_substituto_pula_piores_e_barrados():
    finais, _p, trocas = nf.trocar_piores(
        ["AAAA3"], RANKING, {"AAAA3": 1.0}, {"AAAA3", "BBBB3"},
        exclui=lambda tk: tk == "CCCC3")
    assert finais == ["DDDD3"]
    assert trocas == [{"sai": "AAAA3", "entra": "DDDD3"}]


def test_substituto_ja_com_peso_proprio_mantem_o_dele():
    _f, pesos_out, _t = nf.trocar_piores(["AAAA3"], RANKING,
                                         {"AAAA3": 1.0, "BBBB3": 0.3}, {"AAAA3"})
    assert pesos_out["BBBB3"] == 0.3


def test_sem_substituto_perde_a_vaga():
    finais, _p, trocas = nf.trocar_piores(["AAAA3"], RANKING[:2], {"AAAA3": 1.0},
                                          {"AAAA3", "BBBB3"})
    assert finais == []
    assert trocas == [{"sai": "AAAA3", "entra": None}]


def test_nao_traz_quem_ja_esta_selecionado():
    finais, _p, _t = nf.trocar_piores(["AAAA3", "BBBB3"], RANKING,
                                      {"AAAA3": 0.5, "BBBB3": 0.5}, {"AAAA3"})
    assert finais == ["CCCC3", "BBBB3"]


def test_sem_piores_nada_muda():
    sel, pesos = ["AAAA3", "CCCC3"], {"AAAA3": 0.6, "CCCC3": 0.4}
    finais, pesos_out, trocas = nf.trocar_piores(sel, RANKING, pesos, set())
    assert finais == sel and pesos_out == pesos and trocas == []


# ── medição: o mesmo passo, sem mudar a safra recebida ─────────────────────

def _safra(segmentos, guarda=lambda _tk: False):
    return {"safra": 2020, "segmentos": segmentos, "exclui": guarda, "universo": []}


def test_medicao_com_piores_vazios_e_igual_ao_controle():
    segs = [("Fin", ["AAAA3"], {"AAAA3": 1.0}, RANKING)]
    p = _safra(segs)
    q = aplicar_filtro_na_safra(p, [], nf.trocar_piores)
    assert q["segmentos"] == segs
    assert all(q["exclui"](t) == p["exclui"](t) for t, _ in RANKING)
    assert q["filtro"]["trocas"] == []


def test_medicao_troca_tira_segmento_vazio_e_barra_os_piores_depois():
    segs = [("Fin", ["AAAA3"], {"AAAA3": 1.0}, RANKING),
            ("Ene", ["EEEE3"], {"EEEE3": 1.0}, [("EEEE3", 1.0)])]
    p = _safra(segs, guarda=lambda tk: tk == "BBBB3")
    q = aplicar_filtro_na_safra(p, ["AAAA3", "EEEE3"], nf.trocar_piores,
                                reprovado_no_piso={"CCCC3"}.__contains__)
    assert [s[1] for s in q["segmentos"]] == [["DDDD3"]]
    assert q["filtro"]["segmentos_perdidos"] == 1
    assert q["filtro"]["vagas_perdidas"] == 1
    # As trocas seguintes da medição (banda, montagem) também pulam os piores.
    assert q["exclui"]("AAAA3") and q["exclui"]("EEEE3") and q["exclui"]("BBBB3")
    # O piso barra só o substituto desta troca, como na tela.
    assert not q["exclui"]("CCCC3")
    assert p["segmentos"] is segs and len(segs) == 2  # recebida intacta


def test_diferenca_pareada_so_nas_safras_das_duas():
    c = [{"safra": 2018, "liquido": 0.10}, {"safra": 2019, "liquido": 0.05},
         {"safra": 2020, "liquido": None}]
    f = [{"safra": 2019, "liquido": 0.08}, {"safra": 2020, "liquido": 0.01},
         {"safra": 2021, "liquido": 0.02}]
    assert diferenca_pareada(c, f, "liquido") == [
        {"safra": 2019, "dif": pytest.approx(0.03)}]


@pytest.mark.parametrize("resumo,esperado", [
    ({"media": 0.02, "ic95": [0.005, 0.04]}, True),
    ({"media": 0.02, "ic95": [-0.01, 0.04]}, False),
    ({"media": -0.01, "ic95": [-0.03, -0.001]}, False),
    ({"media": None, "ic95": [None, None]}, False),
])
def test_regra_de_decisao_pre_registrada(resumo, esperado):
    assert decidir(resumo) is esperado


# ── tela ────────────────────────────────────────────────────────────────────

def test_piores_de_hoje_sem_carteira_e_none():
    assert v.piores_de_hoje(None) is None
    assert v.piores_de_hoje({"tabela": pd.DataFrame()}) is None
    tab = pd.DataFrame({"fica": [True, False]}, index=["A", "B"])
    assert v.piores_de_hoje({"tabela": tab}) == frozenset({"B"})


def test_texto_da_medicao_sem_json_diz_que_falta_medir():
    assert "Ainda não medido" in v.texto_medicao_filtro(None, [])
    txt = v.texto_medicao_filtro(
        {"medido_em": "2026-11-23T10:00:00", "decisao": "fica opcional e desligado",
         "diferenca_pareada": {"liquido": {"media": 0.012, "ic95": [-0.01, 0.03],
                                           "n_safras": 8}}}, [])
    assert "+1.2 pp" in txt and "8 safras" in txt and "fica opcional" in txt


def test_barreira_extra_vale_no_gate_e_na_inteligencia():
    from views.portfolio_b3 import _aplicar_gate_qualitativo, _barrado
    assert _barrado({}, "AAAA3", {"AAAA3"}.__contains__)
    assert not _barrado({}, "AAAA3", None)
    st.session_state["pb3_quali_cache"] = {
        "AAAA3": {"classificacao": "vetar", "motivo": "x"},
        "BBBB3": {"classificacao": "aprovar", "motivo": ""},
        "CCCC3": {"classificacao": "aprovar", "motivo": ""},
    }
    log = {"vetados": [], "substituicoes": [], "ressalvas": {}}
    finais = _aplicar_gate_qualitativo(["AAAA3"], RANKING, {}, {"AAAA3": 1.0}, "Seg",
                                       log, exclui_extra={"BBBB3"}.__contains__)
    assert finais == ["CCCC3"]


def test_reprovados_do_piso_sem_retrato_e_vazio():
    from core.b3_quality_floor import reprovados
    assert reprovados(None, ["A"]) == frozenset()
    assert reprovados(pd.DataFrame(), ["A"]) == frozenset()

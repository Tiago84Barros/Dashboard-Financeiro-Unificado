"""Eventos de capital do núcleo puro das saídas da B3 (data_pipeline/market/b3_saidas.py).

O caso que motivou: BRPR3 grupou 40:1 em fev/2023 (40 não estava na lista
de fatores redondos e não havia DFP para confirmar) e restituiu capital em
abr/2023 — o índice de retorno total subia 40× num mês e caía 73% no outro.
"""
from __future__ import annotations

import pandas as pd
import pytest

from data_pipeline.market import b3_saidas as bs


def _serie(valores, inicio="2023-01-02", marcas=None):
    idx = pd.bdate_range(inicio, periods=len(valores))
    p = pd.Series([float(v) for v in valores], index=idx)
    m = pd.Series("", index=idx)
    for i, letra in (marcas or {}).items():
        m.iloc[i] = letra
    return p, m


def test_marcador_b3_le_as_letras_depois_do_e():
    assert bs.marcador_b3("ON  EG  NM") == "G"
    assert bs.marcador_b3("ON  ERC NM") == "RC"
    assert bs.marcador_b3("ON  EDB NM") == "DB"
    assert bs.marcador_b3("ON      NM") == ""
    assert bs.marcador_b3("PN  *EJ N1") == "J"
    assert bs.marcador_b3(None) == ""


def test_grupamento_40_para_1_sem_dfp_e_detectado():
    # sem marcador: 40 entra na lista de redondos e o salto exato vira so_preco
    p, _ = _serie([6.0] * 30 + [240.0] * 30)
    ev = bs.detectar_eventos(p, {}, {})
    assert [(e.data, e.fator, e.status) for e in ev] == [(p.index[30], 1 / 40, "so_preco")]


def test_grupamento_com_marcador_eg_aceita_salto_ruidoso():
    # 39,75× com ruído de pregão: longe de 3% do redondo, mas a B3 marcou EG
    p, m = _serie([6.0] * 30 + [238.5] * 30, marcas={30: "G"})
    ev = bs.detectar_eventos(p, {}, {}, marcadores=m)
    assert len(ev) == 1
    assert ev[0].fator == pytest.approx(1 / 40)
    assert ev[0].status == "marcador_b3"


def test_bonificacao_marcada_dentro_da_faixa_normal_entra():
    # bonificação de 20%: o preço cai 1/1,2 — dentro de [0,6; 1,7], antes ignorada
    p, m = _serie([12.0] * 30 + [10.0] * 30, marcas={30: "B"})
    assert bs.detectar_eventos(p, {}, {}) == []
    ev = bs.detectar_eventos(p, {}, {}, marcadores=m)
    assert len(ev) == 1 and ev[0].fator == pytest.approx(1.2)


def test_bonificacao_ruidosa_escolhe_o_fator_que_a_dfp_mostra():
    # preço diz 1,23 (o pregão caiu junto); as ações da DFP cresceram 20%
    p, m = _serie([12.3] * 30 + [10.0] * 30, marcas={30: "B"})
    acoes = {2022: 1_000.0, 2023: 1_200.0}
    av = {2022: p.index[5], 2023: p.index[50]}
    ev = bs.detectar_eventos(p, acoes, av, marcadores=m)
    assert ev[0].fator == pytest.approx(1.2)
    assert ev[0].status == "confirmado"


def test_restituicao_nao_vira_desdobramento():
    # ERC: 234 -> 63,7 parece 1/4 de preço, mas é dinheiro devolvido
    p, m = _serie([234.0] * 30 + [63.7] * 30, marcas={30: "RC"})
    assert bs.detectar_eventos(p, {}, {}, marcadores=m) == []
    dist = bs.detectar_distribuicoes(p, m, [])
    assert len(dist) == 1
    d = dist[0]
    assert d.data == p.index[30] and d.tipo == "restituicao"
    assert d.valor == pytest.approx(234.0 - 63.7)
    assert d.fracao == pytest.approx((234.0 - 63.7) / 63.7)


def test_cisao_e_distribuicao_do_tipo_cisao():
    p, m = _serie([20.0] * 30 + [8.0] * 30, marcas={30: "C"})
    d = bs.detectar_distribuicoes(p, m, [])
    assert [x.tipo for x in d] == ["cisao"]


def test_queda_pequena_no_marcador_nao_vira_distribuicao():
    p, m = _serie([100.0] * 30 + [99.0] * 30, marcas={30: "R"})
    assert bs.detectar_distribuicoes(p, m, []) == []


def test_restituicao_junto_com_grupamento_desconta_o_fator():
    # ERG: grupa 5:1 e devolve 5,8% no mesmo dia
    p, m = _serie([76.44] * 30 + [360.0] * 30, marcas={30: "RG"})
    ev = bs.detectar_eventos(p, {}, {}, marcadores=m)
    assert [e.fator for e in ev] == [pytest.approx(0.2)]
    d = bs.detectar_distribuicoes(p, m, ev)
    assert len(d) == 1
    assert d[0].valor == pytest.approx(76.44 / 0.2 - 360.0)


def test_indice_de_retorno_total_atravessa_grupamento_e_restituicao_sem_saltar():
    # jan 6; fev grupa 40:1 (240); abr devolve 176,3 por ação (240 -> 63,7)
    dias = pd.bdate_range("2023-01-02", "2023-05-31")
    p = pd.Series(6.0, index=dias)
    p[dias >= "2023-02-24"] = 240.0
    p[dias >= "2023-04-05"] = 63.7
    m = pd.Series("", index=dias)
    m[pd.Timestamp("2023-02-24")] = "G"
    m[pd.Timestamp("2023-04-05")] = "RC"
    ev = bs.detectar_eventos(p, {}, {}, marcadores=m)
    dist = bs.detectar_distribuicoes(p, m, ev)
    tr = bs.retorno_total_mensal(p, ev, {}, dist)
    razoes = (tr / tr.shift(1)).dropna()
    assert razoes.max() < 1.01 and razoes.min() > 0.99


def test_lucro_por_lote_de_mil_e_corrigido():
    acoes = {2010: 120_000.0, 2011: 121_000.0, 2012: 1.2e8, 2013: 1.21e8, 2014: 1.22e8}
    corr, anos = bs.corrigir_lote_de_mil(acoes)
    assert anos == [2010, 2011]
    assert corr[2010] == pytest.approx(1.2e8)
    assert corr[2012] == acoes[2012]


def test_queda_de_cem_vezes_nao_e_lote_de_mil():
    # grupamento 100:1 de verdade (OGX) não pode ser "corrigido"
    acoes = {2014: 3.2e9, 2015: 3.2e9, 2016: 3.2e7, 2017: 3.2e7, 2018: 3.3e7}
    corr, anos = bs.corrigir_lote_de_mil(acoes)
    assert anos == [] and corr == acoes


def test_saltos_residuais_lista_meses_fora_de_3x():
    idx = pd.period_range("2020-01", periods=4, freq="M")
    tr = pd.Series([10.0, 11.0, 40.0, 12.0], index=idx)
    assert bs.saltos_residuais(tr) == [
        {"mes": "2020-03", "razao": pytest.approx(40 / 11)},
        {"mes": "2020-04", "razao": pytest.approx(12 / 40)},
    ]


def test_queda_para_metade_marcada_eb_e_desdobramento_e_nao_bonificacao_de_95():
    # BRML3 2010: 0,507 — o pregão subiu junto; 1:2, não "bonificação de 95%"
    p, m = _serie([20.0] * 30 + [10.14] * 30, marcas={30: "B"})
    ev = bs.detectar_eventos(p, {}, {}, marcadores=m)
    assert ev[0].fator == pytest.approx(2.0)


def test_grupamento_com_primeiro_pregao_ralo_usa_a_dfp():
    # MMXM3 2016: o dia sobe 21×, a semana 23×; a DFP mostra 25:1
    p, m = _serie([0.28] * 30 + [6.0, 6.66, 6.57, 6.6, 6.5] + [6.57] * 25, marcas={30: "G"})
    acoes = {2015: 1.62e8, 2020: 6.48e6}
    av = {2015: p.index[2], 2020: p.index[55]}
    ev = bs.detectar_eventos(p, acoes, av, marcadores=m)
    assert ev[0].fator == pytest.approx(1 / 25) and ev[0].status == "confirmado"

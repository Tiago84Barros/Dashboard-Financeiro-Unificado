"""Camada determinística da análise de carteira (core/inteligencia_ativos/calculos.py).

A carteira de referência soma R$ 10.000 em valor de mercado, com números
escolhidos para que todo resultado possa ser conferido de cabeça:

    ITUB4  Ações BR  Financeiro  2.000  → 20%
    BBAS3  Ações BR  Financeiro  1.000  → 10%
    PETR4  Ações BR  Petróleo    1.500  → 15%
    PETR3  Ações BR  Petróleo      500  →  5%
    HGLG11 FII       Logística   1.000  → 10%
    TIPCA2035 Tesouro IPCA+      2.000  → 20%
    CDB Banco X 110% CDI         1.000  → 10%
    AAPL   (USD)     Tecnologia  1.000  → 10%

Classes: ações BR 50, FIIs 10, renda fixa 30, exterior 10.
Renda variável (ações BR + FIIs) = 60.
"""
import math

import pandas as pd
import pytest

from core.global_portfolio.concentration import hhi as hhi_global
from core.inteligencia_ativos import analise, calculos as c, contexto
from core.inteligencia_ativos import modelos as m
from tests.test_inteligencia_ativos_adequacao import _registro


def _pos(ticker, classe, valor, setor=None, moeda="BRL", nome=None, pct=None):
    return {"ticker": ticker, "nome": nome or ticker, "classe": classe,
            "setor": setor, "moeda": moeda, "valor_mercado": valor,
            "total_investido": valor * 0.9,
            # pct_carteira propositalmente errado: o peso é recalculado
            "pct_carteira": 99.0 if pct is None else pct}


POSICOES = [
    _pos("ITUB4", "Ações BR", 2000, "Financeiro"),
    _pos("BBAS3", "Ações BR", 1000, "Financeiro"),
    _pos("PETR4", "Ações BR", 1500, "Petróleo"),
    _pos("PETR3", "Ações BR", 500, "Petróleo"),
    _pos("HGLG11", "FII", 1000, "Logística"),
    _pos("TIPCA2035", "Tesouro Direto", 2000, nome="Tesouro IPCA+ 2035"),
    _pos("CDB-BANCOX", "Renda Fixa", 1000, nome="CDB Banco X 110% CDI"),
    _pos("AAPL", "Ações BR", 1000, "Tecnologia", moeda="USD"),
]

POLITICA = {
    "asset_class_targets": {"renda_fixa": 40, "acoes_br": 30, "fiis": 20,
                            "exterior": 10},
    "asset_class_limits": {"acoes_br": 32},
    "single_asset_limit_pct": 15,
    "sector_limit_pct": 25,
}


def _calc(posicoes=POSICOES, politica=POLITICA, faixas=None):
    return c.calcular(posicoes, politica, faixas)


def _alertas(calc, codigo):
    return [a for a in calc.alertas if a.codigo == codigo]


# -- peso dos ativos ----------------------------------------------------------------

def test_peso_e_valor_de_mercado_sobre_o_total():
    calc = _calc()
    assert calc.total == 10_000 and calc.base_valor == "valor_mercado"
    esperado = {"ITUB4": 20, "BBAS3": 10, "PETR4": 15, "PETR3": 5,
                "HGLG11": 10, "TIPCA2035": 20, "CDB-BANCOX": 10, "AAPL": 10}
    for ticker, peso in esperado.items():
        assert calc.peso(ticker).peso == pytest.approx(peso)
    assert sum(p.peso for p in calc.pesos) == pytest.approx(100.0)
    # do maior para o menor, empate por ticker
    assert [p.ticker for p in calc.pesos[:3]] == ["ITUB4", "TIPCA2035", "PETR4"]


def test_sem_valor_de_mercado_usa_o_investido():
    posicoes = [{"ticker": "A", "classe": "FII", "valor_mercado": 0,
                 "total_investido": 300},
                {"ticker": "B", "classe": "FII", "valor_mercado": None,
                 "total_investido": 100}]
    calc = _calc(posicoes)
    assert calc.base_valor == "total_investido"
    assert calc.peso("A").peso == pytest.approx(75.0)


def test_valor_negativo_entra_com_peso_zero_e_gera_alerta():
    posicoes = [_pos("A", "FII", 100), _pos("B", "FII", -50)]
    calc = _calc(posicoes)
    assert calc.peso("A").peso == pytest.approx(100.0)
    assert calc.peso("B").peso == 0.0
    assert [a.chave for a in _alertas(calc, "valor_invalido")] == ["B"]


def test_carteira_vazia_nao_quebra():
    calc = _calc([])
    assert calc.total == 0 and calc.pesos == ()
    assert calc.concentracao[c.DIM_ATIVO].hhi is None


# -- alocação atual vs alvo, faixas e desvios -------------------------------------------

def test_classe_atual_vs_alvo_com_tolerancia_do_sistema():
    calc = _calc()
    rf = calc.linha(c.DIM_CLASSE, "renda_fixa")
    assert rf.atual == pytest.approx(30)
    assert (rf.faixa.minimo, rf.faixa.alvo, rf.faixa.maximo) == (35, 40, 45)
    assert rf.faixa.origem_minimo == c.SISTEMA
    assert rf.faixa.origem_alvo == c.ESTRATEGIA
    assert rf.diferenca_para_alvo == pytest.approx(-10)
    assert rf.underweight == pytest.approx(5)     # 35 − 30
    assert rf.overweight == 0 and rf.status == c.ABAIXO

    ext = calc.linha(c.DIM_CLASSE, "exterior")
    assert ext.status == c.DENTRO and ext.diferenca_para_alvo == pytest.approx(0)


def test_limite_da_classe_corta_a_faixa_e_vale_o_mais_restritivo():
    acoes = _calc().linha(c.DIM_CLASSE, "acoes_br")
    assert acoes.faixa.maximo == 32 and acoes.faixa.origem_maximo == c.ESTRATEGIA
    assert acoes.overweight == pytest.approx(18)          # 50 − 32
    assert acoes.diferenca_para_alvo == pytest.approx(20)  # 50 − 30


def test_alvo_zero_nao_tem_tolerancia():
    politica = {**POLITICA, "asset_class_targets": {
        "renda_fixa": 50, "acoes_br": 30, "fiis": 20, "exterior": 0}}
    ext = _calc(politica=politica).linha(c.DIM_CLASSE, "exterior")
    assert (ext.faixa.minimo, ext.faixa.maximo) == (0, 0)
    assert ext.status == c.ACIMA and ext.overweight == pytest.approx(10)


def test_ativo_sem_faixa_do_usuario_so_tem_o_limite_da_estrategia():
    itub = _calc().linha(c.DIM_ATIVO, "ITUB4")
    assert itub.faixa.minimo is None and itub.faixa.alvo is None
    assert itub.faixa.maximo == 15
    assert itub.diferenca_para_alvo is None   # nenhum alvo presumido
    assert itub.overweight == pytest.approx(5) and itub.status == c.ACIMA


def test_faixa_do_usuario_petr4_de_2_a_5():
    calc = _calc(faixas={"ativo": {"petr4": {"min": 2, "max": 5}}})
    petr4 = calc.linha(c.DIM_ATIVO, "PETR4")
    # o usuário é mais restritivo que o limite por ativo (15%)
    assert (petr4.faixa.minimo, petr4.faixa.maximo) == (2, 5)
    assert petr4.faixa.origem_maximo == c.USUARIO
    assert petr4.overweight == pytest.approx(10) and petr4.underweight == 0
    assert petr4.status == c.ACIMA
    [alerta] = [a for a in calc.alertas if a.chave == "PETR4"]
    assert alerta.mensagem == ("PETR4 está acima da faixa-alvo: 15% contra 2% "
                               "a 5% (da faixa que você definiu).")
    assert alerta.severidade == c.MEDIA


def test_faixa_do_usuario_mais_larga_que_o_limite_perde_para_o_limite():
    calc = _calc(faixas={"ativo": {"BBAS3": {"min": 12, "alvo": 14, "max": 20}}})
    bbas = calc.linha(c.DIM_ATIVO, "BBAS3")
    assert bbas.faixa.maximo == 15 and bbas.faixa.origem_maximo == c.ESTRATEGIA
    assert bbas.diferenca_para_alvo == pytest.approx(-4)
    assert bbas.underweight == pytest.approx(2) and bbas.status == c.ABAIXO


def test_faixa_so_com_alvo_e_alvo_pontual():
    calc = _calc(politica={}, faixas={"setor": {"Logística": {"alvo": 12}}})
    log = calc.linha(c.DIM_SETOR, "Logística")
    assert log.underweight == pytest.approx(2) and log.status == c.ABAIXO


def test_faixa_para_ativo_fora_da_carteira_aparece_com_peso_zero():
    calc = _calc(faixas={"ativo": {"VALE3": {"min": 3, "max": 8}}})
    vale = calc.linha(c.DIM_ATIVO, "VALE3")
    assert vale.atual == 0 and vale.underweight == pytest.approx(3)


@pytest.mark.parametrize("faixa", [{"min": 6, "max": 5}, {"min": -1},
                                   {"max": 120}, {"min": 2, "alvo": 1}])
def test_faixa_invalida_levanta(faixa):
    with pytest.raises(ValueError):
        c.faixa_do_usuario(faixa)


def test_subclasse_e_setor_agregados():
    calc = _calc()
    assert calc.linha(c.DIM_SUBCLASSE, "Tesouro IPCA+").atual == pytest.approx(20)
    assert calc.linha(c.DIM_SUBCLASSE, "CDB").atual == pytest.approx(10)
    fin = calc.linha(c.DIM_SETOR, "Financeiro")
    assert fin.atual == pytest.approx(30) and fin.faixa.maximo == 25
    assert fin.overweight == pytest.approx(5)


# -- concentração e HHI ------------------------------------------------------------------

def test_hhi_por_ativo_e_numero_efetivo():
    k = _calc().concentracao[c.DIM_ATIVO]
    pesos = [.2, .1, .15, .05, .1, .2, .1, .1]
    assert k.hhi == pytest.approx(sum(p * p for p in pesos))   # 0,145
    assert k.hhi == pytest.approx(hhi_global(pd.Series(pesos)))
    assert k.numero_efetivo == pytest.approx(1 / 0.145)
    assert k.top3 == pytest.approx(55)                          # 20+20+15
    assert k.cobertura == pytest.approx(100)


def test_setor_medido_dentro_da_renda_variavel():
    calc = _calc()
    k = calc.concentracao[c.DIM_SETOR]
    assert calc.peso_renda_variavel == pytest.approx(60)
    assert k.base == "renda variável" and k.peso_da_base == pytest.approx(60)
    assert [(g.chave, round(g.peso, 2)) for g in k.grupos] == [
        ("Financeiro", 50.0), ("Petróleo", 33.33), ("Logística", 16.67)]
    assert k.hhi == pytest.approx(0.25 + (1 / 3) ** 2 + (1 / 6) ** 2)


def test_classe_e_geografia():
    calc = _calc()
    assert calc.concentracao[c.DIM_CLASSE].hhi == pytest.approx(
        .5 ** 2 + .3 ** 2 + .1 ** 2 + .1 ** 2)
    geo = {g.chave: g.peso for g in calc.concentracao[c.DIM_GEOGRAFIA].grupos}
    assert geo == pytest.approx({"Brasil": 90, "Exterior (USD)": 10})


def test_emissor_junta_petr3_e_petr4_e_hhi_ignora_nao_identificado():
    k = _calc().concentracao[c.DIM_EMISSOR]
    petr = next(g for g in k.grupos if g.chave == "PETR")
    assert petr.peso == pytest.approx(20) and set(petr.tickers) == {"PETR3", "PETR4"}
    assert next(g for g in k.grupos if g.chave == c.TESOURO_NACIONAL).peso == \
        pytest.approx(20)
    assert k.nao_identificado == pytest.approx(10)   # o banco do CDB
    assert k.cobertura == pytest.approx(90)
    # HHI renormalizado sobre os 90% identificados: (3·20² + 3·10²) / 90²
    assert k.hhi == pytest.approx(15 / 81)


def test_indexador_medido_dentro_da_renda_fixa():
    k = _calc().concentracao[c.DIM_INDEXADOR]
    assert k.base == "renda fixa" and k.peso_da_base == pytest.approx(30)
    grupos = {g.chave: g.peso for g in k.grupos}
    assert grupos == pytest.approx({"IPCA": 200 / 3, "CDI": 100 / 3})
    assert k.hhi == pytest.approx(5 / 9)


def test_carteira_de_um_ativo_tem_hhi_1():
    k = _calc([_pos("HGLG11", "FII", 100, "Logística")]).concentracao[c.DIM_ATIVO]
    assert k.hhi == 1.0 and k.numero_efetivo == 1.0


# -- identificação de emissor, indexador e geografia -----------------------------------------

@pytest.mark.parametrize("posicao,esperado", [
    ({"ticker": "PETR4", "classe": "Ações BR"}, "PETR"),
    ({"ticker": "TAEE11", "classe": "Ações BR"}, "TAEE"),
    ({"ticker": "AAPL34", "classe": "BDR"}, "AAPL"),
    ({"ticker": "MSFT", "classe": "Ações BR", "moeda": "USD"}, "MSFT"),
    ({"ticker": "TSELIC2029", "classe": "Renda Fixa"}, c.TESOURO_NACIONAL),
    ({"ticker": "X", "nome": "Tesouro Prefixado 2027"}, c.TESOURO_NACIONAL),
    ({"ticker": "CDB-XP", "classe": "Renda Fixa"}, c.NAO_IDENTIFICADO),
])
def test_emissor(posicao, esperado):
    assert c.emissor(posicao) == esperado


@pytest.mark.parametrize("posicao,esperado", [
    ({"ticker": "TIPCA2035", "classe": "Tesouro Direto"}, "IPCA"),
    ({"ticker": "TSELIC2029", "classe": "Tesouro Direto"}, "Selic"),
    ({"ticker": "TPRE2031", "classe": "Tesouro Direto"}, "Prefixado"),
    ({"ticker": "X", "nome": "Tesouro Renda+ Aposentadoria 2065",
      "classe": "Tesouro Direto"}, "IPCA"),
    ({"ticker": "CDB1", "nome": "CDB 110% CDI", "classe": "Renda Fixa"}, "CDI"),
    ({"ticker": "LCA1", "nome": "LCA PRE 12%", "classe": "Renda Fixa"}, "Prefixado"),
    ({"ticker": "D1", "nome": "Debênture IGP-M", "classe": "Renda Fixa"}, "IGP-M"),
    # dois indexadores no nome: não escolhe
    ({"ticker": "C1", "nome": "CDB IPCA ou CDI", "classe": "Renda Fixa"},
     c.NAO_IDENTIFICADO),
    ({"ticker": "F1", "nome": "Fundo RF", "classe": "Fundo RF"}, c.NAO_IDENTIFICADO),
    ({"ticker": "PETR4", "classe": "Ações BR"}, None),   # não se aplica
])
def test_indexador(posicao, esperado):
    assert c.indexador(posicao) == esperado


def test_geografia():
    assert c.geografia({"classe": "FII"}) == "Brasil"
    assert c.geografia({"classe": "Ações BR", "moeda": "USD"}) == "Exterior (USD)"
    assert c.geografia({"classe": "BDR", "pais": "Estados Unidos"}) == "Estados Unidos"
    assert c.geografia({"classe": "Cripto"}) == c.NAO_IDENTIFICADO


# -- alertas objetivos ------------------------------------------------------------------------

def test_alerta_de_ativo_acima_do_limite_com_a_frase_da_regra():
    calc = _calc()
    [itub] = [a for a in calc.alertas if a.chave == "ITUB4"]
    assert itub.mensagem == ("ITUB4 representa 20% da carteira e excede o "
                             "limite por ativo de 15%.")
    assert itub.severidade == c.ALTA and itub.referencia == 15


def test_alerta_com_decimal_em_virgula():
    posicoes = [_pos("A", "FII", 187), _pos("B", "FII", 813)]
    calc = _calc(posicoes, {"single_asset_limit_pct": 10})
    [a] = [x for x in calc.alertas if x.chave == "A"]
    assert a.mensagem == ("A representa 18,7% da carteira e excede o limite "
                          "por ativo de 10%.")


def test_alerta_de_setor_na_renda_variavel():
    [a] = _alertas(_calc(), "setor_na_renda_variavel")
    assert a.mensagem == ("O setor Financeiro representa 50% da renda variável "
                          "(limiar de 40% adotado pelo sistema).")


def test_alertas_de_classe_e_de_setor():
    calc = _calc()
    mensagens = {a.chave: a.mensagem for a in calc.alertas
                 if a.dimensao in (c.DIM_CLASSE, c.DIM_SETOR)
                 and a.codigo != "setor_na_renda_variavel"}
    assert mensagens["renda_fixa"] == (
        "Renda fixa está abaixo da faixa-alvo: 30% contra 35% a 45%, alvo 40% "
        "(tolerância de 5 pp adotada pelo sistema).")
    assert mensagens["acoes_br"] == (
        "Ações Brasil representa 50% da carteira e excede o limite máximo da "
        "classe de 32%.")
    assert mensagens["Financeiro"] == (
        "O setor Financeiro representa 30% da carteira e excede o limite por "
        "setor de 25%.")
    assert "exterior" not in mensagens       # dentro da faixa


def test_alerta_de_emissor_com_mais_de_um_papel():
    [a] = _alertas(_calc(), "emissor_acima")
    assert a.chave == "PETR" and a.valor == pytest.approx(20)
    # Tesouro Nacional (20%, um papel só aqui) não vira alerta de emissor
    assert all(x.chave != c.TESOURO_NACIONAL for x in _alertas(_calc(), "emissor_acima"))


def test_alerta_de_classe_fora_da_politica():
    posicoes = POSICOES + [_pos("BTC", "Cripto", 1000)]
    [a] = _alertas(_calc(posicoes), "fora_da_politica")
    assert a.valor == pytest.approx(1000 / 11000 * 100)
    assert "Cripto" in a.mensagem


def test_alertas_ordenados_por_severidade():
    ordem = [c._ORDEM_SEVERIDADE[a.severidade] for a in _calc().alertas]
    assert ordem == sorted(ordem)


def test_sem_limites_nem_alvos_nao_ha_alerta_de_faixa():
    calc = _calc(politica={})
    assert all(a.status == c.SEM_REFERENCIA
               for a in calc.alocacao[c.DIM_ATIVO])
    assert {a.codigo for a in calc.alertas} <= {"setor_na_renda_variavel"}


# -- integração: o contexto e a análise usam os números calculados --------------------------------

def test_contexto_e_analise_usam_o_calculo_e_nao_o_pct_da_carteira():
    carteira = {"total_mercado": 10_000, "posicoes": POSICOES}
    ctx = contexto.montar(_registro(single_asset_limit_pct=15), carteira,
                          faixas={"ativo": {"HGLG11": {"min": 12, "max": 14}}})
    assert ctx.calculos is not None
    assert ctx.peso_por_classe["acoes_br"] == pytest.approx(50)
    pos = next(p for p in POSICOES if p["ticker"] == "HGLG11")
    a = analise.analisar(pos, ctx)
    assert a.ativo.peso_atual == pytest.approx(10)       # não 99
    assert a.faixa.piso_ativo == 12 and a.faixa.status_ativo == c.ABAIXO
    assert a.faixa.underweight_ativo == pytest.approx(2)
    assert a.faixa.emissor == "HGLG"
    assert any("abaixo do mínimo de 12%" in j for j in a.acao.justificativas)


def test_texto_para_llm_leva_os_numeros_prontos():
    carteira = {"total_mercado": 10_000, "posicoes": POSICOES}
    ctx = contexto.montar(_registro(single_asset_limit_pct=15), carteira)
    pos = next(p for p in POSICOES if p["ticker"] == "ITUB4")
    texto = analise.texto_para_llm(analise.analisar(pos, ctx), ctx)
    assert "CÁLCULOS DETERMINÍSTICOS DA CARTEIRA" in texto
    assert "não recalcule" in texto
    assert "HHI" in texto and "overweight" in texto
    assert "Alerta (alta): ITUB4 representa 20% da carteira" in texto


def test_como_dict_serializa_os_calculos():
    d = _calc().como_dict()
    assert d["concentracao"]["ativo"]["hhi"] == pytest.approx(0.145)
    assert not math.isnan(d["peso_renda_variavel"])

import numpy as np
import pandas as pd
import pytest

import core.b3_safras as b3_safras
from core.b3_safras import (
    SafraCarteira,
    _dias_por_ano_civil,
    carteiras_por_safra,
    retorno_da_safra,
    tabela_de_safras,
)

HOJE = pd.Timestamp("2026-09-21")


def _resultado(segmento, lids_por_ano, pesos_por_ano, tickers):
    return {
        "setor": "S", "subsetor": "SS", "segmento": segmento,
        "tickers": tickers,
        "lids_por_ano": lids_por_ano,
        "pesos_por_ano": pesos_por_ano,
    }


def _precos(datas, valores_por_ticker):
    return pd.DataFrame(valores_por_ticker, index=pd.DatetimeIndex(datas))


def test_carteira_da_safra_soma_um():
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3", "BBBB3"])]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.safra == 2024
    assert carteira.ano_base == 2023
    assert carteira.inicio == pd.Timestamp("2024-04-01")
    assert carteira.fim == pd.Timestamp("2025-03-31")
    assert carteira.completa is True
    assert pytest.approx(sum(carteira.pesos.values())) == 1.0


def test_orcamento_igual_entre_segmentos():
    """Dois segmentos, um lider cada: 50% para cada segmento."""
    res = [
        _resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
        _resultado("B", {2024: ["BBBB3"]}, {2024: {"BBBB3": 1.0}}, ["BBBB3"]),
    ]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.pesos == pytest.approx({"AAAA3": 0.5, "BBBB3": 0.5})
    assert carteira.segmentos == 2


def test_ticker_em_dois_segmentos_soma_os_orcamentos():
    res = [
        _resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
        _resultado("B", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
    ]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.pesos == pytest.approx({"AAAA3": 1.0})


def test_universo_e_todos_os_tickers_dos_segmentos():
    """O equal-weight compara 'escolher os lideres' com 'comprar o segmento
    inteiro' -- entao o universo tem que ter os nao-selecionados tambem."""
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}},
                      ["AAAA3", "BBBB3", "CCCC3"])]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.universo == ("AAAA3", "BBBB3", "CCCC3")


def test_safra_vigente_vem_marcada_como_incompleta():
    res = [_resultado("A", {2024: ["AAAA3"], 2026: ["AAAA3"]},
                      {2024: {"AAAA3": 1.0}, 2026: {"AAAA3": 1.0}}, ["AAAA3"])]
    por_safra = {c.safra: c for c in carteiras_por_safra(res, hoje=HOJE)}
    assert por_safra[2024].completa is True
    assert por_safra[2026].completa is False


def test_retorno_usa_so_precos_da_janela():
    """Preco de janeiro/2024 e de maio/2025 sao armadilhas: se entrarem no
    calculo, o retorno sai diferente de +50%."""
    df = _precos(
        ["2024-01-31", "2024-04-30", "2024-12-31", "2025-03-31", "2025-05-31"],
        {"AAAA3": [1.0, 10.0, 12.0, 15.0, 999.0]},
    )
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["retorno_estrategia"] == pytest.approx(0.5)


def test_peso_sem_preco_rende_zero_e_e_reportado():
    """Mesma convencao de core/fii_validation.py: a fatia ausente nao rende o
    que os sobreviventes renderam, e nao e redistribuida entre eles."""
    df = _precos(["2024-04-30", "2025-03-31"],
                 {"AAAA3": [10.0, 20.0], "BBBB3": [np.nan, np.nan]})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 0.5, "BBBB3": 0.5},
        universo=("AAAA3", "BBBB3"), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["retorno_estrategia"] == pytest.approx(0.5)   # 0.5*1.0 + 0.5*0
    assert out["peso_ausente"] == pytest.approx(0.5)


def test_selic_da_janela_cruza_dois_anos():
    """A janela abril/2024 a marco/2025 pega dias de 2024 e de 2025, cada
    um com sua PROPRIA taxa -- usar so o ano do inicio continuaria passando
    se as duas taxas fossem iguais. `inicio_mercado` e 30/04/2024 (primeiro
    preco real) e `corte` e 31/03/2025: `_dias_por_ano_civil` reparte os
    335 dias corridos em 246 dias de 2024 (30/04 a 31/12) e 89 dias de 2025
    (01/01 a 31/03) -- conferido com
    `(pd.Timestamp('2025-03-31')-pd.Timestamp('2024-04-30')).days == 335`
    e `246+89==335`. Cada pedaco capitaliza com a taxa do seu ano."""
    df = _precos(pd.date_range("2024-04-30", "2025-03-31", freq="ME"),
                 {"AAAA3": [10.0] * 12})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df,
                           selic_por_ano={2024: 0.12, 2025: 0.10},
                           taxa_selic_aa=0.0)
    # 246 dias de 2024 a 12% a.a. + 89 dias de 2025 a 10% a.a.
    esperado = (1.12 ** (246 / 365.0)) * (1.10 ** (89 / 365.0)) - 1.0
    assert out["retorno_selic"] == pytest.approx(esperado, abs=0.001)
    assert out["selic_anos_estimados"] == []


def test_selic_com_ano_ausente_ou_none_cai_no_fallback_e_e_reportado():
    """Ano fora do dict E ano com valor None explicito tem que cair no
    mesmo default -- o bug antigo (`or 0.0`) fazia {2025: None} virar
    0,0% em vez de usar taxa_selic_aa."""
    df = _precos(pd.date_range("2024-04-30", "2025-03-31", freq="ME"),
                 {"AAAA3": [10.0] * 12})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df,
                           selic_por_ano={2025: None},  # 2024 ausente
                           taxa_selic_aa=0.10)
    # mesma janela e mesma repartição de dias de
    # test_selic_da_janela_cruza_dois_anos (246 dias de 2024, 89 de 2025),
    # mas os dois anos caem no fallback taxa_selic_aa=0.10.
    esperado = (1.10 ** (246 / 365.0)) * (1.10 ** (89 / 365.0)) - 1.0
    assert out["retorno_selic"] == pytest.approx(esperado, abs=0.001)
    assert out["selic_anos_estimados"] == [2024, 2025]


def test_selic_da_safra_parcial_para_no_corte():
    """Safra incompleta so tem preco ate agosto/2026 -- a Selic tem que
    comparar os mesmos dias, nao a janela inteira. `inicio_mercado` e
    30/04/2026 e `corte` e 31/08/2026: `(pd.Timestamp('2026-08-31') -
    pd.Timestamp('2026-04-30')).days == 123`, tudo dentro de 2026."""
    df = _precos(pd.date_range("2026-04-30", "2026-08-31", freq="ME"),
                 {"AAAA3": [10.0, 10.5, 11.0, 11.5, 12.0]})
    carteira = SafraCarteira(
        safra=2026, ano_base=2025,
        inicio=pd.Timestamp("2026-04-01"), fim=pd.Timestamp("2027-03-31"),
        completa=False, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df,
                           selic_por_ano={2026: 0.12, 2027: 0.12},
                           taxa_selic_aa=0.0)
    # 123 dias corridos de 2026 a 12% a.a.
    esperado = 1.12 ** (123 / 365.0) - 1.0
    assert out["retorno_selic"] == pytest.approx(esperado, abs=0.001)


def test_selic_e_estavel_entre_convencao_fim_de_mes_e_inicio_de_mes():
    """A mesma janela economica (~11 meses de bolsa, abril/2024 a
    marco/2025) medida com indice de preco em fim de mes ou em inicio de
    mes tem que dar Selic quase igual -- contar fronteiras de mes no
    `date_range` antigo dava 12 meses para os dois lados tambem, mas em
    dias reais um e 335 e o outro 334
    (`(pd.Timestamp('2025-03-01')-pd.Timestamp('2024-04-01')).days==334`);
    a diferenca tem que ficar na casa de 0,1 pp, nao de 1 pp
    (`yf.download(interval="1mo")` devolve indice de inicio de mes, ver
    N-1 do relatorio e `views/empresas_b3.py:497,504`)."""
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    selic_por_ano = {2024: 0.12, 2025: 0.12}

    df_fim_de_mes = _precos(["2024-04-30", "2025-03-31"], {"AAAA3": [10.0, 11.0]})
    df_inicio_de_mes = _precos(["2024-04-01", "2025-03-01"], {"AAAA3": [10.0, 11.0]})

    fim_de_mes = retorno_da_safra(carteira, df_fim_de_mes,
                                  selic_por_ano=selic_por_ano, taxa_selic_aa=0.0)
    inicio_de_mes = retorno_da_safra(carteira, df_inicio_de_mes,
                                     selic_por_ano=selic_por_ano, taxa_selic_aa=0.0)
    assert fim_de_mes["retorno_selic"] == pytest.approx(
        inicio_de_mes["retorno_selic"], abs=0.001)


def test_ticker_que_para_de_negociar_conta_como_sem_preco():
    """BBBB3 so tem preco ate agosto -- ponta final longe do corte (marco)
    vira SEM PRECO, nao o retorno de 4 meses rotulado como o da safra."""
    datas = pd.date_range("2024-04-30", "2025-03-31", freq="ME")
    df = pd.DataFrame(
        {
            "AAAA3": [10.0] * len(datas),
            "BBBB3": [10.0, 11.0, 12.0, 13.0, 14.0] + [np.nan] * (len(datas) - 5),
        },
        index=datas,
    )
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 0.5, "BBBB3": 0.5},
        universo=("AAAA3", "BBBB3"), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["peso_ausente"] == pytest.approx(0.5)
    assert out["n_universo_total"] == 2
    assert out["n_universo_com_preco"] == 1


def test_equal_weight_pune_lacuna_como_a_estrategia():
    """Um lider com 100% do orcamento e quatro tickers no universo com
    retornos diferentes -- o EW nao pode ser so a media dos sobreviventes,
    senao o benchmark fica mais forte do que o mercado que ele descreve."""
    datas = pd.date_range("2024-04-30", "2025-03-31", freq="ME")
    n = len(datas)
    df = pd.DataFrame(
        {
            "AAAA3": np.linspace(10.0, 20.0, n),  # +100%
            "BBBB3": np.linspace(10.0, 15.0, n),  # +50%
            "CCCC3": np.linspace(10.0, 5.0, n),   # -50%
            "DDDD3": [np.nan] * n,                # sem preco
        },
        index=datas,
    )
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0},
        universo=("AAAA3", "BBBB3", "CCCC3", "DDDD3"), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["retorno_estrategia"] == pytest.approx(1.0)
    assert out["retorno_equal_weight"] == pytest.approx((1.0 + 0.5 - 0.5 + 0.0) / 4)
    assert out["excesso_equal_weight"] == pytest.approx(1.0 - 0.25)
    assert out["n_universo_com_preco"] == 3
    assert out["n_universo_total"] == 4


def test_tabela_vazia_tem_colunas_e_empty():
    """Sem safras, o quadro tem que ter as colunas declaradas -- senao
    `tabela["Safra"]` estoura KeyError em vez de achar um quadro vazio."""
    tabela = tabela_de_safras([], _precos([], {}), selic_por_ano={},
                              taxa_selic_aa=0.0, hoje=HOJE)
    assert tabela.empty
    assert list(tabela.columns) == [
        "Safra", "Exercício-base", "Janela", "Completa", "Mensurável",
        "Segmentos", "Ativos", "Maiores posições", "Estratégia (%)",
        "Equal-weight (%)", "Selic (%)", "Excesso s/ Selic (pp)",
        "Peso sem preço (%)", "Universo com preço",
    ]
    assert tabela.attrs["safras_completas"] == []
    assert tabela.attrs["selic_anos_estimados_por_safra"] == {}


def test_mercado_comecando_atrasado_nao_zera_a_safra():
    """Se o feed de precos inteiro so comeca depois do inicio civil da
    safra (atraso de ingestao, nao delisting de um papel so), a tolerancia
    da ponta inicial tem que ancorar no inicio_mercado -- senao TODO
    ticker reprova o piso de proximidade por um problema de cobertura de
    dado que nao e dele. Sem a correcao, d0=31/05 fica a 60 dias do inicio
    civil (01/04), estoura os 45 dias de tolerancia e a safra inteira sai
    com peso_ausente=1.0 e retorno 0.0."""
    df = _precos(["2024-05-31", "2025-03-31"], {"AAAA3": [10.0, 17.5]})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["peso_ausente"] == pytest.approx(0.0)
    assert out["retorno_estrategia"] == pytest.approx(0.75)


def test_linha_toda_nan_no_fim_nao_vira_corte():
    """`reindex`/`asfreq` sobre o calendario cheio deixa linha 100% NaN
    depois do ultimo pregao real -- o corte tem que parar no ultimo dado
    de verdade, nao no ultimo dia do calendario. Sem o `dropna(how="all")`
    em `_janela_de_mercado`, o corte seria 31/03/2025 (a linha vazia) em
    vez de 31/08/2024 (o ultimo preco real), e a Selic contaria meses que
    nenhum papel negociou."""
    df = _precos(
        ["2024-04-30", "2024-08-31", "2025-03-31"],
        {"AAAA3": [10.0, 15.0, np.nan]},
    )
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=False, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df,
                           selic_por_ano={2024: 0.12, 2025: 0.12},
                           taxa_selic_aa=0.0)
    # 123 dias corridos de 30/04 (inicio_mercado) a 31/08 (corte real),
    # todos em 2024: (pd.Timestamp('2024-08-31')-pd.Timestamp('2024-04-30')
    # ).days == 123. Se a linha 100% NaN de 31/03/2025 contasse como
    # pregao, o corte iria ate marco/2025 e os dias seriam bem mais.
    esperado = 1.12 ** (123 / 365.0) - 1.0
    assert out["retorno_selic"] == pytest.approx(esperado, abs=0.001)


def test_tabela_exclui_safra_incompleta_das_medias():
    res = [_resultado("A", {2024: ["AAAA3"], 2026: ["AAAA3"]},
                      {2024: {"AAAA3": 1.0}, 2026: {"AAAA3": 1.0}}, ["AAAA3"])]
    df = _precos(pd.date_range("2024-04-30", "2026-09-30", freq="ME"),
                 {"AAAA3": np.linspace(10.0, 30.0, 30)})
    tabela = tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                              hoje=HOJE)
    assert set(tabela["Safra"]) == {2024, 2026}
    assert not bool(tabela.loc[tabela["Safra"] == 2026, "Completa"].iloc[0])
    assert bool(tabela.loc[tabela["Safra"] == 2024, "Completa"].iloc[0])
    assert tabela.attrs["safras_completas"] == [2024]
    assert 2026 not in tabela.attrs["safras_completas"]


def test_tabela_reporta_cobertura_do_universo_e_anos_estimados_da_selic():
    """`retorno_da_safra` calcula `n_universo_total`/`n_universo_com_preco`
    e `selic_anos_estimados`, mas quem le a tela e `tabela_de_safras` --
    sem essa ponte a Task 5 nao tem como avisar cobertura baixa nem qual
    trecho da Selic veio do fallback."""
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}},
                      ["AAAA3", "BBBB3"])]
    df = _precos(["2024-04-30", "2025-03-31"],
                 {"AAAA3": [10.0, 20.0], "BBBB3": [np.nan, np.nan]})
    tabela = tabela_de_safras(res, df, selic_por_ano={2025: None},
                              taxa_selic_aa=0.10, hoje=HOJE)
    linha = tabela.loc[tabela["Safra"] == 2024].iloc[0]
    assert linha["Universo com preço"] == "1/2"
    assert tabela.attrs["selic_anos_estimados_por_safra"][2024] == [2024, 2025]


def test_tabela_nao_tem_coluna_vazia_por_chave_desalinhada():
    """Cada coluna declarada em COLUNAS_TABELA tem que vir preenchida.
    `pd.DataFrame(linhas, columns=COLUNAS_TABELA)` so mostra o que
    COLUNAS_TABELA declara -- uma coluna na lista sem a chave
    correspondente no dict fica cheia de NaN sem erro nenhum, e essa
    direcao continua coberta aqui via `isna()`. A outra direcao -- chave
    nova no dict sem entrar na lista, que aqui simplesmente sumiria em
    silencio -- e fechada em `tabela_de_safras`, que levanta `ValueError`
    no ponto em que a linha e montada (`set(linha) == set(COLUNAS_TABELA)`)
    antes mesmo de chegar no DataFrame; qualquer chamada a
    `tabela_de_safras`, inclusive esta, ja exercita essa guarda."""
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"])]
    df = _precos(["2024-04-30", "2025-03-31"], {"AAAA3": [10.0, 20.0]})
    tabela = tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                              hoje=HOJE)
    assert not tabela.isna().any().any()


# ---------------------------------------------------------------------------
# Rodada de correção 4
# ---------------------------------------------------------------------------


def test_safra_sem_nenhum_pregao_nao_e_mensuravel():
    """Janela 100% NaN: nenhum pregao medido, entao NENHUM retorno pode sair
    com numero.

    Sem a correcao, `_janela_de_mercado` recaia nas pontas CIVIS
    (01/04/2024 a 31/03/2025 = 364 dias corridos) e a Selic capitalizava a
    janela inteira contra estrategia 0,0 e equal-weight 0,0:
    `1.12 ** (364/365) - 1 = 0.11965`, ou seja `Excesso s/ Selic = -12,0 pp`
    (o `round(-0.11965*100, 1)`) sem um unico preco observado -- na tela,
    indistinguivel de uma safra real que perdeu 12 pontos.

    `peso_ausente` e a cobertura continuam saindo com numero: sao contagem
    do observado, nao retorno fabricado.
    """
    df = _precos(["2024-04-30", "2025-03-31"],
                 {"AAAA3": [np.nan, np.nan], "BBBB3": [np.nan, np.nan]})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 0.5, "BBBB3": 0.5},
        universo=("AAAA3", "BBBB3"), segmentos=1,
    )
    out = retorno_da_safra(carteira, df,
                           selic_por_ano={2024: 0.12, 2025: 0.12},
                           taxa_selic_aa=0.0)
    assert out["mensuravel"] is False
    assert out["retorno_estrategia"] is None
    assert out["retorno_equal_weight"] is None
    assert out["retorno_selic"] is None
    assert out["excesso_selic"] is None
    assert out["excesso_equal_weight"] is None
    assert out["selic_anos_estimados"] == []
    # contagem do observado: segue saindo com numero
    assert out["peso_ausente"] == pytest.approx(1.0)
    assert out["n_universo_total"] == 2
    assert out["n_universo_com_preco"] == 0


def test_df_de_precos_vazio_tambem_nao_e_mensuravel():
    """Mesma regra pela outra porta: quadro sem nenhuma linha (falha de
    leitura, universo sem cobertura no banco). Sem a correcao a safra saia
    com `Excesso s/ Selic = -12,0 pp` pela mesma conta da janela civil."""
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, _precos([], {}),
                           selic_por_ano={2024: 0.12, 2025: 0.12},
                           taxa_selic_aa=0.0)
    assert out["mensuravel"] is False
    assert out["retorno_selic"] is None
    assert out["excesso_selic"] is None
    assert out["n_universo_com_preco"] == 0


def test_universo_vazio_nao_publica_equal_weight_fabricado():
    """NOVO-2: `tickers=[]` deixava `retornos_ew` vazio e publicava
    `Equal-weight (%) = NaN` numa linha que, no resto, parecia medida.

    Sem universo nao ha mercado contra o qual comparar -- a safra cai no
    MESMO caminho de "nao mensuravel" de NOVO-1, em vez de inventar uma
    segunda gramatica so para essa coluna. O criterio
    `n_universo_com_preco == 0` cobre os dois casos porque `universo` e,
    por construcao de `carteiras_por_safra`, superconjunto de `pesos`.
    """
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, [])]
    df = _precos(["2024-04-30", "2025-03-31"], {"AAAA3": [10.0, 20.0]})

    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.universo == ()
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["mensuravel"] is False
    assert out["retorno_equal_weight"] is None
    assert out["excesso_equal_weight"] is None

    tabela = tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                              hoje=HOJE)
    linha = tabela.loc[tabela["Safra"] == 2024].iloc[0]
    assert bool(linha["Mensurável"]) is False
    assert pd.isna(linha["Equal-weight (%)"])
    assert tabela.attrs["safras_completas"] == []


def test_tabela_marca_safra_nao_mensuravel_e_a_exclui_das_medias():
    """A safra 2024 tem janela civil FECHADA (`Completa` segue True), mas
    nenhum pregao observado. A linha precisa aparecer marcada e ficar fora
    de `safras_completas` -- se entrar, a media da Task 5 soma um numero
    que nunca foi medido. A safra 2025, com preco de verdade, fica.

    2025 e medida de 30/04/2025 (primeiro preco real dentro da janela) a
    31/03/2026: 10,0 -> 12,0 da +20,0% na estrategia e no equal-weight
    (universo de um ticker so), com Selic zerada (`taxa_selic_aa=0.0` e
    2026 fora de `selic_por_ano`).
    """
    res = [_resultado("A", {2024: ["AAAA3"], 2025: ["AAAA3"]},
                      {2024: {"AAAA3": 1.0}, 2025: {"AAAA3": 1.0}}, ["AAAA3"])]
    df = _precos(["2024-04-30", "2025-03-31", "2025-04-30", "2026-03-31"],
                 {"AAAA3": [np.nan, np.nan, 10.0, 12.0]})
    tabela = tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                              hoje=HOJE)

    nao_medida = tabela.loc[tabela["Safra"] == 2024].iloc[0]
    assert bool(nao_medida["Completa"]) is True
    assert bool(nao_medida["Mensurável"]) is False
    assert nao_medida["Universo com preço"] == "0/1"
    for coluna in ("Estratégia (%)", "Equal-weight (%)", "Selic (%)",
                   "Excesso s/ Selic (pp)"):
        assert pd.isna(nao_medida[coluna]), coluna

    medida = tabela.loc[tabela["Safra"] == 2025].iloc[0]
    assert bool(medida["Mensurável"]) is True
    assert medida["Estratégia (%)"] == pytest.approx(20.0)
    assert medida["Equal-weight (%)"] == pytest.approx(20.0)

    assert tabela.attrs["safras_completas"] == [2025]


def test_tabela_desalinhada_de_colunas_tabela_levanta_nas_duas_direcoes(
        monkeypatch):
    """A guarda de `tabela_de_safras` fecha as DUAS direcoes do
    desalinhamento, e cada uma some em silencio sem ela:

    - coluna declarada sem chave no dict -> `pd.DataFrame(..., columns=...)`
      entrega uma coluna 100% NaN;
    - chave no dict sem coluna declarada -> o DataFrame simplesmente
      descarta o dado, e a Task 5 nunca sabe que ele existiu.
    """
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"])]
    df = _precos(["2024-04-30", "2025-03-31"], {"AAAA3": [10.0, 20.0]})
    declaradas = list(b3_safras.COLUNAS_TABELA)

    # direcao 1: COLUNAS_TABELA pede uma coluna que a linha nao monta.
    monkeypatch.setattr(b3_safras, "COLUNAS_TABELA",
                        [*declaradas, "Coluna Fantasma"])
    with pytest.raises(ValueError, match="Coluna Fantasma"):
        tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                         hoje=HOJE)

    # direcao 2: a linha monta uma chave que COLUNAS_TABELA nao declara.
    monkeypatch.setattr(b3_safras, "COLUNAS_TABELA",
                        [c for c in declaradas if c != "Selic (%)"])
    with pytest.raises(ValueError, match="Selic"):
        tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                         hoje=HOJE)


def test_dias_por_ano_civil_reparte_a_virada_do_ano():
    """Derivacao a mao do intervalo `[30/04/2024, 31/03/2025)`, que tem 335
    dias corridos. Pedaco de 2024 = `[30/04, 01/01/2025)`: o resto de abril
    (1 dia, o proprio 30/04) + maio 31 + junho 30 + julho 31 + agosto 31 +
    setembro 30 + outubro 31 + novembro 30 + dezembro 31 = 246. Pedaco de
    2025 = `[01/01, 31/03)`: janeiro 31 + fevereiro 28 + marco 30 (o dia
    31/03 e o fim exclusivo) = 89. E 246 + 89 = 335."""
    assert _dias_por_ano_civil(
        pd.Timestamp("2024-04-30"), pd.Timestamp("2025-03-31")
    ) == {2024: 246, 2025: 89}


def test_dias_por_ano_civil_acusa_erro_em_vez_de_travar(monkeypatch):
    """NOVO-3: o off-by-one natural aqui e escrever `min(fim, fim_do_ano)`
    sem o `+ 1 day`. Com o `while` aberto da rodada 3 o cursor parava em
    31/12 e nunca mais avancava: a SUITE TRAVAVA em laco infinito em vez de
    ficar vermelha (aconteceu de verdade com o revisor). O laco limitado a
    um pedaco por ano civil transforma isso em `ValueError` -- termina, e
    diz onde parou."""
    def _virada_com_off_by_one(cursor, fim):
        fim_do_ano = pd.Timestamp(year=cursor.year, month=12, day=31)
        return min(fim, fim_do_ano)

    monkeypatch.setattr(b3_safras, "_proxima_virada_de_ano",
                        _virada_com_off_by_one)
    with pytest.raises(ValueError, match="nao cobriu a janela inteira"):
        _dias_por_ano_civil(pd.Timestamp("2024-04-30"),
                            pd.Timestamp("2025-03-31"))

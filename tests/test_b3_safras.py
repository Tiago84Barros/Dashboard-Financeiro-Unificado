import numpy as np
import pandas as pd
import pytest

import core.b3_safras as b3_safras
from core.b3_evidence import minimum_detectable_effect
from core.b3_safras import (
    MIN_SAFRAS_LOO,
    SafraCarteira,
    _dias_por_ano_civil,
    _p_valor_unilateral,
    bootstrap_excesso,
    carteiras_por_safra,
    fragilidade_leave_one_out,
    retorno_da_safra,
    tabela_de_safras,
    veredito_do_rank_ic,
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


# --------------------------------------------------------------------------
# Task 6 -- Bloco 3: banda (bootstrap do excesso) e fragilidade (leave-one-out)
# --------------------------------------------------------------------------


def test_bootstrap_de_excessos_todos_positivos_nao_atravessa_zero():
    baixo, alto = bootstrap_excesso([0.10, 0.12, 0.11, 0.09, 0.13])
    assert baixo > 0
    assert alto > baixo


def test_bootstrap_de_excessos_mistos_atravessa_zero():
    baixo, alto = bootstrap_excesso([0.20, -0.18, 0.15, -0.22, 0.05])
    assert baixo < 0 < alto


def test_bootstrap_com_amostra_minima_devolve_none():
    assert bootstrap_excesso([0.1]) == (None, None)
    assert bootstrap_excesso([]) == (None, None)


def test_bootstrap_e_reprodutivel():
    assert bootstrap_excesso([0.1, -0.05, 0.2, 0.0, 0.07]) == \
           bootstrap_excesso([0.1, -0.05, 0.2, 0.0, 0.07])


def test_leave_one_out_detecta_conclusao_que_depende_de_uma_safra():
    """Quatro ICs modestos e um outlier que sustenta sozinho a media.
    Remover o outlier tem que mudar o estado -- se a funcao devolver
    zero safras que viram, ela nao esta recalculando de verdade."""
    ic_values = [0.02, 0.01, 0.0, 0.01, 0.60]
    out = fragilidade_leave_one_out(ic_values)
    assert out["safras_que_viram"] >= 1
    assert len(out["estados_loo"]) == len(ic_values)


def test_leave_one_out_em_evidencia_robusta_nao_vira():
    ic_values = [0.30, 0.32, 0.28, 0.31, 0.29, 0.33]
    out = fragilidade_leave_one_out(ic_values)
    assert out["safras_que_viram"] == 0


def test_leave_one_out_deriva_significancia_de_cada_subamostra():
    """`classify_evidence` sem `p_value` so consegue devolver tres estados
    (sem amplitude, sinal anti-preditivo, sem significancia) -- nenhum deles
    sensivel a tirar uma safra boa de uma amostra positiva. Chamada assim, a
    fragilidade daria zero SEMPRE e pareceria robustez.

    Este teste prende a derivacao: o p-valor tem que sair da SUBAMOSTRA de
    cada passo, e com ele o estado do passo que remove o outlier vira
    `evidencia_a_favor` enquanto o do conjunto inteiro continua
    `inconclusivo`."""
    out = fragilidade_leave_one_out([0.02, 0.01, 0.0, 0.01, 0.60])
    assert out["estado_completo"] == "inconclusivo"
    assert out["estados_loo"][-1] == "evidencia_a_favor"
    assert out["estados_loo"][:-1] == ["inconclusivo"] * 4


def test_leave_one_out_sem_amostra_nao_inventa_fragilidade():
    """Sem safra nenhuma nao ha o que remover: zero safras que viram tem que
    significar "nao ha evidencia a testar", e nao "a evidencia e robusta" --
    por isso `estados_loo` sai vazio e o estado completo e o de amplitude
    insuficiente, que a tela le para nao publicar selo de robustez."""
    out = fragilidade_leave_one_out([])
    assert out["estados_loo"] == []
    assert out["safras_que_viram"] == 0
    assert out["estado_completo"] == "inconclusivo"


def test_bootstrap_e_reprodutivel_em_amostra_com_muitos_valores_distintos():
    """`test_bootstrap_e_reprodutivel` sozinho NAO prende a semente: com 5
    safras a media reamostrada assume poucos valores discretos e os
    percentis 2,5/97,5 caem no mesmo deles mesmo sem semente -- removi
    `seed` do `default_rng` e aquele teste passou.

    Com 30 valores distintos os percentis ja separam as execucoes, e
    reprodutibilidade volta a ser uma afirmacao verificavel. A semente
    existe para isto, e nao para dar estabilidade a conclusao -- quem mede
    estabilidade e `fragilidade_leave_one_out`."""
    excessos = [round(0.01 * i - 0.15 + 0.0037 * (i % 7), 6) for i in range(30)]
    assert len(set(excessos)) == 30
    assert bootstrap_excesso(excessos) == bootstrap_excesso(excessos)


# --------------------------------------------------------------------------
# Rodada de correcao 1 da Task 6 -- F-1/F-2/F-3/F-4/F-5
# --------------------------------------------------------------------------


def test_veredito_do_rank_ic_e_o_mesmo_que_o_leave_one_out_usa():
    """F-1: dois leitores do mesmo Rank-IC tem que chegar ao mesmo estado.

    A tela publicava `classify_evidence(ic_values=...)` sem p-valor no card
    "Ordena?" enquanto o leave-one-out classificava COM p-valor. Com
    Rank-IC ~0,30 em 6 safras o card imprimia "Inconclusivo" e o motor
    interno concluia `evidencia_a_favor` -- o card de fragilidade dava selo
    verde a um veredito que a tela nao mostrava e que contradizia o card ao
    lado.

    Este teste compara os DOIS caminhos nos mesmos dados: a regra certa num
    leitor so nao cobre o outro leitor."""
    for ic_values in ([0.30, 0.32, 0.28, 0.31, 0.29, 0.33],
                      [0.02, 0.01, 0.0, 0.01, 0.60],
                      [-0.20, -0.18, -0.25, -0.22]):
        assert (veredito_do_rank_ic(ic_values).estado
                == fragilidade_leave_one_out(ic_values)["estado_completo"]), (
            f"os dois caminhos divergem em {ic_values}"
        )


def test_veredito_do_rank_ic_alcanca_evidencia_a_favor():
    """Contrapartida do teste acima: unificar os dois caminhos no criterio
    SEM p-valor tambem os deixaria iguais, e seria a unificacao errada --
    `evidencia_a_favor` viraria inalcancavel e o card nunca sairia de
    "Inconclusivo", por mais forte que a amostra fosse. Um criterio que so
    pode dar um resultado nao e criterio."""
    assert veredito_do_rank_ic(
        [0.30, 0.32, 0.28, 0.31, 0.29, 0.33]).estado == "evidencia_a_favor"


def test_leave_one_out_abaixo_do_piso_nao_publica_veredito():
    """F-2: com n abaixo de `MIN_SAFRAS_LOO`, `safras_que_viram == 0` e
    assinatura da amostra, nao robustez -- nao havia o que remover que
    fizesse o classificador mudar de ideia. Zero ali saia em VERDE na tela.

    Abaixo do piso a funcao declara `medido=False` e nao publica veredito
    nenhum, em vez de publicar o zero que parece limpeza."""
    for ic_values in ([0.30], [0.30, 0.32], [0.30, 0.32, 0.28]):
        out = fragilidade_leave_one_out(ic_values)
        assert out["medido"] is False, f"publicou veredito com n={len(ic_values)}"
        assert out["estados_loo"] == []
        assert out["safras_que_viram"] == 0


def test_leave_one_out_no_piso_volta_a_publicar_veredito():
    """Caso oposto do piso: exatamente em `MIN_SAFRAS_LOO` o LOO volta a
    medir. Sem esta metade, subir o piso para um numero inalcancavel
    desligaria a medicao inteira sem nenhum teste vermelho."""
    ic_values = [0.30, 0.32, 0.28, 0.31][:b3_safras.MIN_SAFRAS_LOO]
    assert len(ic_values) == b3_safras.MIN_SAFRAS_LOO
    out = fragilidade_leave_one_out(ic_values)
    assert out["medido"] is True
    assert len(out["estados_loo"]) == b3_safras.MIN_SAFRAS_LOO


def test_p_valor_concorda_com_o_teste_t_de_referencia():
    """F-3 e F-5 no mesmo lugar: o p-valor tem que bater com
    `scipy.stats.ttest_1samp(..., alternative="greater")` nos mesmos dados.

    Prende de uma vez a distribuicao (t, nao normal -- com n de 5 a 15
    safras elas nao sao intercambiaveis: em `[0.2, 0.0, 0.1]` o t da 0,113
    e a normal 0,042, e o veredito VIRA), a direcao (unilateral a direita)
    e o `ddof=1`."""
    from scipy.stats import ttest_1samp

    for amostra in ([0.2, 0.0, 0.1],
                    [0.30, 0.32, 0.28, 0.31, 0.29, 0.33],
                    [0.02, 0.01, 0.0, 0.01, 0.60],
                    [-0.1, 0.05, -0.2, 0.0]):
        esperado = float(ttest_1samp(amostra, 0.0, alternative="greater").pvalue)
        assert _p_valor_unilateral(amostra) == pytest.approx(esperado, rel=1e-9)


def test_p_valor_e_unilateral_a_direita_nao_bilateral():
    """F-5, segunda trava: num teste unilateral, espelhar a amostra tem que
    levar o p-valor para `1 - p`. Num bilateral os dois lados dao o MESMO
    p, e a soma daria `2p` -- nao 1."""
    amostra = [0.2, 0.0, 0.1]
    espelhada = [-v for v in amostra]
    assert _p_valor_unilateral(amostra) < 0.5
    assert (_p_valor_unilateral(amostra) + _p_valor_unilateral(espelhada)
            == pytest.approx(1.0))


def test_p_valor_sem_dispersao_real_segue_a_convencao_relativa_do_modulo():
    """F-4: a guarda era absoluta (`erro_padrao <= 0`), e valores
    praticamente identicos passavam com desvio de ruido de ponto flutuante
    -- p = 2,85e-33 e "evidencia a favor" sobre dispersao que nao existe.

    Mesma convencao RELATIVA de `core.b3_evidence.minimum_detectable_effect`:
    sem dispersao real nao ha erro-padrao a estimar, e o p-valor sai
    `None`."""
    quase_identicos = [0.30, 0.30, 0.30000000000000004, 0.29999999999999993]
    assert _p_valor_unilateral(quase_identicos) is None
    assert minimum_detectable_effect(quase_identicos) is None
    # e o veredito nao pode virar "a favor" em cima disso
    assert veredito_do_rank_ic(quase_identicos).estado != "evidencia_a_favor"


def test_a_conta_do_teste_t_mora_em_um_modulo_so():
    """F-3: `scipy` esta pinado em requirements.txt, entao o ramo de
    fallback era morto em producao -- nao servia de resiliencia, servia de
    armadilha: no dia em que o import falhasse por OUTRO motivo, um
    `except Exception` largo trocaria a estatistica em SILENCIO por uma que
    inverte o veredito. Erro tem que aparecer como erro.

    Rodada 2 (A-3): a varredura cobre TODOS os modulos que esta tela le
    para formar veredito, nao so este. O mesmo `try/except Exception` tinha
    sobrevivido em `core/b3_evidence.py` (aproximacao normal 1,2816/0,8416
    no efeito minimo detectavel) e em `core/b3_pooled_evidence.py` (erf no
    p-valor do universo) -- corrigir a guarda num arquivo e deixar os
    vizinhos e o defeito "guarda duplicada nao fica igual".

    Rodada 3 (N-2): a conta passou a morar em UM modulo so
    (`core.b3_evidence.teste_t_unilateral`). Enquanto era copiada, a copia
    divergiu na guarda de dispersao e os dois cards da tela publicaram
    vereditos opostos sobre os MESMOS Rank-ICs. Por isso a varredura agora
    exige as duas coisas: scipy no topo de `b3_evidence`, e scipy em lugar
    NENHUM dos outros dois -- quem quiser a distribuicao chama a funcao
    compartilhada.

    Inspecao por AST, nao por comportamento: duas copias que hoje
    coincidem passam em qualquer teste de comportamento e divergem na
    proxima edicao (`guarda-duplicada-diverge`)."""
    import ast
    from pathlib import Path

    def importa_scipy(arvore):
        return [no for no in ast.walk(arvore)
                if isinstance(no, (ast.Import, ast.ImportFrom))
                and any(nome.startswith("scipy") for nome in (
                    [(no.module or "")] if isinstance(no, ast.ImportFrom)
                    else [a.name for a in no.names]))]

    arvores = {}
    for modulo in ("b3_safras.py", "b3_evidence.py", "b3_pooled_evidence.py"):
        caminho = Path(__file__).parents[1] / "core" / modulo
        arvores[modulo] = ast.parse(caminho.read_text(encoding="utf-8"))

    topo = [no for no in arvores["b3_evidence.py"].body
            if isinstance(no, ast.ImportFrom)
            and (no.module or "").startswith("scipy")]
    assert topo, "scipy nao e importado no topo de core/b3_evidence.py"

    for modulo in ("b3_safras.py", "b3_pooled_evidence.py"):
        assert not importa_scipy(arvores[modulo]), (
            f"core/{modulo} voltou a importar scipy -- a conta do teste t "
            "tem que vir de core.b3_evidence, senao a guarda de dispersao "
            "volta a ser duas copias que divergem"
        )

    for modulo, arvore in arvores.items():
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Try):
                continue
            for interno in ast.walk(no):
                if isinstance(interno, ast.ImportFrom) and (
                        interno.module or "").startswith("scipy"):
                    raise AssertionError(
                        f"core/{modulo} ainda importa scipy dentro de um try "
                        "-- o fallback troca a distribuicao em silencio"
                    )


# ── Rodada de correção 2 da Task 6 — margem medida e zero conclusivo ──


def test_leave_one_out_publica_a_banda_de_p_valores_das_subamostras():
    """F-2 reaberto: contagem nao mede MARGEM. `p_banda` e o par
    `(min_i p(sem_i), max_i p(sem_i))` -- a distancia ate `alpha` que a
    conclusao manteve no pior e no melhor recorte. Reaproveitar o p-valor
    do conjunto inteiro daria uma banda degenerada de largura zero."""
    ic = [0.30, 0.32, 0.28, 0.31, 0.29, 0.33]
    out = fragilidade_leave_one_out(ic)

    esperados = [_p_valor_unilateral(ic[:i] + ic[i + 1:])
                 for i in range(len(ic))]
    assert out["p_loo"] == pytest.approx(esperados)
    assert out["p_banda"] == pytest.approx((min(esperados), max(esperados)))
    assert out["p_banda"][0] < out["p_banda"][1], (
        "banda de largura zero -- o p-valor nao foi recalculado por "
        "subamostra"
    )
    assert out["alpha"] == b3_safras.ALPHA_EVIDENCIA


def test_leave_one_out_nao_declara_robusto_sobre_veredito_inconclusivo():
    """A-2 no motor: `robusto` e o que a tela pinta de verde, e zero
    remocoes sobre um veredito INCONCLUSIVO e insensibilidade do teste,
    nao robustez -- 50,8% dos casos inconclusivos (n=8, mu=0,02) saiam com
    "0 safra(s)" em verde ao lado de "Inconclusivo"."""
    ic = [0.02, -0.05, 0.10, -0.03, 0.06, 0.01]
    out = fragilidade_leave_one_out(ic)

    assert out["estado_completo"] == "inconclusivo"
    assert out["conclusivo"] is False
    assert out["safras_que_viram"] == 0
    assert out["robusto"] is False, (
        "declarou robustez sobre uma nao-conclusao"
    )

    # caso oposto: amostra conclusiva e sem viradas volta a ser robusta
    forte = fragilidade_leave_one_out([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])
    assert forte["conclusivo"] is True
    assert forte["robusto"] is True


def test_leave_one_out_publica_a_populacao_junto_da_contagem():
    """"0" sem denominador nao distingue robustez de amostra pequena:
    `n_safras` e o tamanho da populacao DEPOIS da limpeza, e sai tambem
    abaixo do piso, onde e justamente ele que explica o "—"."""
    assert fragilidade_leave_one_out([0.3, 0.32, 0.28, 0.31])["n_safras"] == 4
    curto = fragilidade_leave_one_out([0.3, 0.32])
    assert curto["medido"] is False and curto["n_safras"] == 2
    assert fragilidade_leave_one_out(
        [0.3, None, 0.32, np.nan, 0.28, 0.31])["n_safras"] == 4


def test_ic_limpo_e_o_criterio_de_finitude_nao_o_de_nulidade():
    """A-4: `pd.notna(inf)` e True e `np.isfinite(inf)` e False. Contar a
    populacao com um criterio e aplicar o teste com o outro publica um
    denominador que o motor nunca usou -- e `inf` na amostra do t nao da
    erro, da p-valor `nan` e veredito silenciosamente inconclusivo."""
    import pandas as pd

    com_inf = [0.30, float("inf"), 0.32, 0.28]
    assert [pd.notna(v) for v in com_inf] == [True, True, True, True]
    assert b3_safras._ic_limpos(com_inf) == [0.30, 0.32, 0.28]
    assert fragilidade_leave_one_out(com_inf)["n_safras"] == 3


def test_bootstrap_tambem_filtra_por_finitude_nao_por_nulidade():
    """Achado colateral da rodada 2: a mesma divergencia `pd.notna` x
    `np.isfinite` do A-4 existe na reamostragem, e ali `inf` nao fica
    escondido -- ele contamina a MEDIA de cada reamostra e a banda inteira
    sai `inf`, um numero publicado no card sem nenhuma excecao pelo
    caminho. Nenhum teste separava os dois criterios aqui."""
    baixo, alto = bootstrap_excesso([10.0, float("inf"), 12.0, 11.0, 9.0])
    assert baixo is not None
    assert np.isfinite(baixo) and np.isfinite(alto), (
        "a banda saiu infinita -- o filtro da reamostragem aceitou inf"
    )
    assert 8.0 < baixo <= alto < 13.0


def test_os_dois_blocos_da_tela_nao_divergem_sobre_os_mesmos_ics_anuais():
    """N-2: desde a rodada 2 o Bloco 3 e o bloco "Evidencia no universo"
    leem os MESMOS Rank-ICs anuais -- e foi isso que expos que cada um
    tinha a sua guarda de dispersao. `_p_valor_unilateral` usava a guarda
    RELATIVA; `universe_evidence` ficou com `desvio > 0`. Sobre os ICs
    {2018..2023} todos iguais a 0,30 exceto um 0,30+1e-12, um card
    publicava `evidencia_a_favor` com p=5,0e-61 e o outro "Inconclusivo".

    Um teste SO, comparando os dois caminhos sobre a mesma entrada. Dois
    testes paralelos (um por caminho) envelheceriam separados e e
    exatamente assim que a divergencia nasceu."""
    from core.b3_pooled_evidence import universe_evidence

    amostras = [
        # dispersao de ruido de ponto flutuante: o caso medido
        [0.30, 0.30, 0.30, 0.30, 0.30, 0.30 + 1e-12],
        [0.30] * 6,
        [0.2, 0.0, 0.1],
        [0.30, 0.32, 0.28, 0.31, 0.29, 0.33],
        [-0.1, -0.1, -0.1, -0.5],
        [0.10, -0.08, 0.04],
        [-0.20],                       # um ano so: amplitude insuficiente
        [0.05],
        [],
    ]
    for ic in amostras:
        bloco3 = veredito_do_rank_ic(ic)
        universo = universe_evidence(list(ic))
        assert bloco3.estado == universo.estado, (
            f"os dois blocos divergem sobre {ic}: Bloco 3 diz "
            f"{bloco3.estado!r} e o universo diz {universo.estado!r}"
        )


def test_implicacao_da_banda_de_um_lado_e_presa_por_teste_nao_por_docstring():
    """A banda so nao reprova hoje porque `classify_evidence` decide pelo
    p-valor: com zero viradas sobre veredito conclusivo, todo p(sem_i) ja
    esta do mesmo lado de alpha. Isso e uma IMPLICACAO, nao uma constante
    -- e implicacao afirmada so na docstring e `gate-que-so-dava-false`
    com o sinal invertido: no dia em que o criterio deixar de ser o
    p-valor, a condicao passa a morder em silencio.

    Este teste falha se a implicacao parar de valer:
    conclusivo ∧ viram == 0 ⟹ banda_de_um_lado is not False."""
    rng = np.random.default_rng(20260922)
    vistos = {"conclusivo_sem_virada": 0}
    for _ in range(4000):
        n = int(rng.integers(MIN_SAFRAS_LOO, 17))
        mu = float(rng.uniform(-0.25, 0.25))
        amostra = (mu + rng.normal(0.0, float(rng.uniform(0.01, 0.30)), n))
        loo = fragilidade_leave_one_out([float(v) for v in amostra])
        if not (loo["conclusivo"] and loo["safras_que_viram"] == 0):
            continue
        vistos["conclusivo_sem_virada"] += 1
        assert loo["banda_de_um_lado"] is not False, (
            "a banda passou a atravessar alpha sobre uma conclusao com zero "
            f"viradas -- a condicao deixou de ser inerte em {list(amostra)}"
        )
    assert vistos["conclusivo_sem_virada"] > 500, (
        "a amostragem nao exercitou o caso que a implicacao descreve "
        f"({vistos['conclusivo_sem_virada']} casos)"
    )

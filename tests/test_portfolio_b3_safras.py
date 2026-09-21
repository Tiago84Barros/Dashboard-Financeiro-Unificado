"""Testes da rodada de correção 1 da Task 5 (views/portfolio_b3_safras.py).

Todos os testes chamam as funções puras extraídas do módulo
(`_resumo_safras`, `_legenda_resumo`, `_tabela_para_exibicao`,
`_grafico_barras`) — nunca `render_safras` via AppTest, que nesta base
vaza atribuição de módulo e só falha dentro da suíte completa no CI (nota
de memória `apptest-vaza-atribuicao-de-modulo`). As tabelas de entrada são
montadas à mão com o schema de `core.b3_safras.COLUNAS_TABELA`, sem passar
por Streamlit nem pelo motor.
"""
import numpy as np
import pandas as pd

from views.portfolio_b3_safras import (
    _CORES_SERIE,
    _grafico_barras,
    _legenda_resumo,
    _resumo_safras,
    _tabela_para_exibicao,
)


def _linha(safra, *, completa, mensuravel, estrategia=10.0, ew=8.0,
          selic=6.0, excesso=4.0, peso_ausente=0.0):
    if not mensuravel:
        estrategia = ew = selic = excesso = np.nan
        peso_ausente = 100.0  # core/b3_safras.py: nada observado = 100%
    return {
        "Safra": safra, "Exercício-base": safra - 1,
        "Janela": f"abr/{safra} a mar/{safra + 1}",
        "Completa": completa, "Mensurável": mensuravel,
        "Segmentos": 1, "Ativos": 3, "Maiores posições": "AAA, BBB, CCC",
        "Estratégia (%)": estrategia, "Equal-weight (%)": ew,
        "Selic (%)": selic, "Excesso s/ Selic (pp)": excesso,
        "Peso sem preço (%)": peso_ausente, "Universo com preço": 3,
    }


def _tabela(linhas, safras_completas):
    df = pd.DataFrame(linhas)
    df.attrs["safras_completas"] = safras_completas
    return df


# ── C-1: população das médias (attrs["safras_completas"], não "Completa") ──


def test_resumo_usa_attrs_safras_completas_nao_completa_da_tabela():
    """Mutação-alvo: trocar `tabela["Safra"].isin(safras_medidas)` por
    `tabela["Completa"]` sozinha reintroduz o bug que a Task 4 fechou —
    uma safra com janela fechada e zero pregão observado (Completa=True,
    Mensurável=False) entraria na média. Aqui a safra 2020 está nessa
    situação e NÃO está em `safras_completas`; só a 2021 está.
    """
    tabela = _tabela(
        [
            _linha(2020, completa=True, mensuravel=False),
            _linha(2021, completa=True, mensuravel=True),
        ],
        safras_completas=[2021],
    )
    resumo = _resumo_safras(tabela)
    assert resumo["n_medidas"] == 1
    assert resumo["safra_min"] == 2021
    assert resumo["safra_max"] == 2021
    assert list(resumo["completas"]["Safra"]) == [2021]


# ── I-2: a frase sobre a safra vigente é derivada do dado, não do calendário ──


def test_legenda_relata_safra_parcial_quando_ha_linha_incompleta():
    """Reproduz jan-mar (I-2): `safra_vigente_em` devolve o ano anterior,
    que entra no range que `views/portfolio_b3.py` itera, e a safra
    vigente aparece como linha `Completa=False`. A legenda tem que dizer
    isso, não negar que ela exista."""
    tabela = _tabela(
        [
            _linha(2020, completa=True, mensuravel=True),
            _linha(2021, completa=False, mensuravel=False),
        ],
        safras_completas=[2020],
    )
    resumo = _resumo_safras(tabela)
    legenda = _legenda_resumo(resumo)
    assert "2021" in legenda
    assert "está nesta tabela com a janela em curso" in legenda
    assert "não aparece nesta tabela" not in legenda


def test_legenda_nega_safra_vigente_quando_nao_ha_linha_incompleta():
    """Contraparte: de abril a dezembro (sem linha `Completa=False`), a
    afirmação de que a safra vigente não aparece nesta tabela é
    verdadeira e a legenda deve mantê-la."""
    tabela = _tabela(
        [
            _linha(2020, completa=True, mensuravel=True),
            _linha(2021, completa=True, mensuravel=True),
        ],
        safras_completas=[2020, 2021],
    )
    resumo = _resumo_safras(tabela)
    legenda = _legenda_resumo(resumo)
    assert "não aparece nesta tabela" in legenda


# ── I-1: "peso sem preço" agrega só sobre as safras medidas ──


def test_peso_ausente_max_ignora_safra_nao_medida():
    """Mutação-alvo: `tabela["Peso sem preço (%)"].max()` sobre a tabela
    inteira pegaria os 100,0% da safra 2020 (não mensurável, nada
    observado) em vez dos 5,0% da única safra de fato medida."""
    tabela = _tabela(
        [
            _linha(2020, completa=True, mensuravel=False),
            _linha(2021, completa=True, mensuravel=True, peso_ausente=5.0),
        ],
        safras_completas=[2021],
    )
    resumo = _resumo_safras(tabela)
    assert resumo["peso_ausente_max"] == 5.0


# ── m-1: o intervalo da legenda usa as safras medidas, não a tabela inteira ──


def test_legenda_intervalo_usa_safras_medidas_nao_tabela_inteira():
    """Mutação-alvo: `tabela['Safra'].min()/.max()` sobre a tabela inteira
    incluiria 2010 (não mensurável) no intervalo, contradizendo a
    contagem de safras medidas na mesma frase."""
    tabela = _tabela(
        [
            _linha(2010, completa=True, mensuravel=False),
            _linha(2020, completa=True, mensuravel=True),
            _linha(2021, completa=True, mensuravel=True),
        ],
        safras_completas=[2020, 2021],
    )
    resumo = _resumo_safras(tabela)
    legenda = _legenda_resumo(resumo)
    assert "de 2020 a 2021" in legenda
    assert "2010" not in legenda


def test_legenda_sem_safra_medida_nao_publica_intervalo_vazio():
    tabela = _tabela(
        [_linha(2020, completa=True, mensuravel=False)],
        safras_completas=[],
    )
    resumo = _resumo_safras(tabela)
    assert resumo["n_medidas"] == 0
    legenda = _legenda_resumo(resumo)
    assert "Nenhuma safra encerrada e mensurável ainda" in legenda


# ── m-2: retorno NaN vira travessão na tabela exibida, não célula vazia ──


def test_tabela_exibicao_troca_nan_por_traco():
    tabela = _tabela(
        [_linha(2020, completa=True, mensuravel=False)],
        safras_completas=[],
    )
    exibicao = _tabela_para_exibicao(tabela)
    assert exibicao.loc[0, "Estratégia (%)"] == "—"
    assert exibicao.loc[0, "Excesso s/ Selic (pp)"] == "—"


def test_tabela_exibicao_formata_valor_medido_com_uma_casa():
    tabela = _tabela(
        [_linha(2021, completa=True, mensuravel=True, estrategia=12.345)],
        safras_completas=[2021],
    )
    exibicao = _tabela_para_exibicao(tabela)
    assert exibicao.loc[0, "Estratégia (%)"] == "12.3"


# ── I-3: o gráfico usa o tema/cores compartilhados da aba, não os default do plotly ──


def test_grafico_barras_usa_plot_layout_transparente():
    tabela = _tabela(
        [_linha(2020, completa=True, mensuravel=True),
         _linha(2021, completa=True, mensuravel=True)],
        safras_completas=[2020, 2021],
    )
    fig = _grafico_barras(_resumo_safras(tabela)["completas"])
    assert fig.layout.height == 360
    assert fig.layout.paper_bgcolor == "rgba(0,0,0,0)"
    assert fig.layout.plot_bgcolor == "rgba(0,0,0,0)"


def test_grafico_barras_usa_cores_da_aba_para_cada_serie():
    """Mutação-alvo: remover `color_discrete_map` deixa o plotly escolher
    cores default, e a série "Selic" passaria a divergir da cor usada
    para "Selic" no gráfico vizinho (Desempenho da safra vigente)."""
    tabela = _tabela(
        [_linha(2020, completa=True, mensuravel=True),
         _linha(2021, completa=True, mensuravel=True)],
        safras_completas=[2020, 2021],
    )
    fig = _grafico_barras(_resumo_safras(tabela)["completas"])
    cores_por_serie = {trace.name: trace.marker.color for trace in fig.data}
    assert cores_por_serie == _CORES_SERIE

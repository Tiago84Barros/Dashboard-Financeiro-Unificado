"""Testes das Tasks 5 e da rodada de correção 2 (views/portfolio_b3_safras.py).

Todos os testes chamam as funções puras extraídas do módulo
(`_resumo_safras`, `_legenda_resumo`, `_tabela_para_exibicao`,
`_column_config_retorno`, `_grafico_barras`) — nunca `render_safras` via
AppTest, que nesta base vaza atribuição de módulo e só falha dentro da
suíte completa no CI (nota de memória `apptest-vaza-atribuicao-de-modulo`).
As tabelas de entrada são montadas à mão com o schema de
`core.b3_safras.COLUNAS_TABELA`, sem passar por Streamlit nem pelo motor.
"""
import ast
from pathlib import Path

import numpy as np
import pandas as pd

from core.b3_safras import COLUNAS_TABELA
from views.portfolio_b3_safras import (
    _COLUNAS_RETORNO,
    _CORES_SERIE,
    _column_config_retorno,
    _grafico_barras,
    _legenda_resumo,
    _resumo_safras,
    _tabela_para_exibicao,
)

RAIZ = Path(__file__).parents[1]


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
    incluiria 2010 (não mensurável, no PISO) e 2030 (não mensurável, no
    TETO) no intervalo, contradizendo a contagem de safras medidas na
    mesma frase. A re-revisão (m-1) provou que o fixture anterior só
    cobria o piso: mutar `safra_max` de volta para `tabela['Safra'].max()`
    deixava a suíte inteira verde porque não havia safra não mensurável
    acima do intervalo medido. Este fixture tem as duas pontas."""
    tabela = _tabela(
        [
            _linha(2010, completa=True, mensuravel=False),
            _linha(2020, completa=True, mensuravel=True),
            _linha(2021, completa=True, mensuravel=True),
            _linha(2030, completa=True, mensuravel=False),
        ],
        safras_completas=[2020, 2021],
    )
    resumo = _resumo_safras(tabela)
    legenda = _legenda_resumo(resumo)
    # 2010 e 2030 são órfãs (Completa=True, Mensurável=False) e por isso
    # aparecem noutra frase da legenda (achado N-3) — o que este teste
    # prende é a frase de INTERVALO em si, não a ausência total dos anos
    # em qualquer lugar do texto.
    assert "2 safra(s) já encerrada(s) e mensurável(is), de 2020 a 2021." in legenda


def test_legenda_sem_safra_medida_nao_publica_intervalo_vazio():
    tabela = _tabela(
        [_linha(2020, completa=True, mensuravel=False)],
        safras_completas=[],
    )
    resumo = _resumo_safras(tabela)
    assert resumo["n_medidas"] == 0
    legenda = _legenda_resumo(resumo)
    assert "Nenhuma safra encerrada e mensurável ainda" in legenda


# ── N-3: a safra órfã (Completa=True, Mensurável=False) ganha frase própria ──


def test_legenda_explica_safra_orfa_completa_mas_nao_mensuravel():
    """Reproduz o achado N-3: a safra `Completa=True, Mensurável=False`
    (o mesmo caso do I-1) não cai em `completas` (não foi medida) nem em
    `parciais` (a janela FECHOU) — fica muda nas duas frases anteriores da
    legenda, e a linha aparece em branco na tabela sem explicação.
    Mutação-alvo: remover o bloco de `orfas` de `_legenda_resumo` faz
    este teste falhar."""
    tabela = _tabela(
        [
            _linha(2020, completa=True, mensuravel=False),
            _linha(2021, completa=True, mensuravel=True),
        ],
        safras_completas=[2021],
    )
    resumo = _resumo_safras(tabela)
    assert list(resumo["orfas"]["Safra"]) == [2020]
    legenda = _legenda_resumo(resumo)
    assert "A safra 2020 tem a janela fechada" in legenda
    assert "nenhum pregão foi observado" in legenda


def test_legenda_nao_menciona_orfa_quando_nao_ha_nenhuma():
    """Contraparte: sem safra órfã, a frase de N-3 não deve aparecer."""
    tabela = _tabela(
        [_linha(2021, completa=True, mensuravel=True)],
        safras_completas=[2021],
    )
    resumo = _resumo_safras(tabela)
    assert resumo["orfas"].empty
    legenda = _legenda_resumo(resumo)
    assert "janela fechada, mas nenhum pregão" not in legenda


# ── N-1/m-2: NaN não vira texto (quebrava a ordenação); formatação fica ──
# ── no column_config, sem tocar no dtype numérico das colunas de retorno ──


def test_tabela_exibicao_preserva_dtype_numerico_para_ordenar_corretamente():
    """Mutação-alvo: voltar a
    `.map(lambda v: "—" if pd.isna(v) else f"{v:.1f}")` (o bug do N-1)
    torna a coluna `object`/string, e ordenar por ela passa a comparar
    TEXTO em vez de número. Reproduzido pela revisão: ordenar "-3,0,
    100,0, 9,0, 10,0" em texto devolve "-3.0 → 10.0 → 100.0 → 9.0" — a
    safra de +9 pp aparece ACIMA da de +100 pp. Aqui provamos que o dtype
    segue numérico (`is_numeric_dtype`) e que `sort_values` ordena pelo
    valor, não pelo texto: se a coluna virasse string, `sort_values`
    devolveria a ordem lexicográfica acima em vez da numérica."""
    tabela = _tabela(
        [
            _linha(2020, completa=True, mensuravel=True, estrategia=-3.0),
            _linha(2021, completa=True, mensuravel=True, estrategia=100.0),
            _linha(2022, completa=True, mensuravel=True, estrategia=9.0),
            _linha(2023, completa=True, mensuravel=True, estrategia=10.0),
        ],
        safras_completas=[2020, 2021, 2022, 2023],
    )
    exibicao = _tabela_para_exibicao(tabela)
    assert pd.api.types.is_numeric_dtype(exibicao["Estratégia (%)"])
    ordenada = list(exibicao.sort_values("Estratégia (%)")["Estratégia (%)"])
    assert ordenada == [-3.0, 9.0, 10.0, 100.0]


def test_tabela_exibicao_mantem_nan_numerico_sem_texto_na_celula():
    """A ausência de retorno (safra não mensurável) não vira texto "—"
    dentro da célula — isso é o que quebrava a ordenação (N-1). O motivo
    já está do lado, na coluna "Mensurável"; a célula segue `NaN`
    numérico, e é o `st.column_config.NumberColumn` (testado abaixo) que
    cuida da formatação visual sem alterar o dtype."""
    tabela = _tabela(
        [_linha(2020, completa=True, mensuravel=False)],
        safras_completas=[],
    )
    exibicao = _tabela_para_exibicao(tabela)
    assert pd.isna(exibicao.loc[0, "Estratégia (%)"])
    assert not bool(exibicao.loc[0, "Mensurável"])


def test_column_config_retorno_formata_como_numero_sem_mudar_dtype():
    """`_column_config_retorno` tem que cobrir exatamente as 4 colunas de
    retorno com `NumberColumn` (tipo "number" no config do Streamlit,
    não "text") — é essa configuração, aplicada em cima de uma coluna que
    continua numérica, que resolve a formatação (1 casa) sem reintroduzir
    o bug de ordenação do N-1."""
    config = _column_config_retorno()
    assert set(config) == set(_COLUNAS_RETORNO)
    for coluna, cfg in config.items():
        assert cfg["type_config"]["type"] == "number", coluna
        assert cfg["type_config"]["format"] == "%.1f", coluna


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


def test_render_safras_passa_column_config_ao_st_dataframe():
    """Achado NOVO-1 (rodada de correção 3): `_column_config_retorno` é
    testada isolada (`test_column_config_retorno_formata_como_numero_sem_mudar_dtype`),
    mas nada prendia o fato de ela ser *usada* dentro de `render_safras`.
    Remover `column_config=_column_config_retorno()` da chamada
    `st.dataframe` deixava a suíte inteira verde — a tabela volta a
    imprimir `9.130000000000001` sem erro nenhum, porque a formatação é
    só visual e nenhum teste chama `render_safras`.

    Inspeção por AST do código-fonte, não `AppTest` — nesta base o
    AppTest vaza atribuição de módulo e só falha dentro da suíte
    completa no CI, nunca isolado (nota de memória
    `apptest-vaza-atribuicao-de-modulo`)."""
    caminho = RAIZ / "views" / "portfolio_b3_safras.py"
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    render = next(
        no for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef) and no.name == "render_safras"
    )
    chamadas_dataframe = [
        no for no in ast.walk(render)
        if isinstance(no, ast.Call)
        and isinstance(no.func, ast.Attribute)
        and no.func.attr == "dataframe"
    ]
    assert chamadas_dataframe, "render_safras nao chama st.dataframe"
    kwargs_passados = {
        kw.arg for chamada in chamadas_dataframe for kw in chamada.keywords
    }
    assert "column_config" in kwargs_passados, (
        "st.dataframe em render_safras nao recebe column_config -- a "
        "formatacao de 1 casa de _column_config_retorno() nao esta cabeada"
    )


def test_colunas_retorno_esta_contida_em_colunas_tabela():
    """Achado NOVO-1, segundo modo de falha: `st.dataframe` ignora em
    silêncio qualquer chave de `column_config` que não corresponda a
    nenhuma coluna da tabela exibida. Um rename em `_COLUNAS_RETORNO` (ou
    em `core.b3_safras.COLUNAS_TABELA`) sem atualizar o outro lado apaga a
    formatação de `_column_config_retorno` sem nenhum erro, nenhuma
    exceção e nenhum teste vermelho -- exceto este, que prende a
    inclusão diretamente."""
    assert set(_COLUNAS_RETORNO) <= set(COLUNAS_TABELA), (
        "_COLUNAS_RETORNO tem coluna fora de core.b3_safras.COLUNAS_TABELA -- "
        "a formatacao dessa coluna em _column_config_retorno() seria "
        "ignorada em silencio pelo st.dataframe"
    )

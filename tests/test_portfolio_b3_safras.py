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
import contextlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import streamlit as _st_real

from core.b3_safras import COLUNAS_TABELA
from views.portfolio_b3_safras import (
    _COLUNAS_RETORNO,
    _COLUNAS_VIES,
    _CORES_SERIE,
    _causa_sem_teste,
    _column_config_retorno,
    _column_config_vies,
    _expectativa,
    _fmt_p,
    _grafico_barras,
    _legenda_resumo,
    _resumo_safras,
    _tabela_para_exibicao,
    _vies_universo,
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


# ── Task 6: Bloco 3 — banda (bootstrap) e fragilidade (leave-one-out) ──


_M_ATIVOS = 40


def _pares_do_ano(ano, ic_alvo, *, m=_M_ATIVOS):
    """Pares `(ano, score, retorno)` cujo Rank-IC do ano bate `ic_alvo`.

    A população do Bloco 3 passou a ser `ic_pairs` (rodada 2, A-1), então o
    helper dos testes tem que falar a mesma língua da tela: entrega PARES e
    deixa `pooled_yearly_ics` reduzir, em vez de entregar o IC já reduzido
    por um caminho que a tela não usa. Busca binária sobre uma mistura
    monótona de score ordenado com ruído permutado; erro medido < 0,005.
    """
    from core.b3_pooled_evidence import pooled_yearly_ics

    rng = np.random.default_rng(1000 + ano)
    scores = np.arange(m, dtype=float)
    ruido = rng.permutation(m).astype(float)
    baixo, alto, a = -1.5, 1.5, 0.0
    for _ in range(60):
        a = (baixo + alto) / 2
        retornos = a * scores + (1 - abs(a)) * ruido
        ic = pooled_yearly_ics(
            [(ano, s, r) for s, r in zip(scores, retornos)])[ano]
        if ic < ic_alvo:
            baixo = a
        else:
            alto = a
    retornos = a * scores + (1 - abs(a)) * ruido
    return [(ano, float(sc), float(rt)) for sc, rt in zip(scores, retornos)]


def _res_ic(ic_values, *, ano0=2000, segmento="S", com_pares=True):
    """Resultado de um segmento com os Rank-ICs anuais pedidos.

    Mantém `rank_ic_values` preenchido DE PROPÓSITO: é a lista por
    segmento que a tela lia antes e não pode voltar a ler (A-1). Um teste
    que só entregasse `ic_pairs` deixaria o fallback passar despercebido.
    """
    pares = []
    for i, ic in enumerate(ic_values):
        if ic is None or (isinstance(ic, float) and np.isnan(ic)):
            continue
        pares.extend(_pares_do_ano(ano0 + i, float(ic)))
    res = {"segmento": segmento, "rank_ic_values": list(ic_values)}
    if com_pares:
        res["ic_pairs"] = pares
    return res


def test_expectativa_usa_attrs_safras_completas_nao_completa_da_tabela():
    """Mutação-alvo: trocar a leitura de `attrs["safras_completas"]` por
    `tabela[tabela["Completa"]]` (o que o rascunho da task propunha) faz a
    safra 2020 — janela civil fechada, mas fora da população mensurável —
    entrar na banda com +99 pp e jogar o intervalo todo para cima.

    A banda é uma afirmação sobre excesso MEDIDO; a população dela tem que
    ser a mesma das médias do Bloco 1, e essa população é `attrs`."""
    linhas = [_linha(2021, completa=True, mensuravel=True, excesso=4.0),
              _linha(2022, completa=True, mensuravel=True, excesso=5.0),
              _linha(2023, completa=True, mensuravel=True, excesso=3.0),
              _linha(2020, completa=True, mensuravel=True, excesso=99.0)]
    tabela = _tabela(linhas, [2021, 2022, 2023])

    out = _expectativa([_res_ic([0.1, 0.2, 0.15])], tabela)

    assert out["n_safras_banda"] == 3
    baixo, alto = out["banda"]
    assert alto < 0.06, (
        "a banda subiu acima de 6% -- a safra fora de safras_completas "
        "entrou na reamostragem"
    )
    assert baixo > 0


def test_expectativa_sem_safra_medida_nao_publica_banda():
    """Nenhum número sem evidência medida por trás: sem safra mensurável a
    banda sai `(None, None)` e o texto sai "—", nunca um ponto central
    fabricado para preencher o card."""
    tabela = _tabela([_linha(2026, completa=False, mensuravel=True)], [])

    out = _expectativa([_res_ic([0.1, 0.2])], tabela)

    assert out["banda"] == (None, None)
    assert out["texto_banda"] == "—"
    assert out["n_safras_banda"] == 0


def test_limitacao_da_banda_e_derivada_da_contagem_medida():
    """Texto de limitação derivado da MEDIÇÃO, nunca escrito à mão: com uma
    única safra mensurável a frase tem que dizer "1 safra", e com nenhuma
    tem que dizer "nenhuma". Uma frase fixa ("ainda não há safras
    encerradas") envelheceria invertida e continuaria soando como rigor."""
    uma = _expectativa(
        [_res_ic([0.1, 0.2])],
        _tabela([_linha(2023, completa=True, mensuravel=True)], [2023]),
    )
    assert uma["banda"] == (None, None)
    assert "1 safra" in uma["limitacao_banda"]

    nenhuma = _expectativa(
        [_res_ic([0.1, 0.2])],
        _tabela([_linha(2026, completa=False, mensuravel=True)], []),
    )
    assert "nenhuma safra" in nenhuma["limitacao_banda"].lower()


def test_aviso_de_banda_atravessando_zero_cita_os_limites_medidos():
    linhas = [_linha(2020 + i, completa=True, mensuravel=True, excesso=e)
              for i, e in enumerate([20.0, -18.0, 15.0, -22.0, 5.0])]
    tabela = _tabela(linhas, [2020, 2021, 2022, 2023, 2024])

    out = _expectativa([_res_ic([0.1, 0.2, 0.15, 0.05, 0.12])], tabela)

    baixo, alto = out["banda"]
    assert baixo < 0 < alto
    aviso = " ".join(out["avisos"])
    assert "atravessa o zero" in aviso
    assert f"{baixo:+.1%}" in aviso and f"{alto:+.1%}" in aviso


def test_sem_aviso_de_banda_quando_ela_nao_atravessa_zero():
    linhas = [_linha(2020 + i, completa=True, mensuravel=True, excesso=e)
              for i, e in enumerate([10.0, 12.0, 11.0, 9.0, 13.0])]
    tabela = _tabela(linhas, [2020, 2021, 2022, 2023, 2024])

    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])], tabela)

    assert all("atravessa o zero" not in a for a in out["avisos"])


def test_aviso_de_fragilidade_conta_as_safras_que_viram():
    """O veredito da B3 já virou para APROVADO por 0,004 e reprovava de
    novo ao tirar uma safra. Quando o leave-one-out acha essa dependência,
    ela tem que chegar à tela com o número de safras que a produzem."""
    tabela = _tabela(
        [_linha(2020 + i, completa=True, mensuravel=True, excesso=4.0)
         for i in range(3)],
        [2020, 2021, 2022],
    )

    out = _expectativa([_res_ic([0.02, 0.01, 0.0, 0.01, 0.60])], tabela)

    assert out["loo"]["safras_que_viram"] >= 1
    aviso = " ".join(out["avisos"])
    assert f"{out['loo']['safras_que_viram']} dos 5 anos" in aviso


def test_sem_aviso_de_fragilidade_quando_nenhuma_safra_vira():
    tabela = _tabela(
        [_linha(2020 + i, completa=True, mensuravel=True, excesso=4.0)
         for i in range(3)],
        [2020, 2021, 2022],
    )

    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])], tabela)

    assert out["loo"]["safras_que_viram"] == 0
    assert all("veredito muda" not in a for a in out["avisos"])


def test_expectativa_junta_os_pares_de_todos_os_segmentos_por_ano():
    """Os pares vêm de vários segmentos e a redução é por ANO: dois
    segmentos cobrindo anos diferentes somam anos; ano sem IC calculável
    (`None`/`NaN` no lado do motor) não vira observação."""
    tabela = _tabela([_linha(2023, completa=True, mensuravel=True)], [2023])

    out = _expectativa(
        [_res_ic([0.1, None, 0.2]), _res_ic([0.3], ano0=2010)], tabela)

    assert out["anos"] == [2000, 2002, 2010]
    assert out["ic_values"] == pytest.approx([0.1, 0.2, 0.3], abs=5e-3)
    assert out["veredito"].anos_medidos == 3


def test_render_expectativa_e_chamada_por_render_safras():
    """`_expectativa` testada isolada não prende o cabeamento: deletar a
    chamada de `render_expectativa` em `render_safras` apaga o Bloco 3
    inteiro da tela e deixa a suíte verde. Inspeção por AST, não AppTest
    (nota de memória `apptest-vaza-atribuicao-de-modulo`)."""
    caminho = RAIZ / "views" / "portfolio_b3_safras.py"
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    render = next(
        no for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef) and no.name == "render_safras"
    )
    chamadas = {no.func.id for no in ast.walk(render)
                if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)}
    assert "render_expectativa" in chamadas, (
        "render_safras nao chama render_expectativa -- o Bloco 3 nao "
        "aparece na tela e nenhum outro teste percebe"
    )


# ── Rodada de correção 1 da Task 6 — F-1 e F-2 no que a tela publica ──


def _tabela_medida(n=3):
    return _tabela(
        [_linha(2020 + i, completa=True, mensuravel=True, excesso=4.0)
         for i in range(n)],
        [2020 + i for i in range(n)],
    )


def test_card_ordena_usa_o_mesmo_criterio_do_leave_one_out():
    """F-1 na tela: o veredito impresso no card "Ordena?" e o veredito que o
    card de fragilidade testa tem que ser o MESMO. Com Rank-IC ~0,30 em 6
    safras, o card imprimia "Inconclusivo" enquanto o LOO concluía
    `evidencia_a_favor` — dois critérios homônimos com vereditos opostos
    lado a lado na mesma tela, e nenhum teste comparava os caminhos."""
    for ic in ([0.30, 0.32, 0.28, 0.31, 0.29, 0.33],
               [0.02, 0.01, 0.0, 0.01, 0.60],
               [-0.20, -0.18, -0.25, -0.22]):
        out = _expectativa([_res_ic(ic)], _tabela_medida())
        assert out["veredito"].estado == out["loo"]["estado_completo"], (
            f"card e LOO divergem em {ic}"
        )

    forte = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                         _tabela_medida())
    assert forte["veredito"].estado == "evidencia_a_favor", (
        "unificar os dois caminhos no critério SEM p-valor deixaria "
        "'evidência a favor' inalcançável no card"
    )


def test_card_de_fragilidade_nao_sai_verde_abaixo_do_piso():
    """F-2 na tela: "0 safra(s)" em verde tem duas causas opostas —
    evidência robusta, ou amostra pequena demais para haver o que remover.
    Abaixo do piso o card sai "—" e SEM cor positiva; o verde é reservado a
    zero medido."""
    for ic in ([0.30], [0.30, 0.32], [0.30, 0.32, 0.28]):
        out = _expectativa([_res_ic(ic)], _tabela_medida())
        assert out["texto_fragilidade"] == "—", f"publicou número com {ic}"
        assert out["positivo_fragilidade"] is None, (
            f"card de fragilidade saiu colorido com n={len(ic)}"
        )


def test_card_de_fragilidade_sai_verde_quando_zero_foi_medido():
    """Caso oposto do piso: acima dele, zero safras que viram volta a ser um
    resultado medido e o card volta a sair verde. Sem esta metade, tornar o
    piso inalcançável apagaria o card inteiro sem teste vermelho."""
    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                       _tabela_medida())
    assert out["loo"]["safras_que_viram"] == 0
    assert out["texto_fragilidade"] == "0 de 6 anos", (
        "o card tem que publicar a POPULACAO junto do zero -- "
        "'0 safra(s)' nao distingue robustez de amostra pequena"
    )
    assert out["positivo_fragilidade"] is True


def test_render_expectativa_le_o_card_de_fragilidade_da_funcao_pura():
    """`texto_fragilidade`/`positivo_fragilidade` testados isolados não
    prendem o cabeamento: `render_expectativa` poderia voltar a montar o
    texto na mão e o piso do F-2 sumiria da tela com a suíte verde.
    Inspeção por AST (nota de memória `apptest-vaza-atribuicao-de-modulo`)."""
    caminho = RAIZ / "views" / "portfolio_b3_safras.py"
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    render = next(
        no for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef) and no.name == "render_expectativa"
    )
    lidas = {no.slice.value for no in ast.walk(render)
             if isinstance(no, ast.Subscript)
             and isinstance(no.slice, ast.Constant)
             and isinstance(no.slice.value, str)}
    assert {"texto_fragilidade", "positivo_fragilidade"} <= lidas, (
        "render_expectativa nao le o card de fragilidade de _expectativa -- "
        f"leu apenas {sorted(lidas)}"
    )


# ── Rodada de correção 2 da Task 6 — a POPULAÇÃO do veredito (A-1, A-2) ──


def test_veredito_nao_escala_com_o_numero_de_segmentos():
    """A-1, o defeito maior da rodada: `ic_values` concatenava
    `rank_ic_values` de todos os segmentos, então `n` era *segmento × ano*.

    Replicar os MESMOS ICs anuais por k segmentos não acrescenta
    informação nenhuma — é a mesma ordenação de mercado recontada — mas
    multiplicava a amostra do teste t por k e inflava a significância por
    √k. Medido antes da correção: 8 ICs em 20 segmentos moviam a tela de
    "Inconclusivo / fragilidade 2 sem cor" para "Evidência a favor /
    fragilidade 0 em VERDE", com a ajuda dizendo "160 ano(s)".

    Este teste falha se a população voltar a escalar com k."""
    ic = [0.02, -0.05, 0.10, -0.03, 0.06, 0.01, 0.04, -0.02]
    tabela = _tabela_medida()
    base = _res_ic(ic)

    referencia = None
    for k in (1, 3, 10, 20):
        segmentos = [dict(base, segmento=f"S{j}") for j in range(k)]
        out = _expectativa(segmentos, tabela)
        atual = (out["veredito"].estado,
                 out["veredito"].anos_medidos,
                 out["texto_fragilidade"],
                 out["positivo_fragilidade"])
        if referencia is None:
            referencia = atual
        assert atual == referencia, (
            f"com {k} segmentos a tela publica {atual}, e com 1 publicava "
            f"{referencia} -- a populacao voltou a ser segmento-ano"
        )
        assert out["veredito"].anos_medidos == len(ic)


def test_sem_pares_o_bloco_diz_que_nao_mediu_em_vez_de_cair_no_fallback():
    """A-1, o caso oposto: sem `ic_pairs` não há como reduzir a um IC por
    ano. Cair no `rank_ic_values` concatenado seria trocar "não medi" por
    um número inflado — exatamente o defeito que a correção fecha. O bloco
    tem que dizer que não pôde medir, e a limitação sai da medição."""
    tabela = _tabela_medida()
    sem_pares = _res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33],
                        com_pares=False)

    out = _expectativa([sem_pares, dict(sem_pares, segmento="S2")], tabela)

    assert out["ic_values"] == []
    assert out["veredito"].anos_medidos == 0
    assert out["veredito"].estado != "evidencia_a_favor"
    assert "rank_ic_values" in out["limitacao_evidencia"], (
        "a limitacao tem que nomear a lista que NAO foi usada e por que"
    )
    assert out["texto_fragilidade"] == "—"
    assert out["positivo_fragilidade"] is None


def test_fragilidade_zero_sobre_inconclusivo_nao_sai_verde():
    """A-2: "0 safra(s)" em VERDE ao lado de "Inconclusivo" afirma robustez
    da *ignorância* — e isso acontecia em 50,8% dos casos inconclusivos
    medidos (n=8, mu=0,02). Zero remoções sobre uma não-conclusão é
    insensibilidade do teste, não robustez, e o texto tem que dizer isso."""
    ic = [0.02, -0.05, 0.10, -0.03, 0.06, 0.01]
    out = _expectativa([_res_ic(ic)], _tabela_medida())

    assert out["veredito"].estado == "inconclusivo"
    assert out["loo"]["safras_que_viram"] == 0
    assert out["positivo_fragilidade"] is None, (
        "card verde sobre veredito inconclusivo -- o verde afirma robustez "
        "da ignorancia"
    )
    assert out["texto_fragilidade"] == "0 de 6 anos"
    ajuda = out["ajuda_fragilidade"].lower()
    assert "insensibilidade" in ajuda and "inconclusivo" in ajuda, (
        f"o estado neutro nao carrega a causa: {out['ajuda_fragilidade']!r}"
    )


def test_card_de_fragilidade_publica_a_banda_de_p_valores():
    """F-2 reaberto: contagem diz QUANTOS anos viram, e só a banda
    `[min p(sem_i), max p(sem_i)]` diz por QUANTO a conclusão passou de
    alpha. O veredito da B3 já virou para APROVADO por 0,004; uma contagem
    de zero não distingue "passou raspando" de "passou com folga"."""
    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                       _tabela_medida())

    baixo, alto = out["loo"]["p_banda"]
    assert baixo is not None and alto <= baixo + 1
    # A-6: a banda sai UMA vez, no texto visivel. Ate a rodada 3 ela era
    # montada duas vezes -- no `st.caption` e no `ajuda=`, que `card_metrica`
    # vira `title=` -- e duas copias do mesmo texto divergem em silencio
    # (nota de memoria `guarda-duplicada-diverge`).
    nota = out["nota_fragilidade"]
    assert _fmt_p(baixo) in nota and _fmt_p(alto) in nota, (
        f"a banda medida nao chegou ao card: {nota!r}"
    )
    assert "α = 0.10" in nota
    assert "α = 0.10" not in out["ajuda_fragilidade"], (
        "a banda voltou a ser duplicada no tooltip: "
        f"{out['ajuda_fragilidade']!r}"
    )


def test_premissa_de_independencia_sai_em_texto_visivel_nao_em_tooltip():
    """Premissa de independência: nenhum texto da tela mencionava que o
    teste t trata os anos como observações independentes, sendo que anos
    vizinhos compartilham universo e regime — o p-valor é otimista.

    A ressalva tem que sair da MEDIÇÃO (quantos anos, quais, quantos
    consecutivos), nunca de um rodapé fixo: este projeto já publicou texto
    de limitação que envelheceu invertido e continuou soando como rigor.

    N-4: e tem que ser VISÍVEL. Na rodada 2 ela foi parar no `ajuda`, que
    `card_metrica` renderiza como atributo `title=` — tooltip de hover,
    inexistente no toque. Ressalva que qualifica o selo mais forte da tela
    não pode depender de o usuário passar o mouse."""
    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                       _tabela_medida())

    nota = out["nota_independencia"]
    assert "independentes" in nota
    assert "6 anos (2000–2005" in nota, f"não citou a medição: {nota!r}"
    assert "6 consecutivos" in nota
    assert "independentes" not in out["ajuda_ordena"], (
        "a ressalva voltou para o tooltip -- `ajuda` vira title= do card"
    )

    # e a frase acompanha a medição: outra amostra, outros números
    esparso = _expectativa(
        [_res_ic([0.30, 0.32], ano0=2000),
         _res_ic([0.28, 0.31], ano0=2010)], _tabela_medida())
    assert "4 anos (2000–2011" in esparso["nota_independencia"]
    assert "2 consecutivos" in esparso["nota_independencia"]


def test_render_expectativa_le_a_ajuda_e_a_limitacao_da_funcao_pura():
    """Cabeamento dos textos novos: `render_expectativa` montava
    `ajuda_ordena` na mão, e a ressalva de independência (que é derivada da
    medição) sumiria da tela com a suíte verde. Inspeção por AST, não
    AppTest (nota de memória `apptest-vaza-atribuicao-de-modulo`)."""
    caminho = RAIZ / "views" / "portfolio_b3_safras.py"
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    render = next(
        no for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef) and no.name == "render_expectativa"
    )
    lidas = {no.slice.value for no in ast.walk(render)
             if isinstance(no, ast.Subscript)
             and isinstance(no.slice, ast.Constant)
             and isinstance(no.slice.value, str)}
    assert {"ajuda_ordena", "ajuda_fragilidade",
            "limitacao_evidencia"} <= lidas, (
        "render_expectativa nao le os textos derivados da medicao -- "
        f"leu apenas {sorted(lidas)}"
    )


def test_ressalva_e_margem_chegam_a_tela_como_texto_e_nao_como_tooltip():
    """N-4 e a banda: `ajuda=` vira `title=` do `<div>` do card
    (`design/componentes.py`), isto e, tooltip de hover — invisivel no
    toque. A premissa de independencia e a banda de p-valores tem que
    chegar por `st.caption`, e NAO por `ajuda=`.

    Inspecao por AST, nao AppTest (`apptest-vaza-atribuicao-de-modulo`):
    procura as chaves lidas dentro do laco cujo corpo chama `st.caption`,
    e confere que nenhuma delas e passada como `ajuda=` a um card."""
    caminho = RAIZ / "views" / "portfolio_b3_safras.py"
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    render = next(
        no for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef) and no.name == "render_expectativa"
    )

    def chaves(no):
        return {n.slice.value for n in ast.walk(no)
                if isinstance(n, ast.Subscript)
                and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)}

    def chama_caption(no):
        return any(isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Attribute)
                   and c.func.attr == "caption"
                   for c in ast.walk(no))

    visiveis = set()
    for no in ast.walk(render):
        if isinstance(no, ast.For) and chama_caption(no):
            visiveis |= chaves(no.iter)
        elif isinstance(no, ast.If) and chama_caption(no):
            visiveis |= chaves(no.test)
        elif isinstance(no, ast.Expr) and chama_caption(no):
            visiveis |= chaves(no.value)

    assert {"nota_independencia", "nota_fragilidade"} <= visiveis, (
        "a ressalva de independencia e a banda de p-valores nao sao "
        f"publicadas em texto visivel -- st.caption recebe {sorted(visiveis)}"
    )

    em_tooltip = set()
    for no in ast.walk(render):
        if isinstance(no, ast.Call):
            for kw in no.keywords:
                if kw.arg == "ajuda":
                    em_tooltip |= chaves(kw.value)
    assert not ({"nota_independencia", "nota_fragilidade"} & em_tooltip), (
        f"texto visivel virou tooltip de novo: {sorted(em_tooltip)}"
    )


def _pares_exatos(ano, rho, *, n=5):
    """Pares (ano, score, retorno) com Rank-IC EXATAMENTE `rho`.

    A bisseccao de `_pares_do_ano` chega perto, e perto nao serve aqui: o
    ramo que este bloco de testes exercita depende de subamostras
    IDENTICAS, sem dispersao nenhuma. Com n=5, rho = 1 − Σd²/20 assume
    valores exatos; a busca varre as permutacoes ate achar o Σd² pedido.
    """
    import itertools
    alvo = round((1 - rho) * (n * (n * n - 1) / 6) / 1.0)
    for perm in itertools.permutations(range(n)):
        d2 = sum((i - j) ** 2 for i, j in enumerate(perm))
        if d2 == alvo:
            return [(ano, float(i), float(p)) for i, p in enumerate(perm)]
    raise AssertionError(f"rho={rho} nao e alcancavel com n={n}")


def test_fragilidade_sem_margem_nao_afirma_veredito_inconclusivo():
    """N-1: o texto do estado neutro dizia "o veredito e inconclusivo" ao
    lado de um card publicando "Evidencia contra". Sao dois casos opostos
    colados no mesmo ramo: (a) zero viradas sobre veredito inconclusivo e
    (b) zero viradas sobre veredito CONCLUSIVO cuja banda nao e
    calculavel. Em [-0.1, -0.1, -0.1, -0.5] as subamostras identicas ficam
    sem dispersao, o p-valor nem existe, e o texto negava o proprio card
    ao lado."""
    pares = (_pares_exatos(2000, -0.1) + _pares_exatos(2001, -0.1)
             + _pares_exatos(2002, -0.1) + _pares_exatos(2003, -0.5))
    out = _expectativa([{"segmento": "S", "ic_pairs": pares}],
                       _tabela_medida())

    assert out["ic_values"] == pytest.approx([-0.1, -0.1, -0.1, -0.5])
    assert len(set(out["ic_values"][:3])) == 1, (
        "os tres primeiros anos precisam ser IDENTICOS -- e a ausencia "
        "de dispersao na subamostra que apaga o p-valor"
    )
    assert out["veredito"].estado == "evidencia_contra"
    assert out["loo"]["safras_que_viram"] == 0
    assert out["loo"]["banda_de_um_lado"] is None
    assert out["positivo_fragilidade"] is None

    ajuda = out["ajuda_fragilidade"]
    assert "inconclusivo" not in ajuda.lower(), (
        f"o texto contradiz o card ao lado (Evidencia contra): {ajuda!r}"
    )
    assert "Evidência contra" in ajuda, (
        f"o texto nao nomeia o veredito que sobreviveu: {ajuda!r}"
    )
    assert "sem dispersão" in ajuda, (
        f"a causa da margem ausente nao foi medida no texto: {ajuda!r}"
    )


def test_banda_de_p_valores_e_publicada_em_texto_visivel():
    """A banda e o "por quanto passou" — e e ela que impede o selo verde
    de ser lido como salvaguarda testada. Na rodada 2 ela ficou so no
    `ajuda`, ou seja, no `title=` do card: o usuario via "0 de 6 anos" em
    verde e mais nada."""
    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                       _tabela_medida())

    baixo, alto = out["loo"]["p_banda"]
    nota = out["nota_fragilidade"]
    assert _fmt_p(baixo) in nota and _fmt_p(alto) in nota, (
        f"a banda medida nao chegou ao texto visivel: {nota!r}"
    )
    assert "α = 0.10" in nota


def test_limitacao_distingue_pares_ausentes_de_ano_sem_ativos_suficientes():
    """N-3: com `ic_pairs` presentes e nenhum ano alcancando o minimo de
    ativos, a tela dizia "os pares nao vieram nos resultados" — causa nao
    medida, e a frase certa (nenhum ano calculavel) ficava inalcancavel
    tres linhas abaixo. A discriminacao tem que ser pelos PARES, que e o
    que `_ics_por_ano` viu, nao por `rank_ic_values`."""
    poucos = [(2020, float(i), float(i)) for i in range(3)]
    out = _expectativa(
        [{"segmento": "S", "rank_ic_values": [0.3], "ic_pairs": poucos}],
        _tabela_medida())

    assert out["ic_values"] == []
    limitacao = out["limitacao_evidencia"]
    assert "não vieram" not in limitacao, (
        f"culpou a ausencia errada: {limitacao!r}"
    )
    assert "3 observação(ões)" in limitacao, (
        f"a limitacao nao cita o que chegou: {limitacao!r}"
    )
    assert "5 ativos mínimos" in limitacao


def test_tela_explica_por_que_as_duas_contagens_diferem():
    """N-5: "Safras medidas: 3" no Bloco 1 e "0 de 6 anos" no Bloco 3
    ficavam lado a lado sem nada dizendo que sao populacoes diferentes —
    safra mensuravel (janela fechada com pregao) contra ano com Rank-IC
    calculavel (>= 5 ativos no universo)."""
    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                       _tabela_medida(n=3))

    assert out["n_safras_banda"] == 3 and out["loo"]["n_safras"] == 6
    nota = out["nota_fragilidade"]
    assert "6 ano(s) com Rank-IC calculável" in nota, nota
    assert "3 safra(s) mensurável(is)" in nota, nota

    # e some quando as duas contagens coincidem: nota derivada da medicao,
    # nao frase fixa
    igual = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                         _tabela_medida(n=6))
    assert "populações diferentes" not in igual["nota_fragilidade"]


def test_textos_do_bloco_nao_deixam_espaco_orfao():
    """N-6: os textos eram concatenados com `". "` + `_texto_banda_p`, que
    e vazio quando a banda nao e calculavel — sobrava espaco no fim ou
    espaco duplo no meio."""
    amostras = ([0.30, 0.32, 0.28, 0.31, 0.29, 0.33],
                [0.02, -0.05, 0.10, -0.03, 0.06, 0.01],
                [0.30, 0.32, 0.28],
                [-0.20, -0.18, -0.25, -0.22])
    entradas = [[_res_ic(ic)] for ic in amostras]
    # o caso que de fato produz banda VAZIA: subamostras identicas, sem
    # dispersao, `_texto_banda_p` devolve "" e a juncao ingenua deixaria o
    # espaco orfao no fim da frase
    entradas.append([{"segmento": "S", "ic_pairs": (
        _pares_exatos(2000, -0.1) + _pares_exatos(2001, -0.1)
        + _pares_exatos(2002, -0.1) + _pares_exatos(2003, -0.5))}])
    for entrada in entradas:
        out = _expectativa(entrada, _tabela_medida())
        for chave in ("ajuda_ordena", "ajuda_fragilidade",
                      "nota_fragilidade", "nota_independencia",
                      "limitacao_evidencia", "limitacao_banda"):
            texto = out[chave]
            assert texto == texto.strip(), f"{chave} com espaco nas pontas"
            assert "  " not in texto, f"{chave} com espaco duplo: {texto!r}"


# ── Rodada de correção 4 da Task 6 (A-2, A-3, A-5, A-6) ──


def test_banda_de_p_valores_preserva_a_ordem_de_grandeza():
    """A-3: com `.3f` fixo, 2.255 dos 13.040 casos robustos de 20.000
    amostras (17,3% dos selos verdes) imprimiam "entre 0.000 e 0.000" —
    as bandas desses casos estão na casa de 1e-7. O "por quanto passou"
    saiu do tooltip na rodada 3 justamente para ser lido; publicá-lo como
    zero visual devolve o selo verde sem a margem, que é o defeito que a
    rodada 3 fechou.

    A amostra abaixo é exatamente um desses casos."""
    out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                       _tabela_medida())

    baixo, alto = out["loo"]["p_banda"]
    assert 0 < baixo < 1e-3 and 0 < alto < 1e-3, (
        f"a amostra deixou de exercitar a faixa medida: {baixo}, {alto}"
    )
    nota = out["nota_fragilidade"]
    assert "0.000" not in nota, (
        f"a banda voltou a ser publicada como zero visual: {nota!r}"
    )
    assert _fmt_p(baixo) in nota and _fmt_p(alto) in nota, nota

    # e o formato não troca a notação onde o `.3f` já dizia a verdade
    assert _fmt_p(0.05) == "0.050" and _fmt_p(0.10) == "0.100"
    assert "e-" in _fmt_p(9.1e-07)


def test_margem_ausente_e_publicada_em_texto_visivel_nao_so_no_tooltip():
    """A-5: `p_banda == (None, None)` é o ÚNICO ramo em que a banda de
    fato reprova o selo — e era justamente nele que `nota_fragilidade`
    ficava `""` e a razão vivia só no `ajuda=`, isto é, no `title=` do
    card: tooltip de hover, inexistente no toque. O oposto do que a
    rodada 3 consertou, no ramo que mais precisa."""
    pares = (_pares_exatos(2000, -0.1) + _pares_exatos(2001, -0.1)
             + _pares_exatos(2002, -0.1) + _pares_exatos(2003, -0.5))
    out = _expectativa([{"segmento": "S", "ic_pairs": pares}],
                       _tabela_medida())

    assert out["loo"]["medido"] is True
    assert out["loo"]["p_banda"] == (None, None)

    nota = out["nota_fragilidade"]
    assert nota, "o unico ramo em que a banda morde nao publica razao nenhuma"
    assert "sem dispersão" in nota, (
        f"a razao da margem ausente nao foi derivada da medicao: {nota!r}"
    )
    sem_banda = sum(1 for v in out["loo"]["p_loo"] if v is None)
    assert sem_banda > 0 and str(sem_banda) in nota, (
        f"a nota nao cita quantas subamostras ficaram sem p-valor: {nota!r}"
    )


def test_limitacao_nao_culpa_o_minimo_de_ativos_quando_os_anos_o_alcancaram():
    """A-2: `pooled_yearly_ics` descarta o ano por DUAS razões opostas —
    menos de `MIN_ATIVOS_ANO` observações, ou postos degenerados. A frase
    culpava sempre a primeira e contradizia o próprio número que imprimia:
    com 6 pares num único ano saía "6 observação(ões) chegaram, mas nenhum
    ano juntou os 5 ativos mínimos"."""
    degenerados = [(2020, 1.0, 1.0) for _ in range(6)]
    out = _expectativa(
        [{"segmento": "S", "rank_ic_values": [0.3], "ic_pairs": degenerados}],
        _tabela_medida())

    assert out["ic_values"] == []
    limitacao = out["limitacao_evidencia"]
    assert "6 observação(ões)" in limitacao, limitacao
    assert "nenhum ano juntou" not in limitacao, (
        f"a frase contradiz o proprio numero que imprime: {limitacao!r}"
    )
    assert "postos" in limitacao and "2020" in limitacao, (
        f"a causa nao foi derivada da medicao: {limitacao!r}"
    )

    # e a outra causa continua sendo nomeada quando é ela que vale
    poucos = _expectativa(
        [{"segmento": "S", "ic_pairs": [(2020, float(i), float(i))
                                        for i in range(3)]}],
        _tabela_medida())
    assert "5 ativos mínimos" in poucos["limitacao_evidencia"]


def test_pares_de_ic_e_percorrido_uma_vez_so_por_expectativa():
    """A-6: `_expectativa` precisava dos pares para a limitação e chamava
    `_ics_por_ano`, que os montava de novo — a lista mais larga do bloco
    (todos os ativos × todos os anos × todos os segmentos) percorrida duas
    vezes por render, sem nenhuma segurança em troca."""
    import views.portfolio_b3_safras as modulo

    original = modulo._pares_de_ic
    chamadas = []

    def contando(resultados):
        chamadas.append(1)
        return original(resultados)

    modulo._pares_de_ic = contando
    try:
        out = _expectativa([_res_ic([0.30, 0.32, 0.28, 0.31, 0.29, 0.33])],
                           _tabela_medida())
    finally:
        modulo._pares_de_ic = original

    assert out["ic_values"], "o teste precisa do caminho que de fato mede"
    assert len(chamadas) == 1, (
        f"os pares foram montados {len(chamadas)} vezes por render"
    )


# ── Task 7 (Bloco 2) — o tamanho do viés de universo ──────────────────────


def _par(linhas_com, completas_com, linhas_sem, completas_sem):
    return (_tabela(linhas_com, completas_com),
            _tabela(linhas_sem, completas_sem))


def _safras_medidas(primeira, retornos):
    """Linhas mensuráveis consecutivas a partir de `primeira`."""
    return [_linha(primeira + i, completa=True, mensuravel=True,
                   estrategia=r)
            for i, r in enumerate(retornos)]


def test_vies_le_attrs_safras_completas_e_nao_a_coluna_completa():
    """Mutação-alvo: trocar `attrs["safras_completas"]` por
    `df["Completa"]` em `_vies_universo`.

    `attrs` é `completa AND mensurável`; a coluna é só `completa`. Uma
    safra pode ter a janela civil fechada e zero pregão observado — ela
    aparece com `Completa=True` e não entra em `attrs`. Aqui a safra 2020
    está nesse estado do lado COM gate: se a população vier da coluna,
    2020 passa a ser declarada "Comparável" e a ressalva que explica a
    célula vazia desaparece da tela.
    """
    com_gate, sem_gate = _par(
        [_linha(2020, completa=True, mensuravel=False),
         _linha(2021, completa=True, mensuravel=True, estrategia=30.0)],
        [2021],
        [_linha(2020, completa=True, mensuravel=True, estrategia=10.0),
         _linha(2021, completa=True, mensuravel=True, estrategia=10.0)],
        [2020, 2021],
    )
    out = _vies_universo(com_gate, sem_gate)

    assert out["n_safras"] == 1
    assert out["medio"] == pytest.approx(20.0)
    linha_2020 = out["comparacao"].set_index("Safra").loc[2020]
    assert bool(linha_2020["Comparável"]) is False
    assert pd.isna(linha_2020["Viés (pp)"])
    assert any("2020" in n and "sem viés" in n for n in out["notas"]), (
        f"a célula vazia da safra 2020 ficou sem explicação: {out['notas']}"
    )


def test_vies_ignora_safra_fora_de_attrs_mesmo_com_retorno_publicado():
    """A mesma regra, sem depender de o valor excluído ser `NaN`.

    O teste acima ainda passaria por acidente se a exclusão viesse do
    `NaN` em vez de `attrs` — aqui a safra 2020 tem retorno publicado dos
    dois lados e `Completa=True`, e mesmo assim está fora de
    `safras_completas` do lado com gate. Só quem lê `attrs` a exclui; quem
    ler a coluna publica um viés de +40 pp que a população não autoriza.
    """
    com_gate, sem_gate = _par(
        [_linha(2020, completa=True, mensuravel=True, estrategia=50.0),
         _linha(2021, completa=True, mensuravel=True, estrategia=30.0)],
        [2021],
        [_linha(2020, completa=True, mensuravel=True, estrategia=10.0),
         _linha(2021, completa=True, mensuravel=True, estrategia=10.0)],
        [2020, 2021],
    )
    out = _vies_universo(com_gate, sem_gate)

    assert out["n_safras"] == 1
    assert out["vies"] == pytest.approx([20.0])
    assert out["medio"] == pytest.approx(20.0)
    # E a linha nao comparavel nao publica numero NENHUM: os dois lados
    # tem retorno, entao a subtracao existe -- e publicar +40 pp ali seria
    # publicar um vies para uma safra que a propria tabela declara fora da
    # populacao, na coluna ao lado.
    assert pd.isna(out["comparacao"].set_index("Safra").loc[2020, "Viés (pp)"])


def test_vies_nunca_publica_selo_verde():
    """Verde no card afirmaria "não há viés de universo".

    Nesta tela verde é conclusão, e "não rejeitei a hipótese nula" não é
    conclusão nenhuma — é ausência de evidência. O critério descartado era
    `positivo=abs(medio) < 1.0`: constante escrita à mão, sem medição
    atrás, que dava selo de aprovação a um viés de 0,9 pp. Os dois
    extremos entram aqui: viés exatamente zero e viés grande.
    """
    zero_com, zero_sem = _par(_safras_medidas(2020, [10.0, 12.0, 8.0]),
                              [2020, 2021, 2022],
                              _safras_medidas(2020, [10.0, 12.0, 8.0]),
                              [2020, 2021, 2022])
    assert _vies_universo(zero_com, zero_sem)["medio"] == pytest.approx(0.0)
    assert _vies_universo(zero_com, zero_sem)["positivo"] is not True

    pequeno_com, pequeno_sem = _par(
        _safras_medidas(2020, [10.9, 12.8, 8.7, 11.6]),
        [2020, 2021, 2022, 2023],
        _safras_medidas(2020, [10.0, 12.0, 8.0, 11.0]),
        [2020, 2021, 2022, 2023])
    out = _vies_universo(pequeno_com, pequeno_sem)
    assert 0 < out["medio"] < 1.0
    assert out["positivo"] is not True, (
        "um viés de menos de 1 pp voltou a ganhar selo verde -- o limiar "
        "escrito à mão é exatamente o defeito que esta regra fechou"
    )


def test_vies_demonstrado_sai_vermelho_e_o_texto_diz_por_quanto():
    """Viés consistente e com dispersão: o teste t tem que concluir, o
    card tem que sair VERMELHO (viés medido é má notícia, não boa) e a
    ressalva tem que trazer p-valor e faixa observada — contagem sozinha
    não diz por quanto."""
    com, sem = _par(_safras_medidas(2020, [20.0, 22.0, 19.0, 21.0, 20.5]),
                    [2020, 2021, 2022, 2023, 2024],
                    _safras_medidas(2020, [10.0, 10.0, 10.0, 10.0, 10.0]),
                    [2020, 2021, 2022, 2023, 2024])
    out = _vies_universo(com, sem)

    assert out["n_safras"] == 5
    assert out["significante"] is True
    assert out["positivo"] is False
    assert out["texto_medio"] == "+10.5 pp"
    assert out["faixa"] == pytest.approx((9.0, 12.0))
    juntas = " ".join(out["notas"])
    assert "+9.0 a +12.0 pp" in juntas, f"a faixa observada sumiu: {juntas}"
    assert "se distingue de zero" in juntas
    assert "p = " in juntas


def test_vies_sem_significancia_nao_afirma_ausencia_de_vies():
    """Ausência de evidência não é evidência de ausência, e a frase tem que
    dizer isso explicitamente: sem essa ressalva, "não se distingue de
    zero" é lido como "não há viés" — e o viés observado continua de pé."""
    com, sem = _par(_safras_medidas(2020, [20.0, -2.0, 15.0, 2.0]),
                    [2020, 2021, 2022, 2023],
                    _safras_medidas(2020, [10.0, 10.0, 10.0, 10.0]),
                    [2020, 2021, 2022, 2023])
    out = _vies_universo(com, sem)

    assert out["significante"] is False
    assert out["positivo"] is None
    juntas = " ".join(out["notas"])
    assert "não é o mesmo que não haver viés" in juntas, (
        f"a tela afirmou ausência de viés a partir de um p-valor alto: {juntas}"
    )


def test_causa_de_nao_haver_teste_separa_as_duas_ausencias():
    """`_causa_sem_teste` cobre duas ausências OPOSTAS: faltam safras, ou
    há safras e as diferenças são todas iguais. Uma frase única culparia
    sempre a primeira e, com 5 safras de viés idêntico, diria que faltaram
    safras — texto de limitação que contradiz o próprio número ao lado."""
    poucas = _causa_sem_teste([3.0])
    assert "1 safra" in poucas and "dispersão" in poucas

    identicas = _causa_sem_teste([3.0, 3.0, 3.0, 3.0, 3.0])
    assert "5 diferenças" in identicas
    assert "+3.0 pp" in identicas
    assert "não há erro-padrão a estimar" in identicas


def test_sem_dispersao_entre_safras_nao_publica_p_valor():
    """Guarda de dispersão herdada de `core.b3_evidence` (relativa, não
    `desvio <= 0.0`): diferenças idênticas não produzem p-valor, e a tela
    tem que dizer por quê em vez de publicar significância fabricada."""
    com, sem = _par(_safras_medidas(2020, [13.0, 15.0, 11.0]),
                    [2020, 2021, 2022],
                    _safras_medidas(2020, [10.0, 12.0, 8.0]),
                    [2020, 2021, 2022])
    out = _vies_universo(com, sem)

    assert out["vies"] == pytest.approx([3.0, 3.0, 3.0])
    assert out["p_bilateral"] is None
    assert out["significante"] is False
    assert any("não há erro-padrão a estimar" in n for n in out["notas"])


def test_safra_que_so_existe_sem_o_gate_e_relatada():
    """O gate não reduz o retorno dessas safras — ele apaga a safra
    inteira. A subtração nunca mostraria isso, porque a linha simplesmente
    não aparece no `merge`: só uma ressalva derivada da diferença entre os
    dois conjuntos de safras conta essa parte do viés."""
    com, sem = _par(_safras_medidas(2021, [30.0]), [2021],
                    _safras_medidas(2020, [10.0, 10.0]), [2020, 2021])
    out = _vies_universo(com, sem)

    assert list(out["comparacao"]["Safra"]) == [2021]
    assert any("2020" in n and "apaga a safra inteira" in n
               for n in out["notas"]), (
        f"a safra que o gate apagou não foi relatada: {out['notas']}"
    )


def test_limitacao_do_vies_deriva_a_causa_observada():
    """"Sem safras suficientes para medir" cobre causas opostas e não
    informa nenhuma. Aqui as duas reconstruções têm safras mensuráveis,
    mas elas não se cruzam — e a frase tem que nomear os dois lados."""
    com, sem = _par(
        [_linha(2020, completa=True, mensuravel=True, estrategia=30.0),
         _linha(2021, completa=True, mensuravel=False)],
        [2020],
        [_linha(2020, completa=True, mensuravel=False),
         _linha(2021, completa=True, mensuravel=True, estrategia=10.0)],
        [2021],
    )
    out = _vies_universo(com, sem)

    assert out["n_safras"] == 0
    assert out["texto_medio"] == "—"
    assert "não se cruzam" in out["limitacao"]
    assert "2020" in out["limitacao"] and "2021" in out["limitacao"]
    assert out["limitacao"] in out["notas"]


def test_vies_com_tabelas_vazias_nao_estoura_e_explica():
    """Antes de rodar a análise as duas tabelas chegam vazias (mas com as
    colunas de `COLUNAS_TABELA`). O bloco não pode estourar nem publicar
    número — e tem que dizer o que faltou."""
    vazia = _tabela(pd.DataFrame(columns=COLUNAS_TABELA), [])
    out = _vies_universo(vazia, vazia)

    assert out["n_safras"] == 0
    assert out["medio"] is None
    assert out["comparacao"].empty
    assert "nenhuma safra em comum" in out["limitacao"]


def test_column_config_do_vies_cobre_as_colunas_numericas():
    """Mesma regra de `_column_config_retorno`: formatação por
    `column_config`, nunca convertendo a coluna para texto — texto faz o
    `st.dataframe` ordenar em ordem alfabética ("-3,0 → 10,0 → 100,0")."""
    config = _column_config_vies()
    assert set(config) == set(_COLUNAS_VIES)


# ── Cabeamento do Bloco 2 (AST, nunca AppTest) ──────────────────────────────


def _funcao_da_view(nome, arquivo="portfolio_b3_safras.py"):
    caminho = RAIZ / "views" / arquivo
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    return next(no for no in ast.walk(arvore)
                if isinstance(no, ast.FunctionDef) and no.name == nome)


def test_render_safras_chama_render_vies_universo():
    """Sem esta checagem, apagar a chamada apaga o Bloco 2 inteiro da tela
    e deixa a suíte verde — `_vies_universo` é pura e continuaria passando
    sozinha. Inspeção por AST (`apptest-vaza-atribuicao-de-modulo`)."""
    render = _funcao_da_view("render_safras")
    chamadas = {no.func.id for no in ast.walk(render)
                if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)}
    assert "render_vies_universo" in chamadas, (
        "render_safras nao chama render_vies_universo -- o Bloco 2 nao "
        "aparece na tela e nenhum outro teste percebe"
    )
    # Presenca nao e alcance: um `if False and ...` em volta da chamada
    # deixaria o `ast.Call` no lugar e o Bloco 2 fora da tela. A guarda
    # tem que ser exatamente "tenho a lista completa".
    guardas = [no for no in ast.walk(render) if isinstance(no, ast.If)
               and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                       and c.func.id == "render_vies_universo"
                       for filho in no.body for c in ast.walk(filho))]
    assert len(guardas) == 1, "esperava uma unica guarda em volta do Bloco 2"
    teste = guardas[0].test
    assert isinstance(teste, ast.Name) and teste.id == "resultados_todos", (
        "a chamada do Bloco 2 esta sob uma condicao que nao e apenas "
        "`resultados_todos` -- ela pode nunca ser alcancada"
    )


def test_bloco_2_so_mede_depois_do_botao():
    """Decisão do dono do projeto: a reconstrução sem gate é a conta mais
    cara da tela e não é paga a cada rerun. Renderizar eagerly não quebra
    nada visível — só fica lento —, então o que prende o comportamento é a
    estrutura: nenhuma chamada a `tabela_de_safras` pode estar acima da
    guarda que retorna quando o botão não foi clicado."""
    render = _funcao_da_view("render_vies_universo")
    guardas = [no for no in ast.walk(render) if isinstance(no, ast.If)
               and any(isinstance(c, ast.Call)
                       and isinstance(c.func, ast.Attribute)
                       and c.func.attr == "button"
                       for c in ast.walk(no.test))]
    assert guardas, "render_vies_universo nao esta atras de um st.button"
    assert any(isinstance(s, ast.Return) for g in guardas
               for s in ast.walk(g)), (
        "o botao nao interrompe o render -- sem `return` a medicao roda "
        "mesmo sem clique"
    )
    linha_guarda = min(g.lineno for g in guardas)
    medicoes = [no.lineno for no in ast.walk(render)
                if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
                and no.func.id == "tabela_de_safras"]
    assert medicoes, "render_vies_universo nao reconstroi safra nenhuma"
    assert min(medicoes) > linha_guarda, (
        "tabela_de_safras e chamada ANTES da guarda do botao -- o bloco "
        "voltou a medir a cada rerun"
    )


def test_medicao_guardada_e_redesenhada_com_as_mesmas_ressalvas():
    """A medição fica em `session_state` e volta nos reruns seguintes.
    Se o caminho do cache republicar só a tabela, os números voltam sem o
    texto que diz o que eles NÃO são — e é o texto que impede o viés de
    ser lido como resultado da estratégia. Os dois caminhos têm que
    desenhar pela mesma função."""
    render = _funcao_da_view("render_vies_universo")
    guardas = [no for no in ast.walk(render) if isinstance(no, ast.If)
               and any(isinstance(c, ast.Call)
                       and isinstance(c.func, ast.Attribute)
                       and c.func.attr == "button"
                       for c in ast.walk(no.test))]
    dentro_da_guarda = {no.func.id for g in guardas for no in ast.walk(g)
                        if isinstance(no, ast.Call)
                        and isinstance(no.func, ast.Name)}
    assert "_desenha_vies" in dentro_da_guarda, (
        "o caminho do cache nao passa por _desenha_vies -- a medicao "
        "guardada volta sem as ressalvas"
    )
    todas = [no for no in ast.walk(render)
             if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
             and no.func.id == "_desenha_vies"]
    assert len(todas) >= 2, (
        "o caminho da medicao nova nao desenha pela mesma funcao do cache"
    )


def test_ressalvas_do_vies_chegam_como_caption_e_nao_como_tooltip():
    """`ajuda=` vira `title=` do `<div>` do card (`design/componentes.py`),
    isto é, tooltip de hover — invisível no toque. As notas derivadas da
    medição têm que sair por `st.caption`."""
    desenha = _funcao_da_view("_desenha_vies")

    def chaves(no):
        return {n.slice.value for n in ast.walk(no)
                if isinstance(n, ast.Subscript)
                and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)}

    em_ajuda = set()
    for no in ast.walk(desenha):
        if isinstance(no, ast.Call):
            for kw in no.keywords:
                if kw.arg == "ajuda":
                    em_ajuda |= chaves(kw.value)
    assert "notas" not in em_ajuda, (
        "as ressalvas do vies viraram tooltip -- `ajuda` e `title=`"
    )

    visiveis = set()
    for no in ast.walk(desenha):
        if isinstance(no, ast.For) and any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                and c.func.attr == "caption" for c in ast.walk(no)):
            visiveis |= chaves(no)
    assert "notas" in visiveis, (
        f"as notas nao chegam por st.caption -- visiveis: {sorted(visiveis)}"
    )


def test_tela_b3_passa_os_aprovados_e_a_lista_completa():
    """As duas pontas do Bloco 2 vêm da mesma chamada, e passar a lista
    errada em qualquer uma delas não quebra nada visível: com `resultados`
    no lugar de `aprovados` as duas reconstruções ficam IDÊNTICAS e o
    bloco publica viés zero — um número tranquilizador e falso."""
    chamada = next(
        no for no in ast.walk(
            ast.parse((RAIZ / "views" / "portfolio_b3.py")
                      .read_text(encoding="utf-8")))
        if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)
        and no.func.id == "render_safras"
    )
    assert isinstance(chamada.args[0], ast.Name)
    assert chamada.args[0].id == "aprovados", (
        "o Bloco 1 nao reconstroi a carteira que a tela publica -- primeiro "
        f"argumento: {ast.dump(chamada.args[0])}"
    )
    todos = {kw.arg: kw.value for kw in chamada.keywords}
    assert "resultados_todos" in todos, (
        "render_safras nao recebe a lista nao filtrada -- o Bloco 2 nao "
        "e renderizado"
    )
    assert isinstance(todos["resultados_todos"], ast.Name)
    assert todos["resultados_todos"].id == "resultados", (
        "a lista sem gate nao e a lista nao filtrada -- as duas curvas "
        "ficariam iguais e o vies sairia zero"
    )


# ── Rodada de correção 1 ─────────────────────────────────────────────────────


class _Falso:
    """Dublê de `streamlit` para provar ALCANCE, não forma.

    Não é AppTest: nada de runtime, script runner ou atribuição de módulo
    (`apptest-vaza-atribuicao-de-modulo`). É só um objeto que registra o
    que a função chamou, injetado no lugar do `st` do módulo — a única
    forma de um teste falhar quando a função para de DESENHAR, e não
    apenas quando o texto dela muda de forma.
    """

    def __init__(self, botao=False):
        self.chamadas: list[tuple] = []
        self.session_state: dict = {}
        self._botao = botao

    def _reg(self, nome):
        def _f(*a, **k):
            self.chamadas.append((nome, a, k))
            return None
        return _f

    # `column_config` nao e desenho, e construcao de objeto que a funcao
    # devolve -- o dublê deixa passar o modulo real.
    column_config = _st_real.column_config

    def __getattr__(self, nome):
        return self._reg(nome)

    def button(self, *a, **k):
        self.chamadas.append(("button", a, k))
        return self._botao

    def columns(self, n, *a, **k):
        self.chamadas.append(("columns", (n,), k))
        quantas = n if isinstance(n, int) else len(n)
        return [contextlib.nullcontext() for _ in range(quantas)]

    def spinner(self, *a, **k):
        self.chamadas.append(("spinner", a, k))
        return contextlib.nullcontext()

    def caption(self, texto="", *a, **k):
        self.chamadas.append(("caption", (texto,), k))

    def dataframe(self, *a, **k):
        self.chamadas.append(("dataframe", a, k))

    def nomes(self):
        return [c[0] for c in self.chamadas]

    def legendas(self):
        return [c[1][0] for c in self.chamadas if c[0] == "caption"]


def _tabela_vies(com, sem, safras=None):
    """Duas tabelas de safras com os retornos dados, prontas para
    `_vies_universo`."""
    safras = safras or list(range(2020, 2020 + len(com)))
    t_com = _tabela([_linha(s, completa=True, mensuravel=True, estrategia=v)
                     for s, v in zip(safras, com)], list(safras))
    t_sem = _tabela([_linha(s, completa=True, mensuravel=True, estrategia=v)
                     for s, v in zip(safras, sem)], list(safras))
    return t_com, t_sem


def test_vies_testa_o_valor_cheio_e_nao_o_arredondado_para_exibicao():
    """A-T7-02: arredondar antes do teste apaga viés demonstrado.

    `[3.04, 3.02, 2.97]` dá p = 2,4e-05; a mesma amostra arredondada para
    exibição vira `[3.0, 3.0, 3.0]`, perde toda a dispersão, e o teste t
    devolve `(None, None)` — a tela publicaria "não têm dispersão entre
    si", que é FALSO sobre o dado, e um viés significante sairia como
    "não testável" com o card neutro.
    """
    com, sem = _tabela_vies([13.04, 13.02, 12.97], [10.0, 10.0, 10.0])
    m = _vies_universo(com, sem)

    assert m["vies"] == pytest.approx([3.04, 3.02, 2.97], abs=1e-9), (
        "a amostra do teste saiu da coluna arredondada de exibição"
    )
    assert m["p_bilateral"] is not None, (
        "dispersão real abaixo da primeira casa decimal foi apagada antes "
        "do teste"
    )
    assert m["p_bilateral"] < 0.001
    assert m["significante"] is True
    assert m["positivo"] is False
    texto = " ".join(m["notas"])
    assert "dispersão entre si" not in texto, (
        "a tela afirma ausência de dispersão sobre um dado que tem "
        "dispersão medida"
    )
    assert list(m["comparacao"]["Viés (pp)"]) == pytest.approx([3.0, 3.0, 3.0])


def test_alfa_do_vies_e_bilateral_e_cada_cauda_le_metade():
    """A-T7-04: sem isto, `meia_cauda = _ALPHA_VIES` transforma o teste em
    20% bilateral e a tela segue imprimindo "α = 0.10, 0.05 por cauda" —
    número publicado que não corresponde à conta feita.

    A amostra abaixo tem p bilateral = 0,115: acima de 0,10, logo NÃO
    significante. Com a meia-cauda errada (0,10 em vez de 0,05) a cauda
    direita (p = 0,057) passaria e o card viraria vermelho.
    """
    valores = [2.0, 0.0, 3.0, -0.5, 2.5]
    com, sem = _tabela_vies([10.0 + v for v in valores], [10.0] * len(valores))
    m = _vies_universo(com, sem)

    assert m["p_bilateral"] == pytest.approx(0.11476192, abs=1e-6)
    assert 0.10 < m["p_bilateral"] < 0.20
    assert m["significante"] is False, (
        "p bilateral acima de α = 0,10 saiu como significante — a "
        "meia-cauda não está lendo α/2"
    )
    assert m["positivo"] is None
    texto = " ".join(m["notas"])
    assert "α = 0.10" in texto and "0.05 por cauda" in texto, (
        "o α impresso tem que ser o α usado na decisão"
    )


def test_p_bilateral_e_o_dobro_da_cauda_menor():
    """A-T7-05: sem dobrar, o p publicado é de um teste unilateral com
    rótulo de bilateral — metade do valor certo, a favor de declarar viés."""
    from core.b3_evidence import teste_t_unilateral

    valores = [2.0, 0.0, 3.0, -0.5, 2.5]
    com, sem = _tabela_vies([10.0 + v for v in valores], [10.0] * len(valores))
    m = _vies_universo(com, sem)

    p_mais = teste_t_unilateral(valores)[1]
    p_menos = teste_t_unilateral([-v for v in valores])[1]
    menor = min(p_mais, p_menos)
    assert m["p_bilateral"] == pytest.approx(min(1.0, 2.0 * menor))
    assert m["p_bilateral"] > menor * 1.5, (
        "o p bilateral saiu sem dobrar a cauda"
    )
    assert _fmt_p(m["p_bilateral"]) in " ".join(m["notas"])


def _tela_de_safras_falsa(monkeypatch, *, botao=False):
    """Injeta o dublê de streamlit no módulo da view."""
    import views.portfolio_b3_safras as mod
    falso = _Falso(botao=botao)
    monkeypatch.setattr(mod, "st", falso)
    monkeypatch.setattr(mod, "card_metrica",
                        lambda *a, **k: falso.chamadas.append(("card", a, k)))
    return mod, falso


def _resultado_b3(segmento, safra, tickers):
    return {
        "segmento": segmento,
        "lids_por_ano": {safra - 1: list(tickers)},
        "pesos_por_ano": {safra - 1: {t: 1.0 / len(tickers) for t in tickers}},
        "tickers": list(tickers),
    }


def _precos_b3(tickers):
    """Mesmo formato que o motor de safras consome: datas no índice,
    tickers nas colunas."""
    datas = pd.DatetimeIndex(["2024-04-30", "2025-03-31"])
    return pd.DataFrame({t: [10.0, 12.0 + i] for i, t in enumerate(tickers)},
                        index=datas)


def test_render_safras_executa_o_bloco_2_de_fato(monkeypatch):
    """A-T7-01: prova por ALCANCE, não por forma.

    Um `return` logo antes da chamada não muda a forma da guarda nenhuma —
    o `ast.If` continua lá, o `ast.Call` também — e o Bloco 2 some da tela
    com a suíte verde. Este teste roda `render_safras` com um dublê de
    streamlit e exige que `render_vies_universo` tenha sido REALMENTE
    chamada, com a tabela do Bloco 1 junto.
    """
    mod, falso = _tela_de_safras_falsa(monkeypatch)
    chamou: list[dict] = []
    monkeypatch.setattr(mod, "render_vies_universo",
                        lambda *a, **k: chamou.append({"args": a, "kwargs": k}))
    monkeypatch.setattr(mod, "render_expectativa", lambda *a, **k: None)

    aprovados = [_resultado_b3("A", 2025, ["AAAA3", "BBBB3"])]
    todos = aprovados + [_resultado_b3("B", 2025, ["CCCC3"])]
    precos = _precos_b3(["AAAA3", "BBBB3", "CCCC3"])

    mod.render_safras(aprovados, precos, selic_por_ano={}, taxa_selic_aa=0.0,
                      resultados_todos=todos)

    assert chamou, (
        "render_safras terminou sem executar o Bloco 2 -- a chamada pode "
        "estar presente no código e inalcançável"
    )
    assert chamou[0]["args"][0] is aprovados
    assert chamou[0]["args"][1] is todos
    assert isinstance(chamou[0]["kwargs"]["tabela_com_gate"], pd.DataFrame)


def test_render_safras_nao_executa_o_bloco_2_sem_a_lista_completa(monkeypatch):
    """O outro lado: sem `resultados_todos` não há o que comparar, e o
    bloco não pode ser desenhado."""
    mod, falso = _tela_de_safras_falsa(monkeypatch)
    chamou = []
    monkeypatch.setattr(mod, "render_vies_universo",
                        lambda *a, **k: chamou.append(a))
    monkeypatch.setattr(mod, "render_expectativa", lambda *a, **k: None)

    aprovados = [_resultado_b3("A", 2025, ["AAAA3", "BBBB3"])]
    mod.render_safras(aprovados, _precos_b3(["AAAA3", "BBBB3"]),
                      selic_por_ano={}, taxa_selic_aa=0.0)
    assert not chamou


def test_render_vies_universo_desenha_depois_do_clique(monkeypatch):
    """A-T7-01 (A5): um `return` na primeira linha de
    `render_vies_universo` não muda forma nenhuma e apaga o bloco. Aqui o
    botão é clicado e a função TEM que desenhar: card, ressalvas e tabela.
    """
    mod, falso = _tela_de_safras_falsa(monkeypatch, botao=True)

    aprovados = [_resultado_b3("A", 2025, ["AAAA3", "BBBB3"])]
    todos = aprovados + [_resultado_b3("B", 2025, ["CCCC3"])]
    precos = _precos_b3(["AAAA3", "BBBB3", "CCCC3"])

    mod.render_vies_universo(aprovados, todos, precos, selic_por_ano={},
                             taxa_selic_aa=0.0)

    nomes = falso.nomes()
    assert "button" in nomes, "o botão do Bloco 2 nem chegou a ser desenhado"
    assert "card" in nomes, "clicou e o card do viés não foi desenhado"
    assert "dataframe" in nomes, "clicou e a tabela do viés não foi desenhada"
    assert falso.legendas(), "clicou e nenhuma ressalva foi publicada"
    assert "pb3_vies_universo" in falso.session_state


def test_medicao_de_outra_analise_nao_volta_para_a_tela(monkeypatch):
    """A-T7-03: a chave `pb3_vies_universo` sobrevive a um novo "Rodar".

    Sem invalidação, o Bloco 1 mostra a população nova e o Bloco 2
    redesenha a antiga com carimbo de hora velho — duas populações lado a
    lado, sem aviso.
    """
    mod, falso = _tela_de_safras_falsa(monkeypatch, botao=False)
    falso.session_state["pb3_vies_universo"] = {
        "quando": "01/01/2020 00:00",
        "assinatura": "de-outra-analise",
        "medicao": _vies_universo(*_tabela_vies([13.0], [10.0])),
    }

    aprovados = [_resultado_b3("A", 2025, ["AAAA3", "BBBB3"])]
    mod.render_vies_universo(aprovados, aprovados,
                             _precos_b3(["AAAA3", "BBBB3"]),
                             selic_por_ano={}, taxa_selic_aa=0.0)

    nomes = falso.nomes()
    assert "card" not in nomes and "dataframe" not in nomes, (
        "medição de outra análise foi republicada ao lado de uma população "
        "que não é a dela"
    )
    assert any("não vale para a população atual" in c
               for c in falso.legendas())


def test_medicao_da_mesma_analise_volta_com_as_ressalvas(monkeypatch):
    """O outro lado de A-T7-03: invalidar sempre seria fácil e errado — a
    medição cara tem que voltar quando a população é a mesma."""
    mod, falso = _tela_de_safras_falsa(monkeypatch, botao=False)

    aprovados = [_resultado_b3("A", 2025, ["AAAA3", "BBBB3"])]
    precos = _precos_b3(["AAAA3", "BBBB3"])
    assinatura = mod._assinatura_vies(None, aprovados, aprovados, precos)
    falso.session_state["pb3_vies_universo"] = {
        "quando": "01/01/2020 00:00",
        "assinatura": assinatura,
        "medicao": _vies_universo(*_tabela_vies([13.0, 14.0], [10.0, 10.0])),
    }

    mod.render_vies_universo(aprovados, aprovados, precos,
                             selic_por_ano={}, taxa_selic_aa=0.0)

    assert "card" in falso.nomes()
    assert falso.legendas(), "voltou o número sem as ressalvas"


def test_assinatura_do_vies_muda_quando_a_populacao_muda():
    """A assinatura tem que enxergar o que move a medição: a tabela do
    Bloco 1, as safras mensuráveis dela e o tamanho das duas listas."""
    import views.portfolio_b3_safras as mod

    t1, _ = _tabela_vies([13.0, 14.0], [10.0, 10.0])
    t2, _ = _tabela_vies([13.0, 99.0], [10.0, 10.0])
    precos = _precos_b3(["AAAA3"])
    base = mod._assinatura_vies(t1, [1], [1, 2], precos)

    assert base == mod._assinatura_vies(t1, [1], [1, 2], precos)
    assert base != mod._assinatura_vies(t2, [1], [1, 2], precos)
    assert base != mod._assinatura_vies(t1, [1], [1, 2, 3], precos)
    assert base != mod._assinatura_vies(t1, [1, 2], [1, 2], precos)
    t3 = t1.copy()
    t3.attrs["safras_completas"] = []
    assert base != mod._assinatura_vies(t3, [1], [1, 2], precos)


def test_medicao_recem_feita_volta_no_rerun_seguinte(monkeypatch):
    """A assinatura GRAVADA tem que ser a mesma que a leitura confere.

    Sem este teste, gravar uma assinatura que nunca casa passa despercebido:
    a medição cara seria refeita a cada clique e o rerun seguinte publicaria
    "não vale para a população atual" logo depois de medir. Escritor e
    verificador tem que ler a mesma coisa.
    """
    mod, falso = _tela_de_safras_falsa(monkeypatch, botao=True)
    aprovados = [_resultado_b3("A", 2025, ["AAAA3", "BBBB3"])]
    todos = aprovados + [_resultado_b3("B", 2025, ["CCCC3"])]
    precos = _precos_b3(["AAAA3", "BBBB3", "CCCC3"])

    mod.render_vies_universo(aprovados, todos, precos, selic_por_ano={},
                             taxa_selic_aa=0.0)
    assert "pb3_vies_universo" in falso.session_state

    # Rerun seguinte, mesma analise, sem clicar.
    falso.chamadas.clear()
    falso._botao = False
    mod.render_vies_universo(aprovados, todos, precos, selic_por_ano={},
                             taxa_selic_aa=0.0)

    assert "card" in falso.nomes(), (
        "a medicao recem-feita nao voltou no rerun -- a assinatura gravada "
        "nao e a que a leitura confere"
    )
    assert not any("não vale para a população atual" in c
                   for c in falso.legendas())

"""
views/portfolio_b3_safras.py — relatorio de desempenho safra a safra.

Renderizacao apenas: toda a aritmetica de safra mora em core/b3_safras.py.
views/portfolio_b3.py ja tem 4.400+ linhas e nao recebe logica nova.

`_resumo_safras`, `_legenda_resumo`, `_tabela_para_exibicao` e
`_grafico_barras` sao as pecas de logica deste modulo, e sao deliberadamente
puras (sem streamlit, sem banco) para poder ser testadas direto — nesta
base, teste via Streamlit AppTest vaza atribuicao de modulo e falha só
dentro da suíte completa no CI, nunca isolado (nota de memória
`apptest-vaza-atribuicao-de-modulo`).
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from core.b3_safras import tabela_de_safras
from design.componentes import card_metrica
from views.empresas_b3 import _COR_ALT, _COR_NEU, _COR_POS, _plot_layout

_COLUNAS_RETORNO = ["Estratégia (%)", "Equal-weight (%)", "Selic (%)",
                    "Excesso s/ Selic (pp)"]

_CORES_SERIE = {
    "Estratégia (%)": _COR_POS,
    "Equal-weight (%)": _COR_NEU,
    "Selic (%)": _COR_ALT,
}


def _resumo_safras(tabela: pd.DataFrame) -> dict:
    """Deriva das colunas publicadas tudo que a tela afirma em texto.

    Pura (sem streamlit) para poder ser testada isoladamente. Duas regras
    fechadas por revisão:

    - a população das médias é `attrs["safras_completas"]`, nunca
      `tabela["Completa"]` sozinha — "Completa" só diz que a janela civil
      fechou, e uma safra pode ter janela fechada e zero pregão observado
      (core/b3_safras.py, rodada 4 da Task 4);
    - a frase sobre a safra vigente é derivada de `tabela["Completa"]`
      observada agora, nunca de uma suposição de calendário — de janeiro a
      março `safra_vigente_em` devolve o ano anterior, que ESTÁ dentro do
      range que `views/portfolio_b3.py` itera, e a safra vigente pode
      aparecer como linha parcial (rodada de correção 1, achado I-2).
    """
    safras_medidas = set(tabela.attrs.get("safras_completas", []))
    completas = tabela[tabela["Safra"].isin(safras_medidas)]
    parciais = tabela[~tabela["Completa"]]

    n_medidas = len(completas)
    if n_medidas:
        media_excesso = float(completas["Excesso s/ Selic (pp)"].mean())
        venceu = int((completas["Excesso s/ Selic (pp)"] > 0).sum())
        peso_ausente_max = float(completas["Peso sem preço (%)"].max())
        safra_min = int(completas["Safra"].min())
        safra_max = int(completas["Safra"].max())
    else:
        media_excesso = None
        venceu = 0
        peso_ausente_max = 0.0
        safra_min = None
        safra_max = None

    return {
        "completas": completas,
        "parciais": parciais,
        "n_medidas": n_medidas,
        "media_excesso": media_excesso,
        "venceu": venceu,
        "peso_ausente_max": peso_ausente_max,
        "safra_min": safra_min,
        "safra_max": safra_max,
    }


def _legenda_resumo(resumo: dict) -> str:
    """Texto do caption principal — sempre derivado de `resumo`, nunca do
    intervalo bruto de `tabela["Safra"]` (achado m-1: a legenda contava as
    safras medidas mas publicava o min..max da tabela inteira)."""
    if resumo["n_medidas"]:
        intervalo = f"de {resumo['safra_min']} a {resumo['safra_max']}"
        base = f"{resumo['n_medidas']} safra(s) já encerrada(s) e mensurável(is), {intervalo}."
    else:
        base = "Nenhuma safra encerrada e mensurável ainda."
    base += (" Cada safra é pontuada com dados até o ano anterior e vigora "
            "de abril a março.")

    parciais = resumo["parciais"]
    if parciais.empty:
        base += (" **A safra vigente não aparece nesta tabela** — a janela "
                "dela ainda está em curso; o desempenho em andamento está "
                "no gráfico logo acima, não aqui.")
    else:
        safras_parciais = ", ".join(str(int(s)) for s in sorted(parciais["Safra"]))
        base += (f" **A safra {safras_parciais} está nesta tabela com a "
                "janela em curso** (marcada \"Completa\" = Não) — o retorno "
                "dela é parcial e não entra nas médias acima.")
    return base


def _tabela_para_exibicao(tabela: pd.DataFrame) -> pd.DataFrame:
    """Cópia de exibição: `NaN` vira travessão, nunca célula vazia sem
    explicação (achado m-2). `NaN` nas colunas de retorno é o motor
    (core/b3_safras.py) marcando explicitamente "não medido" — célula
    vazia no `st.dataframe` não distingue isso de dado que sumiu."""
    exibicao = tabela.copy()
    for col in _COLUNAS_RETORNO:
        exibicao[col] = exibicao[col].map(
            lambda v: "—" if pd.isna(v) else f"{v:.1f}"
        )
    return exibicao


def _grafico_barras(completas: pd.DataFrame):
    """Barras Estratégia/Equal-weight/Selic por safra, no mesmo tema
    transparente e nas mesmas cores dos demais gráficos da aba (achado
    I-3): sem isso este era o único gráfico com papel branco opaco e Selic
    trocando de cor em relação ao gráfico vizinho."""
    longo = completas.melt(
        id_vars="Safra",
        value_vars=["Estratégia (%)", "Equal-weight (%)", "Selic (%)"],
        var_name="Série", value_name="Retorno da safra (%)",
    )
    fig = px.bar(longo, x="Safra", y="Retorno da safra (%)",
                 color="Série", barmode="group",
                 color_discrete_map=_CORES_SERIE)
    fig.update_layout(**_plot_layout(360))
    return fig


def render_safras(resultados: list[dict], df_precos: pd.DataFrame, *,
                  selic_por_ano: dict[int, float],
                  taxa_selic_aa: float,
                  resultados_todos: list[dict] | None = None) -> None:
    """Bloco 1: a tabela de safras e a barra por safra contra Selic/EW.

    `resultados_todos` fica reservado para a Task 7 (medicao de vies de
    universo); esta task nao o consome.
    """
    st.markdown("<hr style='margin:24px 0;border-color:var(--app-border);'>",
                unsafe_allow_html=True)
    st.markdown(
        '<div style="font-weight:700;font-size:1.05rem;color:var(--app-text);'
        'margin-bottom:8px;">🗂️ Desempenho safra a safra</div>',
        unsafe_allow_html=True,
    )

    if not resultados or df_precos is None or df_precos.empty:
        st.caption("Rode a análise para reconstruir as safras.")
        return

    tabela = tabela_de_safras(resultados, df_precos,
                              selic_por_ano=selic_por_ano,
                              taxa_selic_aa=taxa_selic_aa)
    if tabela.empty:
        st.caption("Nenhuma safra com líderes reconstruídos.")
        return

    resumo = _resumo_safras(tabela)
    completas = resumo["completas"]

    st.caption(_legenda_resumo(resumo))

    cols = st.columns(3)
    if resumo["n_medidas"]:
        with cols[0]:
            card_metrica("Safras medidas", f"{resumo['n_medidas']}",
                         ajuda="Janela fechada e com pelo menos um pregão observado")
        with cols[1]:
            media = resumo["media_excesso"]
            card_metrica("Excesso médio s/ Selic", f"{media:+.1f} pp",
                         positivo=media > 0,
                         ajuda="Média simples das safras medidas")
        with cols[2]:
            card_metrica("Safras acima da Selic",
                         f"{resumo['venceu']} de {resumo['n_medidas']}",
                         ajuda="Contagem, não significância")
    else:
        with cols[0]:
            card_metrica("Safras medidas", "0",
                         ajuda="Nenhuma safra encerrada e mensurável ainda")

    st.dataframe(_tabela_para_exibicao(tabela), width="stretch", hide_index=True)

    if not completas.empty:
        st.plotly_chart(_grafico_barras(completas), width="stretch",
                        config={"displayModeBar": False},
                        key="pb3_safras_barras")
        st.caption(
            "Barras, não curva acumulada: encadear os retornos produziria um "
            "número grande e único, que esconde quantas safras individuais "
            "ficaram atrás do benchmark."
        )

    # Mesma população das outras agregações (as safras medidas): incluir as
    # não mensuráveis infla o aviso, porque nelas "Peso sem preço" vale
    # 100,0 justamente por não ter havido observação nenhuma (achado I-1).
    ausente = resumo["peso_ausente_max"]
    if ausente > 0:
        st.info(
            f"Em pelo menos uma safra medida, até {ausente:.1f}% do peso "
            "ficou sem preço na janela — deslistagem, incorporação ou "
            "buraco de dado. Essa fatia rende **zero** no cálculo: não "
            "inventamos a perda, mas ela também não rende o que os "
            "sobreviventes renderam."
        )

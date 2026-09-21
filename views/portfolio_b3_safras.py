"""
views/portfolio_b3_safras.py — relatorio de desempenho safra a safra.

Renderizacao apenas: toda a aritmetica mora em core/b3_safras.py.
views/portfolio_b3.py ja tem 4.400+ linhas e nao recebe logica nova.
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from core.b3_safras import tabela_de_safras
from design.componentes import card_metrica


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

    # `attrs["safras_completas"]` — nunca `df["Completa"]` — porque
    # "Completa" só diz que a janela civil fechou; uma safra pode ter
    # janela fechada e zero pregão observado, e essa fatia tem que ficar
    # fora das médias mesmo assim (core/b3_safras.py, rodada 4).
    safras_medidas = set(tabela.attrs.get("safras_completas", []))
    completas = tabela[tabela["Safra"].isin(safras_medidas)]

    # A safra vigente NUNCA chega em carteiras_por_safra (o motor só marca
    # lids_por_ano até ano_atual - 1) — ela não aparece nesta tabela, nem
    # como linha marcada "incompleta". Quem quiser o desempenho em curso
    # olha o gráfico de "Desempenho da safra vigente" logo acima. Dizer
    # isso aqui evita o usuário concluir, por engano, que a tabela parou
    # de atualizar.
    st.caption(
        f"{len(completas)} safra(s) já encerrada(s) e mensurável(is), de "
        f"{int(tabela['Safra'].min())} a {int(tabela['Safra'].max())}. Cada "
        "safra é pontuada com dados até o ano anterior e vigora de abril a "
        "março. **A safra vigente não aparece nesta tabela** — a janela "
        "dela ainda está em curso; o desempenho em andamento está no "
        "gráfico logo acima, não aqui."
    )

    cols = st.columns(3)
    if not completas.empty:
        with cols[0]:
            card_metrica("Safras medidas", f"{len(completas)}",
                         ajuda="Janela fechada e com pelo menos um pregão observado")
        with cols[1]:
            media = float(completas["Excesso s/ Selic (pp)"].mean())
            card_metrica("Excesso médio s/ Selic", f"{media:+.1f} pp",
                         positivo=media > 0,
                         ajuda="Média simples das safras medidas")
        with cols[2]:
            venceu = int((completas["Excesso s/ Selic (pp)"] > 0).sum())
            card_metrica("Safras acima da Selic",
                         f"{venceu} de {len(completas)}",
                         ajuda="Contagem, não significância")
    else:
        with cols[0]:
            card_metrica("Safras medidas", "0",
                         ajuda="Nenhuma safra encerrada e mensurável ainda")

    st.dataframe(tabela, width="stretch", hide_index=True)

    if not completas.empty:
        longo = completas.melt(
            id_vars="Safra",
            value_vars=["Estratégia (%)", "Equal-weight (%)", "Selic (%)"],
            var_name="Série", value_name="Retorno da safra (%)",
        )
        fig = px.bar(longo, x="Safra", y="Retorno da safra (%)",
                     color="Série", barmode="group")
        fig.update_layout(height=360, margin=dict(l=8, r=8, t=8, b=8))
        st.plotly_chart(fig, width="stretch",
                        config={"displayModeBar": False},
                        key="pb3_safras_barras")
        st.caption(
            "Barras, não curva acumulada: encadear os retornos produziria um "
            "número grande e único, que esconde quantas safras individuais "
            "ficaram atrás do benchmark."
        )

    ausente = float(tabela["Peso sem preço (%)"].max() or 0.0)
    if ausente > 0:
        st.info(
            f"Em pelo menos uma safra, até {ausente:.1f}% do peso ficou sem "
            "preço na janela — deslistagem, incorporação ou buraco de dado. "
            "Essa fatia rende **zero** no cálculo: não inventamos a perda, "
            "mas ela também não rende o que os sobreviventes renderam."
        )

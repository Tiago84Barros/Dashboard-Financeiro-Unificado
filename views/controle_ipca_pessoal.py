"""Aba "🧮  IPCA Pessoal" do Controle Financeiro: só desenha.

A conta mora em ``core/ipca_pessoal.py``; a leitura, em
``core/ipca_pessoal_dados.py``; a série do IBGE, em ``core/ipca_grupos.py``.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.ipca_pessoal import (
    MESES_JANELA,
    ORIGEM_ITENS,
    CategoriaIpca,
    ResultadoIpcaPessoal,
    pct_br,
    pp_br,
)
from core.ipca_pessoal_dados import ipca_pessoal_do_mes
from design.componentes import card_metrica, estado_vazio
from design.lacunas import aviso_lacuna, falha_de_acao

_COR_PESSOAL = "#FC5C7D"
_COR_OFICIAL = "#4A9EFF"

_EXPLICACAO = """
O IPCA do IBGE mede a inflação da família média: cada grupo de gasto
(alimentação, habitação, transportes…) entra com o peso que tem no orçamento
dessa família. Aqui a conta é a mesma, mas com **o seu** orçamento.

1. **Peso de cada categoria** = quanto você gastou nela nos 12 meses ÷ o seu
   gasto total no período. O peso fica parado nos 12 meses, como no IBGE.
2. **Variação de preço de cada categoria, mês a mês:**
   - **seus itens** — quando a categoria tem gastos que se repetem (mesma
     descrição em 3 meses ou mais, sem parcelamento) e eles somam pelo menos
     30% do gasto dela, a variação vem desses valores. Num mês sem par
     comparável, vale a do grupo do IPCA.
   - **estimativa oficial** — a variação do grupo equivalente do IPCA.
   - **sem grupo definido** — a categoria não tem grupo equivalente e usa o
     Índice geral do IPCA.
3. **IPCA Pessoal do mês** = soma de peso × variação de cada categoria. Os 12
   meses são encadeados, como o IBGE faz. Se uma categoria não tem variação de
   preço num mês, o peso dela é redistribuído entre as que têm, só nesse mês.

**Fica de fora:** receitas, transferências, investimentos, Financiamento e
Impostos e Taxas. O pagamento da fatura do cartão só entra (como "Cartão (sem
detalhe)") nos meses em que a fatura não foi importada; com a fatura importada,
entram as compras dela, para o mesmo gasto não ser contado duas vezes. A regra
vale por mês, não por cartão: com mais de um cartão, importar a fatura de um
também tira, nesse mês, o pagamento da fatura do outro.

**Ignorados:** quando o valor de um gasto repetido muda mais de 50% de um mês
para o outro, o par sai da conta — parece troca de produto ou erro de
lançamento, não preço.
"""


def rotulo_origem(cat: CategoriaIpca) -> str:
    if cat.origem == ORIGEM_ITENS:
        return f"{ORIGEM_ITENS} ({cat.meses_proprios} de {MESES_JANELA} meses)"
    return cat.origem


def tabela_categorias(res: ResultadoIpcaPessoal) -> pd.DataFrame:
    return pd.DataFrame([{
        "Categoria": c.categoria,
        "Grupo do IPCA": c.grupo,
        "Peso": pct_br(c.peso, 1),
        "Variação de preço (12m)": pct_br(c.variacao_12m),
        "Origem": rotulo_origem(c),
        "Contribuição": pp_br(c.contribuicao),
    } for c in res.categorias])


def tabela_pesos(res: ResultadoIpcaPessoal) -> pd.DataFrame:
    return pd.DataFrame([{
        "Grupo do IPCA": grupo,
        "Seu peso": pct_br(usuario, 1),
        "Peso no IPCA (IBGE)": "—" if ibge is None else pct_br(ibge, 1),
    } for grupo, usuario, ibge in res.pesos_por_grupo])


def grafico_acumulado(res: ResultadoIpcaPessoal) -> go.Figure:
    meses = [p.mes for p in res.mensal]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=meses, y=[p.acum_pessoal * 100 for p in res.mensal], name="IPCA Pessoal",
        mode="lines+markers", line=dict(color=_COR_PESSOAL, width=3)))
    fig.add_trace(go.Scatter(
        x=meses, y=[p.acum_oficial * 100 for p in res.mensal], name="IPCA oficial",
        mode="lines+markers", line=dict(color=_COR_OFICIAL, width=2, dash="dot")))
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="#9CA3AF", separators=",.", hovermode="x unified",
        legend=dict(orientation="h", y=-0.22),
        xaxis=dict(tickformat="%m/%Y", gridcolor="#1E2533"),
        yaxis=dict(ticksuffix="%", gridcolor="#1E2533"),
    )
    return fig


def render_ipca_pessoal(ano: int, mes: int) -> None:
    st.markdown("#### 🧮 IPCA Pessoal")
    st.caption("A inflação da sua cesta de consumo nos últimos 12 meses, ao lado "
               "do IPCA oficial do IBGE.")
    try:
        res = ipca_pessoal_do_mes(ano, mes)
    except Exception as exc:  # noqa: BLE001
        falha_de_acao("Não foi possível calcular o IPCA Pessoal agora.", exc)
        return

    if res is None:
        msg = ("A leitura dos seus lançamentos falhou; o IPCA Pessoal não foi "
               "calculado.")
        aviso_lacuna(msg, codigo="tela.controle.ipca_pessoal.leitura", nivel="warning")
        st.warning(msg)
        return
    if not res.ok:
        aviso_lacuna(res.motivo, codigo="tela.controle.ipca_pessoal.sem_calculo")
        estado_vazio(res.motivo, "🧮")
        for aviso in res.avisos:
            st.info(aviso)
        return

    diferenca = res.acumulado_12m - res.oficial_12m
    k1, k2, k3 = st.columns(3)
    with k1:
        card_metrica("IPCA Pessoal (12 meses)", pct_br(res.acumulado_12m))
    with k2:
        card_metrica("IPCA oficial (12 meses)", pct_br(res.oficial_12m))
    with k3:
        card_metrica("Diferença", pp_br(diferenca), positivo=diferenca <= 0)
    st.caption(f"Janela: {res.inicio:%m/%Y} a {res.fim:%m/%Y}.")
    for aviso in res.avisos:
        st.info(aviso)

    st.plotly_chart(grafico_acumulado(res), width="stretch",
                    config={"displayModeBar": False})

    st.markdown("##### Por categoria")
    st.dataframe(tabela_categorias(res), width="stretch", hide_index=True)

    st.markdown("##### Seus pesos × pesos do IPCA")
    st.caption(f"Peso do IBGE em {res.fim:%m/%Y}.")
    st.dataframe(tabela_pesos(res), width="stretch", hide_index=True)

    with st.expander("Como esta conta é feita"):
        st.markdown(_EXPLICACAO)
        if res.ignorados:
            st.markdown("**Itens ignorados na medida de preço**")
            st.dataframe(pd.DataFrame([{
                "Item": i.item, "Categoria": i.categoria,
                "Mês": f"{i.mes:%m/%Y}", "Motivo": i.motivo,
            } for i in res.ignorados]), width="stretch", hide_index=True)

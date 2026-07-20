"""Seção integrada do Analista Financeiro Pessoal IA."""
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st

from core.analista_financeiro import get_diagnostico, simular_patrimonio
from core.utils import fmt_moeda
from design.componentes import badge_status, card_metrica, container_pagina, secao_titulo


def _fmt_pct(valor) -> str:
    return "Dados insuficientes" if valor is None else f"{valor:.1f}%"


def _aviso_fonte(dados: dict) -> None:
    if dados["dados_reais"]:
        badge_status("Diagnóstico com dados reais", "sucesso")
        return
    st.warning(
        "Esta visualização contém dados demonstrativos ou de fallback. "
        "Ela serve para validar a seção, não para orientar decisões financeiras reais.",
        icon="⚠️",
    )


def _resumo(dados: dict) -> None:
    m = dados["metricas"]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Receitas do mês", fmt_moeda(m["receitas"]))
    with c2:
        card_metrica("Despesas do mês", fmt_moeda(m["despesas"]))
    with c3:
        card_metrica("Resultado", fmt_moeda(m["resultado"]), positivo=m["resultado"] >= 0)
    with c4:
        card_metrica("Taxa de poupança", _fmt_pct(m["taxa_poupanca_pct"]))

    secao_titulo("Prioridades sugeridas", "🧭", "Recomendações para revisão humana; nenhuma ação é executada automaticamente.")
    for item in dados["recomendacoes"]:
        icon = {"alta": "🔴", "media": "🟠", "baixa": "🟢"}[item["prioridade"]]
        with st.container(border=True):
            st.markdown(f"**{icon} {item['titulo']}**")
            st.write(item["acao"])
            st.caption(f"Evidência: {item['motivo']}")


def _gastos(dados: dict) -> None:
    categorias = dados["categorias"]
    if categorias:
        frame = pd.DataFrame(categorias)
        fig = px.bar(frame, x="gasto", y="nome", orientation="h", title="Despesas por categoria")
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=380)
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Não há despesas categorizadas suficientes para o período.")

    secao_titulo("Padrões para revisar", "🔎", "São candidatos analíticos, não classificações de desperdício.")
    if not dados["anomalias"]:
        st.success("Nenhum padrão com os gatilhos atuais foi encontrado.")
    for item in dados["anomalias"]:
        with st.container(border=True):
            st.markdown(f"**{item['titulo']}** · confiança {item['confianca']}")
            st.write(item["descricao"])
            st.caption(f"Impacto observado: {fmt_moeda(item['valor'])} · requer confirmação do usuário")


def _carteira_metas(dados: dict) -> None:
    m = dados["metricas"]
    c1, c2, c3 = st.columns(3)
    with c1:
        card_metrica("Patrimônio investido", fmt_moeda(m["patrimonio_investido"]))
    with c2:
        card_metrica("Proventos em 12 meses", fmt_moeda(m["proventos_12m"]))
    with c3:
        card_metrica("Maior posição", _fmt_pct(m["maior_posicao_pct"]))

    secao_titulo("Leitura da carteira", "📊")
    if not dados["carteira"]:
        st.info("Sem alertas de concentração ou sem posições disponíveis.")
    for item in dados["carteira"]:
        st.warning(f"**{item['titulo']}** — {item['descricao']}\n\n{item['evidencia']}")

    secao_titulo("Metas financeiras", "🎯")
    if not dados["metas"]:
        st.info("Nenhuma meta disponível.")
    for meta in dados["metas"]:
        with st.container(border=True):
            st.markdown(f"**{meta['nome']}** · {meta['status'].replace('_', ' ')}")
            st.progress(min(1.0, max(0.0, float(meta["pct"]) / 100)), text=f"{meta['pct']:.1f}% de {fmt_moeda(meta['alvo'])}")
            if meta.get("aporte"):
                st.caption(f"Aporte mensal calculado para o prazo: {fmt_moeda(meta['aporte'])}")


def _simulador(dados: dict) -> None:
    st.caption("Simulação nominal e educacional. Rentabilidade futura é incerta e os valores não constituem recomendação.")
    c1, c2, c3 = st.columns(3)
    with c1:
        inicial = st.number_input("Valor inicial", min_value=0.0, value=float(dados["metricas"]["patrimonio_investido"]), step=1000.0)
    with c2:
        aporte = st.number_input("Aporte mensal", min_value=0.0, value=1000.0, step=100.0)
    with c3:
        anos = st.slider("Prazo (anos)", 1, 40, 10)

    cenarios = {"Conservador (4% a.a.)": 4.0, "Base (7% a.a.)": 7.0, "Otimista (10% a.a.)": 10.0}
    linhas = []
    for nome, taxa in cenarios.items():
        for ponto in simular_patrimonio(inicial, aporte, anos, taxa):
            linhas.append({**ponto, "ano": ponto["mes"] / 12, "cenario": nome})
    frame = pd.DataFrame(linhas)
    fig = px.line(frame, x="ano", y="patrimonio", color="cenario", title="Projeção de patrimônio por cenário")
    fig.update_yaxes(tickprefix="R$ ")
    st.plotly_chart(fig, width="stretch")


def _perguntas(dados: dict) -> None:
    st.caption("Respostas explicáveis geradas com regras determinísticas e os dados já exibidos nesta seção.")
    pergunta = st.selectbox("Pergunta", [
        "Qual é minha principal prioridade agora?",
        "Minha carteira está concentrada?",
        "Há padrões de gastos para revisar?",
    ])
    if pergunta.startswith("Qual"):
        item = dados["recomendacoes"][0]
        st.info(f"**{item['titulo']}** — {item['acao']} Motivo: {item['motivo']}")
    elif "carteira" in pergunta:
        if dados["carteira"]:
            st.info(" ".join(item["descricao"] for item in dados["carteira"]))
        else:
            st.info("Não identifiquei concentração acima dos gatilhos ou faltam dados da carteira.")
    else:
        st.info(
            f"Foram encontrados {len(dados['anomalias'])} padrão(ões) para revisão humana. "
            "Consulte a aba Gastos e padrões para ver evidências e valores."
        )


def render() -> None:
    container_pagina("Analista Financeiro Pessoal IA", "Diagnóstico integrado, explicável e somente leitura", "🧠")
    hoje = date.today()
    dados = get_diagnostico(hoje.year, hoje.month)
    _aviso_fonte(dados)
    tabs = st.tabs(["Resumo", "Gastos e padrões", "Carteira e metas", "Simulador", "Perguntas"])
    with tabs[0]:
        _resumo(dados)
    with tabs[1]:
        _gastos(dados)
    with tabs[2]:
        _carteira_metas(dados)
    with tabs[3]:
        _simulador(dados)
    with tabs[4]:
        _perguntas(dados)

"""Bloco "Atribuição contra a meta" da aba Histórico (Tema 5 da auditoria).

Só interface: os números vêm de ``core.carteira_atribuicao``. Fica logo abaixo
do "Risco da carteira" porque mede a mesma série diária — o risco diz quanto
a carteira oscila; a atribuição diz de onde veio a diferença para a meta.
"""
from __future__ import annotations

import html as _html

import plotly.graph_objects as go
import streamlit as st

from core.carteira_atribuicao import (
    CLASSES,
    EFEITOS,
    REFERENCIA,
    ROTULO,
    get_atribuicao_carteira,
    mes_br,
    resumo_atribuicao,
    totais_por_efeito,
)
from core.utils import fmt_percentual
from design.lacunas import aviso_lacuna, detalhe_tecnico

# Cores literais no Plotly (o adaptador de tema troca as do tema escuro).
_COR_EFEITO = {"alocacao": "#4A9EFF", "selecao": "#2ECC9A", "interacao": "#F5A623"}
_COR_EXCESSO = "#E5E7EB"
_COR_NEUTRO = "#9CA3AF"
_NOME = {"alocacao": "Alocação", "selecao": "Seleção", "interacao": "Interação"}
_EXPLICA = {
    "alocacao": "ganho ou perda por pesar a classe acima ou abaixo da meta, "
                "medido pela referência dela contra a meta inteira.",
    "selecao": "ganho ou perda por os ativos escolhidos renderem mais ou menos "
               "que a referência da classe, no peso da meta.",
    "interacao": "o cruzamento dos dois: seleção feita com peso diferente do da "
                 "meta (boa escolha com peso acima soma; com peso abaixo, desperdiça).",
}


def _pct(v: float | None, casas: int = 2, sinal: bool = False) -> str:
    if v is None:
        return "—"
    return fmt_percentual(v * 100, casas, sinal)


def _pp(v: float | None) -> str:
    return "—" if v is None else f"{v * 100:+.2f}".replace(".", ",") + " p.p."


def _grafico(meses: list[dict]) -> go.Figure:
    completos = [m for m in meses if m["completo"]]
    x = [mes_br(m["mes"]) for m in completos]
    fig = go.Figure()
    for e in EFEITOS:
        fig.add_trace(go.Bar(
            x=x, y=[totais_por_efeito(m["efeitos"])[e] * 100 for m in completos],
            name=_NOME[e], marker_color=_COR_EFEITO[e],
            hovertemplate="%{x}<br>" + _NOME[e] + " %{y:+.2f} p.p.<extra></extra>",
        ))
    fig.add_trace(go.Scatter(
        x=x, y=[m["excesso"] * 100 for m in completos], name="Excesso sobre a meta",
        mode="markers", marker={"color": _COR_EXCESSO, "size": 11, "symbol": "diamond",
                                "line": {"color": "#111827", "width": 1}},
        hovertemplate="%{x}<br>Excesso %{y:+.2f} p.p.<extra></extra>",
    ))
    fig.update_layout(
        barmode="relative",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 0, "l": 0, "r": 0},
        height=260,
        yaxis={"showgrid": True, "gridcolor": "#1E2533", "ticksuffix": " p.p.",
               "zeroline": True, "zerolinecolor": "#374151"},
        xaxis={"showgrid": False, "type": "category"},
        legend={"orientation": "h", "y": 1.1, "x": 0},
    )
    return fig


def _linhas_classe(efeitos: dict, extra: dict | None = None) -> list[dict]:
    linhas = []
    for c in CLASSES:
        ef = efeitos.get(c)
        if not ef:
            continue
        linha = {"Classe": ROTULO[c]}
        if extra:
            linha.update(extra[c])
        linha.update({_NOME[e]: _pp(ef[e]) for e in EFEITOS})
        linha["Total"] = _pp(sum(ef.values()))
        linhas.append(linha)
    return linhas


def render_atribuicao_carteira() -> None:
    st.markdown("#### Atribuição contra a meta")
    atr = get_atribuicao_carteira()
    if not atr.get("disponivel"):
        aviso_lacuna("Atribuição indisponível: "
                     + str(atr.get("motivo") or "sem motivo informado") + ".",
                     codigo="tela.investimentos.atribuicao_indisponivel")
        st.info("Esta análise não está disponível no momento.")
        return

    meta = atr.get("meta") or {}
    desde = atr.get("meta_desde")
    detalhe_tecnico(
        "Brinson-Fachler por mês fechado, contra a meta de alocação da política "
        f"(versão {atr.get('meta_versao')}"
        + (f", definida em {desde:%d/%m/%Y} e aplicada também aos meses anteriores"
           if hasattr(desde, "strftime") else "")
        + "). Referências: " + ", ".join(f"{ROTULO[c]} = {REFERENCIA[c]}" for c in CLASSES)
        + f". Meses encadeados pelo método de {atr.get('linking')}: a soma dos "
        "efeitos fecha o excesso acumulado.",
        codigo="investimentos.atribuicao.metodologia",
    )
    st.caption(
        "Efeitos por mês fechado contra a meta de alocação da política: "
        + ", ".join(f"{ROTULO[c]} {_pct(meta.get(c), 0)}" for c in CLASSES) + "."
    )
    st.markdown(
        "\n".join(f"- **{_NOME[e]}** — {_EXPLICA[e]}" for e in EFEITOS)
    )

    resumo = resumo_atribuicao(atr)
    acc = atr.get("acumulado") or {}
    if resumo:
        st.markdown(
            '<div style="border-left:3px solid var(--app-primary);background:var(--app-surface);'
            'padding:9px 13px;border-radius:0 8px 8px 0;margin:6px 0 10px;'
            f'font-size:0.80rem;color:var(--app-text);line-height:1.45;">{_html.escape(resumo)}'
            f' Carteira {_html.escape(_pct(acc.get("R_p")))} contra '
            f'{_html.escape(_pct(acc.get("R_b")))} da meta.</div>',
            unsafe_allow_html=True,
        )
        if not atr.get("contiguo"):
            aviso_lacuna("Os meses encadeados não são seguidos: o acumulado pula os "
                         "meses incompletos.",
                         codigo="tela.investimentos.atribuicao_meses_nao_contiguos")
        st.plotly_chart(_grafico(atr["meses"]), width="stretch",
                        config={"displayModeBar": False})
        st.markdown("**Acumulado por classe**")
        st.dataframe(_linhas_classe(acc["efeitos"]), hide_index=True, width="stretch")
    else:
        aviso_lacuna("Nenhum mês completo: falta a referência ou o retorno medido de "
                     "alguma classe em todos os meses.",
                     codigo="tela.investimentos.atribuicao_sem_mes_completo")
        st.info("Esta análise não está disponível no momento.")

    st.markdown("**Mês a mês**")
    for m in atr.get("meses") or []:
        rotulo = mes_br(m["mes"])
        if not m["completo"]:
            aviso_lacuna(f"{rotulo} incompleto, fora do acumulado: "
                         + "; ".join(m["motivos"]) + ".",
                         codigo="tela.investimentos.atribuicao_mes_incompleto")
            st.markdown(f"- **{rotulo}** — sem dados suficientes, fora do acumulado.")
            continue
        with st.expander(f"{rotulo} · excesso {_pp(m['excesso'])} "
                         f"(carteira {_pct(m['R_p'])}, meta {_pct(m['R_b'])})"):
            extra = {c: {"Peso real": _pct(m["w_p"].get(c), 1),
                         "Meta": _pct(m["w_b"].get(c), 0),
                         "Retorno": _pct(m["r_p"].get(c)),
                         "Referência": _pct(m["r_b"].get(c)),
                         "Medido": _pct(m["cobertura"].get(c), 0)} for c in CLASSES}
            st.dataframe(_linhas_classe(m["efeitos"], extra), hide_index=True,
                         width="stretch")
            for nota in m.get("notas") or []:
                detalhe_tecnico(f"{rotulo}: {nota}",
                                codigo="investimentos.atribuicao.nota_mensal")

    fonte = atr.get("fonte_classe") or {}
    notas = [
        "Peso real = valor inteiro da classe no último pregão do mês anterior: "
        "ativos com cotação a quantidade × preço da série diária; IPCA+, "
        "prefixado e CDB pelo valor de mercado da foto de posição do fim do mês.",
        "Retorno da classe = só a parte com preço diário (coluna **Medido**). O "
        "que não tem preço pesa na alocação, mas não vira retorno zero.",
        "Renda fixa: retorno " + (fonte.get("renda_fixa") or "ausente")
        + " — o Tesouro Selic sai do CDI, então a seleção da renda fixa não é "
        "medida (dá ~0 por construção).",
        f"Fora das quatro classes: {_pct(atr.get('pct_fora_politica'), 1)} do "
        f"patrimônio de hoje ({', '.join(atr.get('fora_politica') or []) or 'nada'}); "
        "não entra nos pesos.",
        "FIIs contra o fechamento mensal oficial do IFIX (B3); sem ele, o IFIX "
        "spot da brapi e depois o XFIX11 (o proxy do Portfólio Global), os dois "
        "só em data exata — o mês diz qual usou. Sem nenhum, o mês fica "
        "incompleto em vez de usar uma data vizinha.",
        "Exterior: SPY em reais, sem dividendos — igual aos ETFs americanos da "
        "carteira, cujos dividendos não estão no banco.",
        "Ativo vendido antes de hoje entra no peso pela foto, mas o retorno "
        "dele no mês não é medido (a série diária reconstrói só as posições "
        "atuais).",
    ]
    for n in notas:
        detalhe_tecnico(n, codigo="investimentos.atribuicao.cobertura_limitacoes")

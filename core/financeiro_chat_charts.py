"""
core/financeiro_chat_charts.py
Renderização SEGURA de gráficos acionados pelo chat de Controle Financeiro.

A LLM nunca gera código: ela devolve apenas uma diretiva estruturada
({"tipo", "escopo", "meses", "percentual", "titulo"}). Este módulo valida o tipo
contra uma whitelist (_RENDERERS) e desenha o gráfico Plotly usando SEMPRE as
séries numéricas REAIS de `chart_meta` (montado por core.llm_context_financeiro
a partir dos dados do próprio usuário). Tipos desconhecidos são ignorados; um
gráfico que falhe não derruba o chat.

Projeções e simulações são explicitamente rotuladas como ESTIMATIVA no título e
na legenda — nunca se confundem com dados históricos.

Padrão visual: Plotly escuro, fundo transparente, paleta da view.
"""
from __future__ import annotations

import logging

import plotly.graph_objects as go
import streamlit as st

logger = logging.getLogger(__name__)

# ── Paleta (igual à view controle_financeiro) ─────────────────────────────────
_COR_RECEITA = "#00C896"
_COR_DESPESA = "#FC5C7D"
_COR_INVEST = "#4A9EFF"
_COR_NEUTRO = "#9CA3AF"
_COR_ESTIM = "#F6C90E"
_CORES_CAT = [
    "#FC5C7D", "#F6C90E", "#4A9EFF", "#00C896", "#9B59B6",
    "#FF6B35", "#1ABC9C", "#E67E22", "#3498DB", "#E91E63",
]

_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#C4CBD5"),
    margin=dict(l=10, r=10, t=44, b=10),
    height=340,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
)
_MOEDA_AXIS = dict(showgrid=True, gridcolor="#1E2533", tickprefix="R$ ", tickformat=",.0f")


def _fmt_brl(v: float) -> str:
    return ("R$ " + f"{float(v or 0):,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def _emit(fig: go.Figure) -> None:
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ── Renderers (cada um lê apenas de meta) ─────────────────────────────────────

def _r_despesas_categoria(d: dict, meta: dict) -> bool:
    escopo = str(d.get("escopo", "mes")).lower()
    cats = meta.get("categorias_anual" if escopo == "ano" else "categorias_mes") or []
    cats = [c for c in cats if float(c.get("gasto", 0) or 0) > 0]
    if not cats:
        return False
    cats = sorted(cats, key=lambda c: c["gasto"], reverse=True)[:10]
    nomes = [c["nome"] for c in cats]
    gastos = [c["gasto"] for c in cats]
    titulo = d.get("titulo") or f"Despesas por categoria ({'ano' if escopo == 'ano' else 'mês'})"
    fig = go.Figure(go.Bar(
        x=gastos, y=nomes, orientation="h",
        marker_color=_CORES_CAT[:len(cats)], opacity=0.9,
        hovertemplate="<b>%{y}</b><br>R$ %{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(**_LAYOUT, title=titulo,
                      xaxis=_MOEDA_AXIS, yaxis=dict(autorange="reversed"))
    _emit(fig)
    return True


def _r_fluxo_mensal(d: dict, meta: dict) -> bool:
    fluxo = meta.get("fluxo_mensal") or []
    if not fluxo:
        return False
    labels = [h["label"] for h in fluxo]
    fig = go.Figure()
    fig.add_bar(x=labels, y=[h["receitas"] for h in fluxo], name="Receitas",
                marker_color=_COR_RECEITA, opacity=0.9)
    fig.add_bar(x=labels, y=[h["despesas"] for h in fluxo], name="Despesas",
                marker_color=_COR_DESPESA, opacity=0.9)
    fig.add_trace(go.Scatter(x=labels, y=[h["saldo"] for h in fluxo], name="Saldo",
                             mode="lines+markers", line=dict(color=_COR_INVEST, width=2)))
    fig.update_layout(**_LAYOUT, barmode="group",
                      title=d.get("titulo") or "Fluxo de caixa mensal",
                      xaxis=dict(showgrid=False), yaxis=_MOEDA_AXIS)
    _emit(fig)
    return True


def _r_comparativo_anual(d: dict, meta: dict) -> bool:
    anos = meta.get("anos") or []
    por_ano = meta.get("por_ano") or {}
    if not anos:
        return False
    labels = [str(a) for a in anos]
    rec = [float(por_ano.get(str(a), {}).get("receitas", 0) or 0) for a in anos]
    des = [float(por_ano.get(str(a), {}).get("despesas", 0) or 0) for a in anos]
    inv = [float(por_ano.get(str(a), {}).get("investimentos", 0) or 0) for a in anos]
    fig = go.Figure()
    fig.add_bar(x=labels, y=rec, name="Receitas", marker_color=_COR_RECEITA, opacity=0.9)
    fig.add_bar(x=labels, y=des, name="Despesas", marker_color=_COR_DESPESA, opacity=0.9)
    fig.add_bar(x=labels, y=inv, name="Investimentos", marker_color=_COR_INVEST, opacity=0.9)
    fig.update_layout(**_LAYOUT, barmode="group",
                      title=d.get("titulo") or "Comparativo ano a ano",
                      xaxis=dict(showgrid=False), yaxis=_MOEDA_AXIS)
    _emit(fig)
    return True


def _r_evolucao_patrimonio(d: dict, meta: dict) -> bool:
    anos = meta.get("anos") or []
    por_ano = meta.get("por_ano") or {}
    if not anos:
        return False
    labels, acum_vals, acum = [], [], 0.0
    for a in anos:
        da = por_ano.get(str(a), {})
        inv = float(da.get("investimentos") or 0) or max(0.0, float(da.get("saldo", 0) or 0))
        acum += inv
        labels.append(str(a))
        acum_vals.append(round(acum, 2))
    fig = go.Figure(go.Scatter(
        x=labels, y=acum_vals, mode="lines+markers", name="Investido acumulado",
        line=dict(color=_COR_INVEST, width=3), fill="tozeroy",
        fillcolor="rgba(74,158,255,0.12)",
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(**_LAYOUT, title=d.get("titulo") or "Evolução do patrimônio investido",
                      xaxis=dict(showgrid=False), yaxis=_MOEDA_AXIS)
    _emit(fig)
    return True


def _r_essencial(d: dict, meta: dict) -> bool:
    ess = meta.get("essencialidade_mes") or {}
    itens = [("Essenciais", ess.get("essencial", 0), _COR_RECEITA),
             ("Não essenciais", ess.get("nao_essencial", 0), _COR_DESPESA),
             ("Não classificadas", ess.get("nao_classificada", 0), _COR_NEUTRO)]
    itens = [(n, float(v or 0), c) for n, v, c in itens if float(v or 0) > 0]
    if not itens:
        return False
    fig = go.Figure(go.Pie(
        labels=[n for n, _v, _c in itens], values=[v for _n, v, _c in itens],
        marker=dict(colors=[c for _n, _v, c in itens]), hole=0.5,
        hovertemplate="<b>%{label}</b><br>R$ %{value:,.2f}<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(**_LAYOUT,
                      title=d.get("titulo") or "Despesas essenciais × não essenciais (mês)")
    _emit(fig)
    return True


def _r_projecao_saldo(d: dict, meta: dict) -> bool:
    """Saldo histórico (real) + projeção pela média dos últimos meses (ESTIMATIVA)."""
    fluxo = meta.get("fluxo_mensal") or []
    if len(fluxo) < 2:
        return False
    try:
        meses = int(d.get("meses") or 6)
    except Exception:
        meses = 6
    meses = max(1, min(24, meses))

    hist_labels = [h["label"] for h in fluxo]
    hist_saldo = [float(h["saldo"] or 0) for h in fluxo]
    base = hist_saldo[-3:] if len(hist_saldo) >= 3 else hist_saldo
    media = sum(base) / len(base)

    # rótulos futuros a partir do último mês conhecido
    ult = fluxo[-1]
    ano, mes = int(ult.get("ano") or 0), int(ult.get("mes") or 0)
    fut_labels = []
    m, y = mes, ano
    for _ in range(meses):
        m += 1
        if m > 12:
            m, y = 1, y + 1
        fut_labels.append(f"{m:02d}/{str(y)[-2:]}" if y else f"+{_ + 1}")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist_labels, y=hist_saldo, mode="lines+markers", name="Saldo real",
        line=dict(color=_COR_INVEST, width=2),
    ))
    # conecta o último real ao início da projeção
    fig.add_trace(go.Scatter(
        x=[hist_labels[-1]] + fut_labels,
        y=[hist_saldo[-1]] + [round(media, 2)] * meses,
        mode="lines+markers", name=f"Projeção (média {len(base)}m · estimativa)",
        line=dict(color=_COR_ESTIM, width=2, dash="dash"),
    ))
    titulo = d.get("titulo") or f"Projeção de saldo — {meses} meses (estimativa)"
    fig.update_layout(**_LAYOUT, title=titulo,
                      xaxis=dict(showgrid=False), yaxis=_MOEDA_AXIS)
    _emit(fig)
    st.caption(f"Premissa: saldo mensal futuro ≈ média dos últimos {len(base)} meses "
               f"({_fmt_brl(media)}/mês). Estimativa simples, não é previsão.")
    return True


def _r_simulacao_corte(d: dict, meta: dict) -> bool:
    """Despesas não essenciais atuais × após corte de X% (ESTIMATIVA)."""
    try:
        pct = float(d.get("percentual") or 0)
    except Exception:
        pct = 0.0
    if pct <= 0:
        pct = 15.0
    pct = max(1.0, min(100.0, pct))

    ess = meta.get("essencialidade_mes") or {}
    nao_ess = float(ess.get("nao_essencial", 0) or 0)
    essenc = float(ess.get("essencial", 0) or 0)
    naocl = float(ess.get("nao_classificada", 0) or 0)
    if nao_ess <= 0:
        return False
    economia = round(nao_ess * pct / 100.0, 2)
    despesa_atual = essenc + nao_ess + naocl
    despesa_nova = round(despesa_atual - economia, 2)

    fig = go.Figure()
    fig.add_bar(x=["Despesa atual", f"Após corte de {pct:.0f}%"],
                y=[despesa_atual, despesa_nova],
                marker_color=[_COR_DESPESA, _COR_RECEITA], opacity=0.9,
                text=[_fmt_brl(despesa_atual), _fmt_brl(despesa_nova)],
                textposition="outside",
                hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>")
    fig.update_layout(**_LAYOUT,
                      title=d.get("titulo") or f"Simulação: corte de {pct:.0f}% nos não essenciais (estimativa)",
                      xaxis=dict(showgrid=False), yaxis=_MOEDA_AXIS, showlegend=False)
    _emit(fig)
    st.caption(f"Corte de {pct:.0f}% sobre {_fmt_brl(nao_ess)} (não essenciais) = "
               f"economia de {_fmt_brl(economia)}/mês. Essenciais preservados. Estimativa.")
    return True


def _r_cartao_estabelecimentos(d: dict, meta: dict) -> bool:
    itens = [i for i in (meta.get("cartao_estabelecimentos") or [])
             if float(i.get("gasto", 0) or 0) > 0]
    if not itens:
        return False
    itens = sorted(itens, key=lambda i: i["gasto"], reverse=True)[:12]
    fig = go.Figure(go.Bar(
        x=[i["gasto"] for i in itens], y=[i["nome"] for i in itens], orientation="h",
        marker_color=_COR_INVEST, opacity=0.9,
        hovertemplate="<b>%{y}</b><br>R$ %{x:,.2f}<extra></extra>",
    ))
    fig.update_layout(**_LAYOUT, title=d.get("titulo") or "Maiores gastos por estabelecimento",
                      xaxis=_MOEDA_AXIS, yaxis=dict(autorange="reversed"))
    _emit(fig)
    return True


def _r_cartao_evolucao(d: dict, meta: dict) -> bool:
    serie = meta.get("cartao_evolucao") or []
    if len(serie) < 2:
        return False
    fig = go.Figure(go.Bar(
        x=[s["label"] for s in serie], y=[s["total"] for s in serie],
        marker_color=_COR_DESPESA, opacity=0.9,
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(**_LAYOUT, title=d.get("titulo") or "Evolução mensal das compras no cartão",
                      xaxis=dict(showgrid=False), yaxis=_MOEDA_AXIS, showlegend=False)
    _emit(fig)
    return True


def _r_cartao_projecao(d: dict, meta: dict) -> bool:
    """Faturas futuras estimadas pelas parcelas restantes (ESTIMATIVA)."""
    serie = meta.get("cartao_projecao") or []
    if not serie:
        return False
    fig = go.Figure(go.Bar(
        x=[s["label"] for s in serie], y=[s["total"] for s in serie],
        marker_color=_COR_ESTIM, opacity=0.9,
        hovertemplate="<b>%{x}</b><br>R$ %{y:,.2f}<extra></extra>",
    ))
    fig.update_layout(**_LAYOUT,
                      title=d.get("titulo") or "Projeção de faturas futuras (parcelas · estimativa)",
                      xaxis=dict(showgrid=False), yaxis=_MOEDA_AXIS, showlegend=False)
    _emit(fig)
    st.caption("Projeção pelas parcelas já lançadas que ainda vencerão. Não inclui compras "
               "futuras novas — é um piso, não a fatura final.")
    return True


def _r_cartao_assinaturas(d: dict, meta: dict) -> bool:
    """Barras do custo mensal estimado por assinatura."""
    itens = [i for i in (meta.get("cartao_assinaturas") or [])
             if float(i.get("gasto", 0) or 0) > 0]
    if not itens:
        return False
    itens = sorted(itens, key=lambda i: i["gasto"], reverse=True)[:15]
    fig = go.Figure(go.Bar(
        x=[i["gasto"] for i in itens], y=[i["nome"] for i in itens], orientation="h",
        marker_color="#9B59B6", opacity=0.9,
        hovertemplate="<b>%{y}</b><br>~R$ %{x:,.2f}/mês<extra></extra>",
    ))
    fig.update_layout(**_LAYOUT, title=d.get("titulo") or "Assinaturas — custo mensal estimado",
                      xaxis=_MOEDA_AXIS, yaxis=dict(autorange="reversed"))
    _emit(fig)
    return True


_RENDERERS = {
    "despesas_categoria": _r_despesas_categoria,
    "fluxo_mensal": _r_fluxo_mensal,
    "comparativo_anual": _r_comparativo_anual,
    "evolucao_patrimonio": _r_evolucao_patrimonio,
    "essencial_vs_nao_essencial": _r_essencial,
    "essencial": _r_essencial,
    "projecao_saldo": _r_projecao_saldo,
    "simulacao_corte": _r_simulacao_corte,
    # Específicos do cartão de crédito
    "cartao_estabelecimentos": _r_cartao_estabelecimentos,
    "cartao_evolucao": _r_cartao_evolucao,
    "cartao_projecao": _r_cartao_projecao,
    "cartao_assinaturas": _r_cartao_assinaturas,
}


def render_financas_charts(directives, meta: dict | None = None) -> int:
    """
    Desenha os gráficos das diretivas válidas. Retorna quantos foram desenhados.
    Ignora tipos desconhecidos e captura falhas por gráfico (não derruba o chat).
    """
    if not directives:
        return 0
    meta = meta or {}
    if isinstance(directives, dict):
        directives = [directives]
    desenhados = 0
    for d in directives[:2]:  # teto de 2 por resposta
        if not isinstance(d, dict):
            continue
        tipo = str(d.get("tipo", "")).strip().lower()
        fn = _RENDERERS.get(tipo)
        if fn is None:
            logger.info("Diretiva de gráfico ignorada (tipo desconhecido): %s", tipo)
            continue
        try:
            if fn(d, meta):
                desenhados += 1
        except Exception as exc:
            logger.warning("Falha ao desenhar gráfico '%s': %s", tipo, exc)
            st.caption(f"⚠️ Não foi possível desenhar o gráfico '{tipo}'.")
    return desenhados


def infer_financas_chart_directives(question: str, meta: dict | None = None) -> list[dict]:
    """
    Fallback: se o usuário claramente pediu um gráfico e a LLM não emitiu diretiva,
    infere um tipo plausível pela intenção do texto. Nunca inventa dados.
    """
    q = (question or "").lower()
    pediu_grafico = any(t in q for t in ("gráfico", "grafico", "visualiz", "plot", "chart",
                                         "mostre", "desenh"))
    if not pediu_grafico:
        return []
    if any(t in q for t in ("projeç", "projec", "próximos", "proximos", "futuro")):
        return [{"tipo": "projecao_saldo", "meses": 6}]
    if any(t in q for t in ("essenc", "não essenc", "nao essenc")):
        return [{"tipo": "essencial_vs_nao_essencial"}]
    if any(t in q for t in ("corte", "reduç", "reduc", "simul")):
        return [{"tipo": "simulacao_corte", "percentual": 15}]
    if any(t in q for t in ("ano a ano", "anual", "por ano", "compare os anos")):
        return [{"tipo": "comparativo_anual"}]
    if any(t in q for t in ("fluxo", "mês a mês", "mes a mes", "mensal", "meses")):
        return [{"tipo": "fluxo_mensal"}]
    if any(t in q for t in ("patrimôn", "patrimon", "investido acumulado")):
        return [{"tipo": "evolucao_patrimonio"}]
    # padrão: categorias
    return [{"tipo": "despesas_categoria", "escopo": "mes"}]


def infer_cartao_chart_directives(question: str, meta: dict | None = None) -> list[dict]:
    """Fallback de gráficos para o chat da aba Cartão de Crédito."""
    q = (question or "").lower()
    pediu_grafico = any(t in q for t in ("gráfico", "grafico", "visualiz", "plot", "chart",
                                         "mostre", "desenh"))
    if not pediu_grafico:
        return []
    if any(t in q for t in ("assinatura", "recorrent", "streaming", "mensalidade")):
        return [{"tipo": "cartao_assinaturas"}]
    if any(t in q for t in ("estabelecim", "loja", "comércio", "comercio", "onde gast")):
        return [{"tipo": "cartao_estabelecimentos"}]
    if any(t in q for t in ("projeç", "projec", "parcela", "futur", "próximos", "proximos")):
        return [{"tipo": "cartao_projecao"}]
    if any(t in q for t in ("essenc", "não essenc", "nao essenc")):
        return [{"tipo": "essencial_vs_nao_essencial"}]
    if any(t in q for t in ("corte", "reduç", "reduc", "simul")):
        return [{"tipo": "simulacao_corte", "percentual": 15}]
    if any(t in q for t in ("evolu", "mês a mês", "mes a mes", "mensal", "meses")):
        return [{"tipo": "cartao_evolucao"}]
    # padrão: categorias do cartão
    return [{"tipo": "despesas_categoria", "escopo": "mes"}]

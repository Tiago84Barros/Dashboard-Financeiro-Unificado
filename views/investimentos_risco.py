"""Bloco "Risco da carteira" da aba Histórico (INV-A4).

Só interface: a série e as métricas vêm de ``core.carteira_risco``. Módulo à
parte para não importar de ``views.investimentos`` (import circular) e para
não engordar um arquivo de 4.600 linhas com mais um bloco.
"""
from __future__ import annotations

import html as _html

import plotly.graph_objects as go
import streamlit as st

from core.carteira_risco import BENCHMARK, get_risco_carteira
from core.utils import fmt_percentual

# Cores literais no Plotly (o adaptador de tema troca as do tema escuro); no
# HTML, só tokens — o tema claro só alcança o que passa por token.
_COR_LINHA = "#4A9EFF"
_COR_QUEDA = "#FC5C7D"
_COR_NEUTRO = "#9CA3AF"


def _pct(v: float | None, casas: int = 1, sinal: bool = False) -> str:
    if v is None:
        return "—"
    return fmt_percentual(v * 100, casas, sinal)


def _num(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}".replace(".", ",")


def _card(titulo: str, valor: str, sub: str, cor: str = "var(--app-text)") -> str:
    return (
        f'<div style="background:var(--app-surface);border:1px solid var(--app-border);'
        f'border-radius:10px;padding:14px 14px 11px;height:100%;">'
        f'<div style="font-size:0.58rem;font-weight:800;text-transform:uppercase;'
        f'letter-spacing:0.12em;color:var(--app-subtle);margin-bottom:6px;">{titulo}</div>'
        f'<div style="font-size:1.35rem;font-weight:800;color:{cor};'
        f'letter-spacing:-0.02em;line-height:1.1;margin-bottom:5px;">{valor}</div>'
        f'<div style="font-size:0.68rem;color:var(--app-subtle);line-height:1.3;">{sub}</div>'
        f'</div>'
    )


def _faixa(cor: str, texto: str) -> str:
    return (
        f'<div style="border-left:3px solid {cor};background:var(--app-surface);'
        f'padding:9px 13px;border-radius:0 8px 8px 0;margin:6px 0 10px;'
        f'font-size:0.80rem;color:var(--app-text);line-height:1.45;">{texto}</div>'
    )


_COR_ALERTA = {
    "atual_excede": "var(--app-danger)",
    "excedido": "var(--app-warning)",
    "dentro": "var(--app-primary)",
}


def _grafico(serie: list[dict]) -> go.Figure:
    datas = [s["data"] for s in serie]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=datas, y=[(s["indice"] - 1.0) * 100 for s in serie], name="TWR acumulado",
        mode="lines", line={"color": _COR_LINHA, "width": 2},
        hovertemplate="%{x|%d/%m/%Y}<br>TWR %{y:.2f}%<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=datas, y=[-s["drawdown"] * 100 for s in serie], name="Queda desde o pico",
        mode="lines", fill="tozeroy", line={"color": _COR_QUEDA, "width": 1},
        fillcolor="rgba(252,92,125,0.18)",
        hovertemplate="%{x|%d/%m/%Y}<br>Queda %{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color=_COR_NEUTRO,
        margin={"t": 8, "b": 0, "l": 0, "r": 0},
        height=260,
        yaxis={"showgrid": True, "gridcolor": "#1E2533", "ticksuffix": "%"},
        xaxis={"showgrid": False},
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
    return fig


def render_risco_carteira() -> None:
    st.markdown("#### Risco da carteira")
    risco = get_risco_carteira()
    if not risco.get("disponivel"):
        st.info("Série diária da carteira indisponível: "
                + str(risco.get("motivo") or "sem motivo informado") + ".")
        return

    cob = risco.get("cobertura") or {}
    st.caption(
        f"Série **{risco['frequencia']}** de {risco['inicio']:%d/%m/%Y} a "
        f"{risco['fim']:%d/%m/%Y} ({risco['n_dias']} pregões) — o histórico diário de "
        f"cotações no banco começa em abril de 2026. Cobertura: "
        f"**{_pct(cob.get('pct_observado'))}** do patrimônio com cotação diária e "
        f"**{_pct(cob.get('pct_modelado'))}** modelado pelo CDI (Tesouro Selic); "
        f"{_pct(cob.get('pct_fora'))} fica fora da série e não entra no risco "
        f"(não vira retorno zero). Retorno ponderado no tempo: aportes e resgates "
        f"não contam como ganho."
    )

    alerta = risco.get("alerta") or {}
    cor_alerta = _COR_ALERTA.get(alerta.get("nivel"), "var(--app-info)")
    st.markdown(_faixa(cor_alerta, _html.escape(alerta.get("texto") or "")),
                unsafe_allow_html=True)

    cdi = risco.get("cdi_periodo")
    twr = risco.get("twr")
    cor_twr = ("var(--app-text)" if twr is None or cdi is None
               else "var(--app-primary)" if twr >= cdi else "var(--app-danger)")
    tol = risco.get("tolerancia_pct")
    sub_dd = (f"atual {_pct(risco.get('drawdown_atual'))} · tolerância "
              + ("não declarada" if tol is None else f"{tol:.0f}%"))
    var_1m = risco.get("var_1m")
    sub_var = ("1 mês: " + (_pct(var_1m, 2) if var_1m is not None else "amostra curta")
               + f" ({risco.get('n_janelas_1m', 0)} janelas sobrepostas)")
    sharpe = risco.get("sharpe")
    beta = risco.get("beta")
    cards = [
        _card("TWR no período", _pct(twr, 2, sinal=True),
              f"CDI no mesmo período {_pct(cdi, 2)}", cor_twr),
        _card("Volatilidade anual", _pct(risco.get("vol_anual")),
              "desvio diário × √252"),
        _card("Queda máxima", _pct(risco.get("max_drawdown")), sub_dd,
              cor_alerta if alerta.get("nivel") in ("excedido", "atual_excede")
              else "var(--app-text)"),
        _card("VaR 95% · 1 dia", _pct(risco.get("var_1d"), 2), sub_var),
        _card("Sharpe × CDI", _num(sharpe),
              "excesso diário sobre o CDI, anualizado"),
        _card(f"Beta × {BENCHMARK}", _num(beta),
              f"proxy do Ibovespa · {risco.get('n_beta', 0)} pregões"),
    ]
    for linha in (cards[:3], cards[3:]):
        cols = st.columns(3)
        for col, html_card in zip(cols, linha):
            col.markdown(html_card, unsafe_allow_html=True)
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    serie = risco.get("serie") or []
    if serie:
        st.plotly_chart(_grafico(serie), width="stretch",
                        config={"displayModeBar": False})

    with st.expander("O que entra e o que fica fora da série"):
        excluidos = risco.get("excluidos") or []
        if excluidos:
            st.markdown("**Fora da série** (patrimônio sem preço diário):")
            st.dataframe(
                [{"Ativo": e["ticker"], "Classe": e["classe"],
                  "Valor (R$)": round(e["valor"], 2), "Motivo": e["motivo"]}
                 for e in excluidos],
                hide_index=True, width="stretch",
            )
        incl = risco.get("incluidos") or []
        modelados = [i["ticker"] for i in incl if i["fonte"] != "cotação diária"]
        qtd = risco.get("quantidade") or {}
        notas = [
            f"{len(incl)} ativos na série"
            + (f"; {', '.join(modelados)} com preço modelado pelo CDI (PU de hoje "
               "descontado pelo CDI diário)" if modelados else "") + ".",
            f"Cobertura diária dentro dos ativos da série: mínima "
            f"{_pct(cob.get('diaria_min'))}, mediana {_pct(cob.get('diaria_mediana'))} "
            "(dia em que um ativo não tem cotação, ele sai daquele dia).",
            "Quantidade ao longo do tempo ancorada nas fotos de posição; "
            f"{qtd.get('conciliados', 0)} intervalos fecharam com os eventos e "
            f"{qtd.get('degraus', 0)} entraram como degrau na foto seguinte "
            + (f"({', '.join(qtd.get('com_degrau') or [])})" if qtd.get("com_degrau") else "")
            + " — degrau é fluxo, não ganho.",
            "Proventos entram como renda na data de pagamento (só os da B3, em "
            "reais: os ETFs americanos não têm dividendos registrados no banco, "
            "e o retorno deles fica sem essa renda).",
            "VaR e queda máxima são de uma série de meses: não estimam a cauda "
            "de uma crise que a amostra não viu.",
        ]
        if risco.get("saltos"):
            notas.append("Dias descartados por salto acima de 35%: "
                         + ", ".join(risco["saltos"]) + ".")
        st.markdown("\n".join(f"- {n}" for n in notas))

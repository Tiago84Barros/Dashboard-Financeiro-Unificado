"""Bloco "Risco da carteira" da aba Histórico (INV-A4).

Só interface: a série e as métricas vêm de ``core.carteira_risco``. Módulo à
parte para não importar de ``views.investimentos`` (import circular) e para
não engordar um arquivo de 4.600 linhas com mais um bloco.

Quem lê a tela não precisa saber o que é TWR, VaR, Sharpe ou beta: cada card
diz em palavras simples o que mede, e "Como ler estes números" explica cada
um com os valores da própria carteira. As fórmulas vão para o detalhe técnico.
"""
from __future__ import annotations

import html as _html

import plotly.graph_objects as go
import streamlit as st

from core.carteira_risco import BENCHMARK, get_risco_carteira
from core.utils import fmt_moeda, fmt_percentual
from design.lacunas import aviso_lacuna, detalhe_tecnico

# Cores literais no Plotly (o adaptador de tema troca as do tema escuro); no
# HTML, só tokens — o tema claro só alcança o que passa por token.
_COR_LINHA = "#4A9EFF"
_COR_QUEDA = "#FC5C7D"
_COR_NEUTRO = "#9CA3AF"


def _pct(v: float | None, casas: int = 1, sinal: bool = False) -> str:
    if v is None:
        return "—"
    return fmt_percentual(v * 100, casas, sinal)


def _num(v: float | None, casas: int = 2) -> str:
    return "—" if v is None else f"{v:.{casas}f}".replace(".", ",")


def _reais(v: float) -> str:
    # "$" solto no st.markdown abre fórmula LaTeX; escapado, sai literal.
    return fmt_moeda(v).replace("$", "\\$")


def _data(d) -> str:
    return "—" if d is None else f"{d:%d/%m/%Y}"


def _leitura_sharpe(s: float) -> str:
    # O Sharpe é a média do excesso DIÁRIO sobre o CDI; quem diz se o período
    # bateu o CDI é o acumulado (TWR). Com vaivém forte os dois discordam, então
    # a leitura fala da média dos dias e nunca diz "superou".
    if s < 0:
        return "abaixo do CDI na média dos dias"
    if s < 1:
        return "acima do CDI na média dos dias, mas pouco para o risco"
    return "acima do CDI na média dos dias, com folga para o risco"


def _leitura_beta(b: float) -> str:
    x = _num(abs(b) * 10, 1)
    if b < 0:
        return (f"tende a ir na direção contrária: se o Ibovespa subir 10%, a "
                f"carteira tende a cair cerca de {x}%")
    return (f"se o Ibovespa subir ou cair 10%, a carteira tende a subir ou cair "
            f"cerca de {x}%")


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


def cartoes(risco: dict) -> list[str]:
    """Os seis cards: nome simples antes da sigla, legenda que diz o que mede."""
    alerta = risco.get("alerta") or {}
    cor_alerta = _COR_ALERTA.get(alerta.get("nivel"), "var(--app-info)")
    cdi = risco.get("cdi_periodo")
    twr = risco.get("twr")
    cor_twr = ("var(--app-text)" if twr is None or cdi is None
               else "var(--app-primary)" if twr >= cdi else "var(--app-danger)")
    tol = risco.get("tolerancia_pct")
    sub_dd = (f"hoje {_pct(risco.get('drawdown_atual'))} abaixo do pico · tolerância "
              + ("não declarada" if tol is None else f"{tol:.0f}%"))
    var_1m = risco.get("var_1m")
    sub_var = ("só 1 dia em cada 20 perde mais · 1 mês: "
               + (_pct(var_1m, 2) if var_1m is not None else "—"))
    vol_b = risco.get("vol_benchmark")
    sub_vol = ("o quanto o valor sobe e desce"
               + (f" · {BENCHMARK} no período: {_pct(vol_b)}" if vol_b is not None else ""))
    sharpe = risco.get("sharpe")
    beta = risco.get("beta")
    return [
        _card("Rendimento · TWR", _pct(twr, 2, sinal=True),
              f"o CDI rendeu {_pct(cdi, 2)} no mesmo período", cor_twr),
        _card("Oscilação · volatilidade anual", _pct(risco.get("vol_anual")), sub_vol),
        _card("Queda máxima · drawdown", _pct(risco.get("max_drawdown")), sub_dd,
              cor_alerta if alerta.get("nivel") in ("excedido", "atual_excede")
              else "var(--app-text)"),
        _card("Perda em dia ruim · VaR 95%", _pct(risco.get("var_1d"), 2), sub_var),
        _card("Retorno por risco · Sharpe", _num(sharpe),
              "sem amostra suficiente" if sharpe is None else _leitura_sharpe(sharpe)),
        _card("Sensibilidade à bolsa · beta", _num(beta),
              "sem amostra suficiente" if beta is None
              else f"Ibovespa ±10% → carteira ±{_num(beta * 10, 1)}%, em média" if beta >= 0
              else f"Ibovespa +10% → carteira −{_num(-beta * 10, 1)}%, em média"),
    ]


def explicacoes(risco: dict) -> list[tuple[str, str]]:
    """Cada medida em linguagem de quem não é do mercado, com o valor dela.

    Markdown pronto para ``st.markdown``: todo "R$" sai escapado.
    """
    out: list[tuple[str, str]] = []

    twr, cdi = risco.get("twr"), risco.get("cdi_periodo")
    t = ("Quanto a carteira rendeu no período, como se você tivesse investido R\\$ 100 "
         "no primeiro dia e não tivesse colocado nem tirado mais nada. Por isso um "
         "aporte não aparece como lucro, nem um resgate como prejuízo. TWR é a sigla "
         "em inglês de *retorno ponderado no tempo*.")
    if twr is not None:
        t += (f" **Na sua carteira:** {_pct(twr, 2, sinal=True)}, ou seja, cada R\\$ 100 "
              f"viraram {_reais(100 * (1 + twr))}.")
        if cdi is not None:
            t += (" Os mesmos R\\$ 100 no CDI, a referência da renda fixa, teriam "
                  f"virado {_reais(100 * (1 + cdi))}.")
    out.append(("Rendimento (TWR)", t))

    vol, vol_b = risco.get("vol_anual"), risco.get("vol_benchmark")
    t = ("Mede o quanto o valor da carteira sobe e desce de um dia para o outro, "
         "convertido para a escala de um ano. Não diz se ela ganhou ou perdeu: diz o "
         "quanto ela balança no caminho. Quanto maior, mais solavancos. A renda fixa "
         "pós-fixada, como o CDI, fica perto de zero.")
    if vol is not None:
        t += (f" **Na sua carteira:** {_pct(vol)} ao ano. Como regra de bolso, em cerca "
              f"de 2 a cada 3 anos o resultado fica até {_pct(vol)} acima ou abaixo do "
              "rendimento médio.")
        if vol_b is not None:
            t += (f" Para comparar: nos mesmos dias, o {BENCHMARK}, fundo que copia o "
                  f"Ibovespa, oscilou {_pct(vol_b)}.")
    out.append(("Oscilação (volatilidade anual)", t))

    dd, atual = risco.get("max_drawdown"), risco.get("drawdown_atual")
    tol = risco.get("tolerancia_pct")
    t = ("O maior tombo do período: a distância entre o ponto mais alto que a carteira "
         "atingiu e o ponto mais baixo que veio depois. Responde à pergunta \"qual foi "
         "o pior susto?\". Em inglês, *drawdown*.")
    if dd:
        t += (f" **Na sua carteira:** {_pct(dd)}, entre {_data(risco.get('pico_data'))} "
              f"e {_data(risco.get('vale_data'))}. Quem tinha R\\$ 100 no pico chegou a "
              f"ter {_reais(100 * (1 - dd))}.")
    elif dd is not None:
        t += " **Na sua carteira:** ela nunca ficou abaixo do maior valor que já tinha."
    if atual is not None:
        t += (" Hoje ela está no maior valor que já teve." if atual < 0.0005
              else f" Hoje ela está {_pct(atual)} abaixo do maior valor que já teve.")
    if tol is None:
        t += (" Você ainda não declarou qual queda aceitaria (Inteligência dos Ativos → "
              "estratégia). Com esse número, o app avisa quando ele for ultrapassado.")
    else:
        t += f" A queda que você declarou aceitar é de {tol:.0f}%."
    out.append(("Queda máxima (drawdown)", t))

    v1, vm = risco.get("var_1d"), risco.get("var_1m")
    t = ("Responde a \"num dia ruim, quanto posso perder?\". VaR é a sigla em inglês "
         "de *valor em risco*. O app olha todos os dias da carteira no período: em 19 "
         "de cada 20 dias a perda ficou abaixo deste número, e em 1 a cada 20 passou "
         "dele. Não é a perda máxima: nos piores dias a queda pode ser bem maior.")
    if v1 is not None:
        t += (f" **Na sua carteira:** {_pct(v1, 2)} em um dia, ou {_reais(100 * v1)} a "
              "cada R\\$ 100.")
        if vm is not None:
            t += (" Olhando períodos de um mês (21 pregões), o mesmo corte dá "
                  f"{_pct(vm, 2)}.")
    out.append(("Perda em dia ruim (VaR 95%)", t))

    s = risco.get("sharpe")
    t = ("Diz se o risco valeu a pena. Compara quanto a carteira rendeu por dia, em "
         "média, acima do CDI com o quanto ela balançou para chegar lá. Pela régua "
         "usual do mercado: abaixo de 0, os dias renderam em média menos que o CDI, "
         "ou seja, você correu risco e ganhou menos do que ganharia sem correr; entre "
         "0 e 1, renderam acima do CDI, mas pouco para o sobe e desce; acima de 1, o "
         "risco foi bem pago. O nome vem do economista William Sharpe.")
    if s is not None:
        t += f" **Na sua carteira:** {_num(s)}, ou seja, {_leitura_sharpe(s)}."
        if twr is not None and cdi is not None and (s >= 0) != (twr >= cdi):
            t += (" Repare que no acumulado do período a carteira rendeu "
                  f"{_pct(twr, 2, sinal=True)}, {'mais' if twr >= cdi else 'menos'} que "
                  f"o CDI ({_pct(cdi, 2)}). A média dos dias e o acumulado discordam "
                  "quando a carteira balança muito: cair 2% e depois subir 2% não "
                  "devolve tudo (R\\$ 100 viram R\\$ 98 e depois R\\$ 99,96). Para saber "
                  "se o período bateu o CDI, vale o card Rendimento.")
    out.append(("Retorno por risco (índice de Sharpe)", t))

    b = risco.get("beta")
    t = ("Mede o quanto a carteira acompanha a bolsa brasileira. A régua é o "
         f"{BENCHMARK}, um fundo negociado em bolsa que copia o Ibovespa. Beta 1: a "
         "carteira sobe e cai na mesma proporção da bolsa. Beta 0: não liga para a "
         "bolsa, como a renda fixa. Entre 0 e 1: acompanha só em parte. Acima de 1: "
         "exagera os movimentos da bolsa.")
    if b is not None:
        t += (f" **Na sua carteira:** {_num(b)}, ou seja, {_leitura_beta(b)}, em média. Num "
              "dia específico ela pode se mover bem diferente.")
    out.append(("Sensibilidade à bolsa (beta)", t))

    out.append(("O gráfico", (
        "A linha **TWR acumulado** mostra quanto R\\$ 100 investidos no início teriam "
        "rendido até cada dia; 0% é o ponto de partida. A área **Queda desde o pico** "
        "mostra, em cada dia, quanto a carteira estava abaixo do maior valor que já "
        "tinha alcançado. Quando a área encosta no zero, a carteira está no seu recorde.")))
    return out


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
        aviso_lacuna("Série diária da carteira indisponível: "
                     + str(risco.get("motivo") or "sem motivo informado") + ".",
                     codigo="tela.investimentos.risco_indisponivel")
        st.info("Esta análise não está disponível no momento.")
        return

    cob = risco.get("cobertura") or {}
    detalhe_tecnico(
        f"Série {risco['frequencia']} de {risco['inicio']:%d/%m/%Y} a "
        f"{risco['fim']:%d/%m/%Y} ({risco['n_dias']} pregões); o histórico diário de "
        f"cotações no banco começa em abril de 2026. Cobertura: "
        f"{_pct(cob.get('pct_observado'))} do patrimônio com cotação diária e "
        f"{_pct(cob.get('pct_modelado'))} modelado pelo CDI (Tesouro Selic); "
        f"{_pct(cob.get('pct_fora'))} fica fora da série e não entra no risco "
        "(não vira retorno zero).",
        codigo="investimentos.risco.serie_cobertura",
    )
    if (cob.get("pct_fora") or 0) > 0:
        aviso_lacuna("Parte do patrimônio fica fora da série de risco por falta de "
                     "preço diário.", codigo="tela.investimentos.risco_cobertura_parcial")
    st.caption("Os números medem só o desempenho dos investimentos: o dinheiro que "
               "você colocou (aporte) ou tirou (resgate) não conta como ganho nem "
               "como perda.")

    alerta = risco.get("alerta") or {}
    cor_alerta = _COR_ALERTA.get(alerta.get("nivel"), "var(--app-info)")
    st.markdown(_faixa(cor_alerta, _html.escape(alerta.get("texto") or "")),
                unsafe_allow_html=True)

    if risco.get("var_1m") is None:
        aviso_lacuna("VaR de 1 mês sem amostra suficiente.",
                     codigo="tela.investimentos.risco_var_amostra_curta")
    detalhe_tecnico(f"VaR 1 mês: {risco.get('n_janelas_1m', 0)} janelas sobrepostas; "
                    f"beta: {risco.get('n_beta', 0)} pregões; volatilidade do {BENCHMARK}: "
                    f"{risco.get('n_vol_benchmark', 0)} pregões em comum com a carteira.",
                    codigo="investimentos.risco.amostras")
    detalhe_tecnico("Fórmulas: TWR é o produto de (1 + retorno diário); volatilidade "
                    "é o desvio-padrão diário × √252; VaR 95% é a estatística de ordem "
                    "dos retornos diários, sem interpolar (1 mês: janelas móveis de 21 "
                    "pregões); Sharpe é a média do excesso diário sobre o CDI ÷ o "
                    f"desvio-padrão desse excesso × √252; beta é a covariância com o "
                    f"{BENCHMARK} ÷ a variância do {BENCHMARK}, proxy do Ibovespa.",
                    codigo="investimentos.risco.formulas")
    cards = cartoes(risco)
    for linha in (cards[:3], cards[3:]):
        cols = st.columns(3)
        for col, html_card in zip(cols, linha):
            col.markdown(html_card, unsafe_allow_html=True)
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

    with st.expander("📖 Como ler estes números, com os valores da sua carteira"):
        for titulo, texto in explicacoes(risco):
            st.markdown(f"**{titulo}**  \n{texto}")

    serie = risco.get("serie") or []
    if serie:
        st.plotly_chart(_grafico(serie), width="stretch",
                        config={"displayModeBar": False})

    for e in risco.get("excluidos") or []:
        aviso_lacuna(f"{e['ticker']} ({e['classe']}) fora da série de risco: "
                     f"{e['motivo']}.", codigo="tela.investimentos.risco_ativo_fora_da_serie",
                     entidade=e["ticker"])
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
    for n in notas:
        detalhe_tecnico(n, codigo="investimentos.risco.o_que_entra")

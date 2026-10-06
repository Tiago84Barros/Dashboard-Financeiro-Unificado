"""
design/tesouro_painel.py — a sub-aba "Tesouro" como resposta, não como planilha.

A tela antiga empilhava oito KPIs e dois cards por título em duas linguagens
visuais, e quem lia terminava sem resposta para a única pergunta que importa:
*dá para vender, e vale?* Este módulo monta a resposta em faixas, na ordem em
que ela se lê:

1. **o que a marcação vale hoje** — em reais líquidos, não em porcentagem solta;
2. **o que cada título entrega** — carregar até o fim × vender hoje, no gráfico;
3. **o veredito por título** — com a taxa de indiferença, que é a única forma
   honesta de dizer "de quanto é a vantagem" sem escolher pelo usuário;
4. **um gráfico por título** — para onde o papel vai se for carregado até
   o fim, e quanto a marcação já oscilou nele.

Toda função é pura e devolve HTML ou uma figura: o que a tela promete se
verifica sem subir o Streamlit e sem tocar o banco. Cor sempre por token
``var(--app-*)`` — a paleta clara redefine os mesmos nomes e a tela acompanha
os dois temas sozinha. As figuras saem na paleta escura de propósito:
``design/tema_canvas.clarear_figura`` converte a moldura quando o tema é claro,
e passar tema explícito aqui desligaria esse adaptador.
"""
from __future__ import annotations

import html
from datetime import date

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.tesouro_venda import (
    AGIO,
    DESAGIO,
    NEUTRA,
    SEM_PRECO,
    LeituraVenda,
    rotulo_taxa,
)

# A moldura (fundo, grade, eixo, fonte) é convertida pelo adaptador de tema.
# Cor de série, não: foi medido que `design.tema_canvas.clarear_figura` devolve
# `marker.color` e `line.color` intactos. Então a paleta das figuras é escolhida
# para servir aos DOIS temas — contraste de pelo menos 3:1 tanto sobre o fundo
# escuro (#0E1117) quanto sobre o branco. O amarelo da paleta do app (#F6C90E,
# 1,58:1 no claro) e o cinza tênue de antes não passavam nesse critério.
_GRAFICO_NEUTRO = "#9CA3AF"
_GRAFICO_GRADE = "#1E2533"
_GRAFICO_LINHA_ZERO = "#7C879B"

#: As três barras de "levar até o fim × vender hoje".
_FIG_INVESTIDO = "#8792A6"
_FIG_VENDER = "#3B82F6"
_FIG_VENCIMENTO = "#0D9488"

#: A linha de referência do gráfico por título: o que a posição valeria pela
#: taxa contratada, sem oscilação de mercado.
_FIG_CURVA = "#8792A6"

_TOM = {
    AGIO: "var(--app-primary, #00C896)",
    DESAGIO: "var(--app-danger, #FC5C7D)",
    NEUTRA: "var(--app-info, #4A9EFF)",
    SEM_PRECO: "var(--app-subtle, #6B7280)",
}

_ICONE = {AGIO: "▲", DESAGIO: "▼", NEUTRA: "=", SEM_PRECO: "?"}

TD_CSS = """
<style>
.td-estado { display: flex; flex-wrap: wrap; align-items: center; gap: 7px;
    margin: 2px 0 10px; }
.td-chip {
    --td-tom: var(--app-muted, #8A99AE);
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 10px;
    border: 1px solid color-mix(in srgb, var(--td-tom) 30%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--td-tom) 10%, transparent);
    color: var(--app-text, #E2E8F0);
    font-size: .66rem; font-weight: 700; white-space: nowrap;
}
.td-chip b { color: var(--td-tom); font-weight: 820; }
.td-hero {
    --td-tom: var(--app-primary, #00C896);
    margin: 0 0 14px; padding: 20px 24px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-left: 3px solid var(--td-tom);
    border-radius: 14px;
    background: linear-gradient(135deg,
        color-mix(in srgb, var(--td-tom) 10%, transparent),
        var(--app-surface-raised, #171D2B) 58%);
}
.td-hero-rotulo {
    color: var(--app-muted, #8A99AE);
    font-size: .64rem; font-weight: 700; letter-spacing: .09em;
    text-transform: uppercase;
}
.td-hero-valor {
    margin: 4px 0 2px;
    color: var(--td-tom);
    font-size: 2.1rem; font-weight: 860; line-height: 1.05;
    font-variant-numeric: tabular-nums;
}
.td-hero-frase {
    color: var(--app-text, #E2E8F0);
    font-size: .82rem; line-height: 1.5; max-width: 72ch;
}
.td-faixa {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
    gap: 10px; margin: 16px 0 0;
}
.td-kpi {
    padding: 12px 14px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-radius: 11px;
    background: var(--app-surface, #121725);
}
.td-kpi-rotulo {
    color: var(--app-muted, #8A99AE);
    font-size: .6rem; font-weight: 700; letter-spacing: .07em;
    text-transform: uppercase;
}
.td-kpi-valor {
    margin-top: 3px;
    color: var(--app-text, #E2E8F0);
    font-size: 1.15rem; font-weight: 820;
    font-variant-numeric: tabular-nums;
}
.td-kpi-nota {
    margin-top: 2px;
    color: var(--app-subtle, #6B7280);
    font-size: .66rem; line-height: 1.35;
}
.td-cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(330px, 1fr));
    gap: 12px; margin: 6px 0 2px;
}
.td-card {
    --td-tom: var(--app-info, #4A9EFF);
    display: flex; flex-direction: column; gap: 9px;
    padding: 15px 17px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-top: 2px solid var(--td-tom);
    border-radius: 13px;
    background: var(--app-surface-raised, #171D2B);
}
.td-card-topo {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 10px;
}
.td-card-nome {
    color: var(--app-text, #E2E8F0);
    font-size: .95rem; font-weight: 800; line-height: 1.25;
}
.td-card-venc {
    color: var(--app-subtle, #6B7280);
    font-size: .64rem; font-weight: 700; white-space: nowrap;
}
.td-selo {
    display: inline-flex; align-items: center; gap: 6px;
    align-self: flex-start; padding: 3px 10px;
    border: 1px solid color-mix(in srgb, var(--td-tom) 38%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--td-tom) 13%, transparent);
    color: var(--td-tom);
    font-size: .66rem; font-weight: 800;
}
.td-grade {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px 14px;
    padding: 9px 0 1px;
    border-top: 1px solid var(--app-border, rgba(148,163,184,.14));
}
.td-celula-rotulo {
    color: var(--app-muted, #8A99AE);
    font-size: .58rem; font-weight: 700; letter-spacing: .06em;
    text-transform: uppercase;
}
.td-celula-valor {
    color: var(--app-text, #E2E8F0);
    font-size: .92rem; font-weight: 800;
    font-variant-numeric: tabular-nums;
}
.td-celula-nota {
    color: var(--app-subtle, #6B7280);
    font-size: .62rem; line-height: 1.3;
}
.td-regra {
    padding: 9px 12px;
    border-left: 2px solid var(--td-tom);
    border-radius: 0 8px 8px 0;
    background: color-mix(in srgb, var(--td-tom) 8%, transparent);
    color: var(--app-text, #E2E8F0);
    font-size: .74rem; line-height: 1.45;
}
.td-regra b { color: var(--td-tom); font-weight: 840; }
.td-frase {
    color: var(--app-muted, #8A99AE);
    font-size: .72rem; line-height: 1.5;
}
.td-legenda {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 9px 16px; margin: 4px 0 2px;
}
.td-legenda-item {
    display: flex; align-items: flex-start; gap: 8px;
    color: var(--app-muted, #8A99AE);
    font-size: .72rem; line-height: 1.45;
}
.td-legenda-marca {
    flex: 0 0 auto; margin-top: 1px;
    font-size: .74rem; font-weight: 820;
}
.td-legenda-item b { color: var(--app-text, #E2E8F0); font-weight: 800; }

/* Nome do título acima do gráfico dele. Com um gráfico por papel, sem esta
   etiqueta a sequência vira uma pilha de curvas sem dono. */
.td-grafico-nome {
    margin: 14px 0 -8px; padding-left: 2px;
    color: var(--app-text, #E2E8F0);
    font-size: .92rem; font-weight: 800; letter-spacing: .01em;
}
.td-grafico-prazo {
    color: var(--app-muted, #8A99AE);
    font-size: .78rem; font-weight: 600;
}
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Formatação
# ─────────────────────────────────────────────────────────────────────────────

def _reais(valor: float | None, *, sinal: bool = False) -> str:
    if valor is None:
        return "—"
    corpo = f"{abs(valor):,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")
    if sinal:
        marca = "+" if valor >= 0 else "−"
        return f"{marca} R$ {corpo}"
    return f"{'−' if valor < 0 else ''}R$ {corpo}"


def _pct(valor: float | None, *, casas: int = 2, sinal: bool = True) -> str:
    """Recebe fração (0,0135), devolve "+1,35%"."""
    if valor is None:
        return "—"
    texto = f"{valor * 100:{'+' if sinal else ''}.{casas}f}%"
    return texto.replace(".", ",").replace("+-", "−").replace("-", "−")


def _esc(texto) -> str:
    return html.escape(str(texto or ""))


# ─────────────────────────────────────────────────────────────────────────────
# Faixa 1 — procedência e resposta direta
# ─────────────────────────────────────────────────────────────────────────────

def estado_html(*, data_curva: date | None, data_extrato: date | None,
                hoje: date, dias_curva_velha: int = 5) -> str:
    """Chips de procedência: de que dia é o preço e de que dia é a posição.

    As duas datas são diferentes e a diferença importa: a posição vem do
    Extrato Analítico que o usuário importou, o preço vem da curva do Tesouro.
    Número de mercado com cara de preço de hoje sobre extrato de um mês atrás
    foi exatamente o defeito que o campo ``fonte_preco`` existe para impedir.
    """
    chips = []
    if data_curva is not None:
        idade = (hoje - data_curva).days
        velha = idade > dias_curva_velha
        tom = "var(--app-warning, #F6C90E)" if velha else "var(--app-primary, #00C896)"
        extra = f" · {idade} dia{'s' if idade != 1 else ''} atrás" if idade > 0 else " · hoje"
        chips.append(
            f'<span class="td-chip" style="--td-tom: {tom};">'
            f'Preço de mercado <b>{data_curva:%d/%m/%Y}</b>{extra}</span>')
        if velha:
            chips.append(
                '<span class="td-chip" style="--td-tom: var(--app-warning, #F6C90E);">'
                '<b>Curva desatualizada</b> — o ágio é o do último dia coletado'
                '</span>')
    else:
        chips.append(
            '<span class="td-chip" style="--td-tom: var(--app-subtle, #6B7280);">'
            '<b>Sem preço de mercado</b> — valor do extrato</span>')
    if data_extrato is not None:
        chips.append(
            '<span class="td-chip" style="--td-tom: var(--app-info, #4A9EFF);">'
            f'Posição do extrato <b>{data_extrato:%d/%m/%Y}</b></span>')
    return f'<div class="td-estado">{"".join(chips)}</div>'


def hero_html(resumo: dict, *, frase: str) -> str:
    """O que a marcação vale hoje, em reais líquidos, e o que isso significa.

    Em reais e **líquido** porque é o que decide: um ágio de 1,2% sobre a maior
    posição da carteira soa grande e virou R$ 293 depois do imposto. A
    porcentagem fica ao lado, como escala, não como manchete.
    """
    liquido = resumo.get("ganho_mtm_liquido")
    tom = ("var(--app-primary, #00C896)" if (liquido or 0) > 0
           else "var(--app-danger, #FC5C7D)" if (liquido or 0) < 0
           else "var(--app-info, #4A9EFF)")
    if liquido is None:
        tom = "var(--app-subtle, #6B7280)"

    contagem = []
    for chave, rotulo in (("com_agio", "com ágio"), ("com_desagio", "com deságio"),
                          ("neutros", "sem efeito prático"),
                          ("sem_preco", "sem preço")):
        quantos = resumo.get(chave) or 0
        if quantos:
            contagem.append(f"{quantos} {rotulo}")
    linha_contagem = " · ".join(contagem)

    partes = [
        '<div class="td-hero" style="--td-tom: %s;">' % tom,
        '<div class="td-hero-rotulo">O que a marcação a mercado vale hoje,'
        ' depois do imposto</div>',
        f'<div class="td-hero-valor">{_reais(liquido, sinal=liquido is not None)}</div>',
        f'<div class="td-hero-frase">{_esc(frase)}</div>',
    ]
    if contagem:
        partes.insert(3, '<div class="td-kpi-nota" style="margin:0 0 8px;">'
                         f'{_pct(resumo.get("mtm_pct"))} da posição marcada · '
                         f'{_esc(linha_contagem)}</div>')
    partes.append(_faixa_kpis_html(resumo))
    partes.append("</div>")
    return "".join(partes)


def _faixa_kpis_html(resumo: dict) -> str:
    venc = resumo.get("liquido_vencimento")
    nota_venc = ("projeção parcial: algum título ficou sem preço"
                 if resumo.get("projecao_parcial")
                 else "pela taxa de recompra de hoje até o vencimento")
    if resumo.get("depende_do_indice"):
        nota_venc += "; indexado projetado pelo índice implícito do cardápio"
    celulas = [
        ("Se vender tudo hoje", _reais(resumo.get("liquido_hoje")),
         "líquido de IR e IOF, pelo preço de recompra"),
        ("Se carregar até o vencimento", _reais(venc), nota_venc),
        ("Ágio bruto de marcação", _reais(resumo.get("ganho_mtm"), sinal=True),
         "antes do imposto sobre o ganho"),
        ("Imposto antecipado se vender", _reais(resumo.get("imposto_antecipado")),
         "IR que carregar deixaria para o fim"),
    ]
    blocos = "".join(
        f'<div class="td-kpi"><div class="td-kpi-rotulo">{_esc(r)}</div>'
        f'<div class="td-kpi-valor">{v}</div>'
        f'<div class="td-kpi-nota">{_esc(n)}</div></div>'
        for r, v, n in celulas)
    return f'<div class="td-faixa">{blocos}</div>'


# ─────────────────────────────────────────────────────────────────────────────
# Faixa 3 — veredito por título
# ─────────────────────────────────────────────────────────────────────────────

def card_html(leitura: LeituraVenda, extremos: dict | None = None) -> str:
    """Um título: pode vender, quanto rende a marcação e acima de quanto vale.

    A linha da **taxa de indiferença** é o centro do card. Ela responde "de
    quanto é a vantagem" de forma verificável — acima dela trocar ganha, abaixo
    perde — sem fingir uma recomendação que depende de onde o dinheiro vai
    parar. O veredito em reais existe na mesma tela, mas só dentro do expander,
    porque lá ele tem a alternativa escolhida pelo usuário.
    """
    tom = _TOM[leitura.situacao]
    venc = (f"vence em {leitura.vencimento:%d/%m/%Y}"
            if leitura.vencimento else "sem vencimento no extrato")

    celulas = [
        ("Se vender hoje", _reais(leitura.liquido_hoje), "líquido de IR e IOF"),
        ("Se carregar até o fim", _reais(leitura.liquido_vencimento),
         f"{leitura.du_restante} dias úteis restantes"
         if leitura.du_restante else "sem prazo restante"),
    ]
    if leitura.pode_marcar:
        positivo = (leitura.ganho_mtm_reais or 0.0) > 0
        celulas.append((
            "Ágio de marcação" if positivo else "Deságio de marcação",
            f"{_reais(leitura.ganho_mtm_reais, sinal=True)} "
            f"({_pct(leitura.mtm_pct)})",
            f"{_reais(leitura.ganho_mtm_liquido, sinal=True)} depois do IR"
            if positivo else
            "realizar o deságio não gera crédito de imposto"))
        celulas.append((
            "Imposto antecipado", _reais(leitura.imposto_antecipado),
            "pago agora em vez de no vencimento"))

    grade = "".join(
        f'<div><div class="td-celula-rotulo">{_esc(r)}</div>'
        f'<div class="td-celula-valor">{v}</div>'
        f'<div class="td-celula-nota">{_esc(n)}</div></div>'
        for r, v, n in celulas)

    partes = [
        f'<article class="td-card" style="--td-tom: {tom};">',
        '<div class="td-card-topo">'
        f'<span class="td-card-nome">{_esc(leitura.titulo)}</span>'
        f'<span class="td-card-venc">{_esc(venc)}</span></div>',
        f'<span class="td-selo">{_ICONE[leitura.situacao]} '
        f'{_esc(leitura.rotulo)}</span>',
        f'<div class="td-grade">{grade}</div>',
    ]

    if leitura.taxa_indiferenca is not None:
        alvo = rotulo_taxa(leitura.indexador, leitura.taxa_indiferenca)
        hoje = rotulo_taxa(leitura.indexador, leitura.taxa_mercado)
        custo = leitura.custo_anual_do_ir
        complemento = ""
        if custo is not None:
            complemento = (" O mesmo papel está sendo ofertado a "
                           f"{hoje}: a distância de {_pct(custo)} é o preço "
                           "anual de antecipar o imposto.")
        partes.append(
            f'<div class="td-regra">Vender só compensa se o dinheiro for para '
            f'algo que renda <b>mais de {_esc(alvo)}</b> até '
            f'{leitura.vencimento:%m/%Y}.{_esc(complemento)}</div>')

    if extremos:
        partes.append(
            '<div class="td-frase">Nesta posição a marcação já foi de '
            f'{_pct(extremos["minimo"])} ({extremos["data_minimo"]:%m/%Y}) a '
            f'{_pct(extremos["maximo"])} ({extremos["data_maximo"]:%m/%Y}) — '
            f'{extremos["pontos"]} dias medidos desde '
            f'{extremos["inicio"]:%m/%Y}.</div>')

    partes.append(f'<div class="td-frase">{_esc(leitura.frase)}</div>')
    partes.append("</article>")
    return "".join(partes)


def cards_html(pares) -> str:
    """Grade CSS com um card por título.

    Grade, e não ``st.columns``: coluna empilha na própria altura e a fileira
    sai desencontrada quando um título tem série histórica e o vizinho não.
    """
    corpo = "".join(card_html(leitura, extremos) for leitura, extremos in pares)
    return f'<div class="td-cards">{corpo}</div>'


def legenda_html() -> str:
    """Como ler os selos — e o que a tela não promete."""
    itens = [
        (_ICONE[AGIO], _TOM[AGIO], "Vendável com ágio",
         "a taxa de mercado caiu abaixo da contratada; o preço de recompra "
         "está acima da curva do lote."),
        (_ICONE[DESAGIO], _TOM[DESAGIO], "Venda realizaria deságio",
         "a taxa de mercado subiu; vender agora abre mão da taxa contratada."),
        (_ICONE[NEUTRA], _TOM[NEUTRA], "Marcação sem efeito prático",
         "o ágio está dentro da faixa de ruído de PU, calendário e spread."),
        (_ICONE[SEM_PRECO], _TOM[SEM_PRECO], "Sem preço de mercado",
         "a curva do dia não tem o título; o valor exibido é o do extrato."),
    ]
    corpo = "".join(
        f'<div class="td-legenda-item">'
        f'<span class="td-legenda-marca" style="color: {tom};">{marca}</span>'
        f'<span><b>{_esc(nome)}</b> — {_esc(texto)}</span></div>'
        for marca, tom, nome, texto in itens)
    return f'<div class="td-legenda">{corpo}</div>'


# ─────────────────────────────────────────────────────────────────────────────
# Figuras
# ─────────────────────────────────────────────────────────────────────────────

def _rotulo_curto(titulo: str) -> str:
    """"Tesouro IPCA+ 2032" → "IPCA+ 2032": o eixo não precisa repetir o emissor."""
    return str(titulo or "").replace("Tesouro ", "").strip() or "—"


def fig_carregar_vs_vender(leituras) -> go.Figure:
    """Barras horizontais: investido × líquido hoje × líquido no vencimento.

    Horizontal porque nome de título não cabe no eixo x sem girar o rótulo, e
    as três barras na mesma linha deixam ler de uma vez o que o pedido pede:
    quanto cada título entrega se for até o fim, e quanto entregaria se fosse
    vendido hoje.

    Título sem projeção entra com ``None`` na terceira barra — barra ausente é
    lacuna visível; barra em zero seria afirmação falsa.
    """
    ordenadas = sorted(leituras, key=lambda lv: lv.liquido_hoje or 0.0)
    nomes = [_rotulo_curto(lv.titulo) for lv in ordenadas]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="Investido", y=nomes, x=[lv.valor_investido for lv in ordenadas],
        orientation="h", marker_color=_FIG_INVESTIDO,
        hovertemplate="<b>%{y}</b><br>Investido: R$ %{x:,.2f}<extra></extra>"))
    fig.add_trace(go.Bar(
        name="Líquido se vender hoje", y=nomes,
        x=[lv.liquido_hoje for lv in ordenadas], orientation="h",
        marker_color=_FIG_VENDER,
        hovertemplate="<b>%{y}</b><br>Vendendo hoje: R$ %{x:,.2f}<extra></extra>"))
    fig.add_trace(go.Bar(
        name="Líquido no vencimento", y=nomes,
        x=[lv.liquido_vencimento for lv in ordenadas], orientation="h",
        marker_color=_FIG_VENCIMENTO,
        # O rótulo existe porque a escala é compartilhada: ao lado de uma
        # posição de R$ 93 mil, uma de R$ 2,4 mil vira traço e some. O número
        # impresso devolve a leitura ao título pequeno sem distorcer o eixo.
        text=[_reais(lv.liquido_vencimento) for lv in ordenadas],
        textposition="outside", cliponaxis=False,
        textfont={"size": 10, "color": _GRAFICO_NEUTRO},
        hovertemplate="<b>%{y}</b><br>No vencimento: R$ %{x:,.2f}<extra></extra>"))
    fig.update_layout(
        barmode="group", bargap=0.28,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_GRAFICO_NEUTRO,
        legend={"orientation": "h", "y": -0.14, "x": 0,
                "font": {"size": 11}, "bgcolor": "rgba(0,0,0,0)"},
        margin={"t": 10, "b": 10, "l": 0, "r": 90},
        height=max(240, 62 * len(nomes) + 90),
        xaxis={"showgrid": True, "gridcolor": _GRAFICO_GRADE,
               "tickformat": ",.0f", "tickprefix": "R$ "},
        yaxis={"showgrid": False, "automargin": True},
    )
    return fig


def rotulos_escolha(leituras: list[LeituraVenda]) -> list[str]:
    """Os nomes que a caixa de escolha do gráfico oferece, sem ambiguidade.

    O nome do papel basta na quase totalidade dos casos. Quando dois títulos
    da carteira repetem o nome, só ele deixaria quem escolhe no escuro — aí o
    vencimento entra para desempatar, e entra **só nos repetidos**: carregar
    a data em todos para resolver dois seria pagar no lugar errado.
    """
    nomes = [leitura.titulo for leitura in leituras]
    repetidos = {nome for nome in nomes if nomes.count(nome) > 1}
    saida = []
    for leitura in leituras:
        if leitura.titulo in repetidos and leitura.vencimento:
            saida.append(f"{leitura.titulo} · vence em "
                         f"{leitura.vencimento.strftime('%d/%m/%Y')}")
        else:
            saida.append(leitura.titulo)
    return saida


def nome_grafico_html(leitura: LeituraVenda) -> str:
    """A etiqueta do título acima do gráfico dele, com o vencimento ao lado.

    Vive aqui, e não na view, porque é apresentação: a view passou a desenhar
    um gráfico por papel e precisa dizer de quem é cada um sem montar HTML.
    """
    venc = leitura.vencimento
    prazo = f" · vence em {venc.strftime('%d/%m/%Y')}" if venc else ""
    return (f'<div class="td-grafico-nome">{_esc(leitura.titulo)}'
            f'<span class="td-grafico-prazo">{prazo}</span></div>')


def fig_titulo(leitura: LeituraVenda, serie: list[dict],
               trajetoria: list[dict]) -> go.Figure:
    """Um título, dois painéis: para onde ele vai, e quanto ele já oscilou.

    O gráfico consolidado de antes empilhava seis linhas de marcação num eixo
    só. Dava para ver que a carteira oscila; não dava para responder a pergunta
    de quem olha um papel específico — *quanto eu terei neste aqui, e o que a
    marcação já fez com ele*. Daí um gráfico por título, com as duas coisas no
    mesmo quadro.

    **Em cima, reais.** A linha cheia é o valor de mercado dia a dia; a
    tracejada ao lado dela é o que a posição valeria pela taxa contratada. A
    distância entre as duas *é* a marcação — ágio quando a cheia está por cima.
    Da data da curva em diante vem a trajetória de carregar até o fim, e o
    losango no vencimento marca o que sobra depois do imposto.

    **Embaixo, a mesma história em percentual**, com o zero tracejado. Está num
    painel próprio, e não no mesmo eixo, porque uma oscilação de 3% sobre uma
    linha que sobe some; e com eixo x próprio, porque um papel de 2034 tem oito
    anos de futuro contra um ano e meio de passado — compartilhar o eixo
    espremeria a oscilação inteira contra a margem esquerda.

    O futuro sai tracejado de propósito: ele é aritmética de carregar pela taxa
    de hoje, não observação. Em título indexado, nem isso — depende do índice
    que vier, e o rótulo do painel diz.
    """
    pontos = [p for p in serie if p.get("mtm_pct") is not None]
    com_oscilacao = len(pontos) > 1

    titulo_reais = "Quanto você terá, em reais"
    if leitura.depende_do_indice:
        titulo_reais += "  (o trecho futuro depende do índice projetado)"

    if com_oscilacao:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.19,
            row_heights=[0.62, 0.38],
            subplot_titles=(titulo_reais, "Oscilação da marcação"))
    else:
        fig = make_subplots(rows=1, cols=1, subplot_titles=(titulo_reais,))

    if pontos:
        fig.add_trace(go.Scatter(
            name="Valor de mercado", x=[p["data"] for p in pontos],
            y=[p["valor_bruto"] for p in pontos], mode="lines",
            line={"color": _FIG_VENDER, "width": 1.9},
            hovertemplate="%{x|%d/%m/%Y}<br>Mercado: R$ %{y:,.2f}<extra></extra>"),
            row=1, col=1)
        curva = [p for p in pontos if p.get("valor_curva")]
        if curva:
            fig.add_trace(go.Scatter(
                name="Pela taxa que você contratou",
                x=[p["data"] for p in curva],
                y=[p["valor_curva"] for p in curva], mode="lines",
                line={"color": _FIG_CURVA, "width": 1.4, "dash": "dot"},
                hovertemplate="%{x|%d/%m/%Y}<br>Pela taxa contratada: "
                              "R$ %{y:,.2f}<extra></extra>"),
                row=1, col=1)

    if trajetoria:
        fig.add_trace(go.Scatter(
            name="Levando até o vencimento",
            x=[p["data"] for p in trajetoria],
            y=[p["valor_bruto"] for p in trajetoria], mode="lines",
            line={"color": _FIG_VENCIMENTO, "width": 1.9, "dash": "dash"},
            hovertemplate="%{x|%d/%m/%Y}<br>Carregando: R$ %{y:,.2f}"
                          "<extra></extra>"),
            row=1, col=1)

    if leitura.liquido_vencimento is not None and leitura.vencimento:
        fig.add_trace(go.Scatter(
            name="Líquido no vencimento", x=[leitura.vencimento],
            y=[leitura.liquido_vencimento], mode="markers+text",
            marker={"color": _FIG_VENCIMENTO, "size": 10, "symbol": "diamond"},
            text=[_reais(leitura.liquido_vencimento)],
            textposition="middle left", cliponaxis=False,
            textfont={"size": 11, "color": _GRAFICO_NEUTRO},
            hovertemplate="No vencimento, já com IR: R$ %{y:,.2f}<extra></extra>"),
            row=1, col=1)

    if com_oscilacao:
        fig.add_trace(go.Scatter(
            name="Marcação", x=[p["data"] for p in pontos],
            y=[p["mtm_pct"] * 100 for p in pontos], mode="lines",
            line={"color": _FIG_VENDER, "width": 1.6}, showlegend=False,
            hovertemplate="%{x|%d/%m/%Y}<br>Marcação: %{y:+.2f}%<extra></extra>"),
            row=2, col=1)
        fig.add_hline(y=0, line={"color": _GRAFICO_LINHA_ZERO, "width": 1,
                                 "dash": "dot"}, row=2, col=1)

    # Os títulos de painel vêm centralizados do plotly; à esquerda e menores
    # eles se leem como rótulo da faixa, não como título do gráfico.
    for nota in fig.layout.annotations:
        nota.update(x=0, xanchor="left", font={"size": 11.5,
                                               "color": _GRAFICO_NEUTRO})

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color=_GRAFICO_NEUTRO, hovermode="x unified",
        legend={"orientation": "h", "y": -0.13, "x": 0, "font": {"size": 11},
                "bgcolor": "rgba(0,0,0,0)"},
        # A legenda é quem reserva a faixa onde os rótulos do eixo x caem; a
        # margem de baixo só completa o que falta.
        margin={"t": 26, "b": 10, "l": 0, "r": 20},
        height=400 if com_oscilacao else 260,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(showgrid=True, gridcolor=_GRAFICO_GRADE, automargin=True)
    fig.update_yaxes(tickformat=",.0f", tickprefix="R$ ", row=1, col=1)
    if com_oscilacao:
        fig.update_yaxes(ticksuffix="%", tickformat="+.1f", zeroline=False,
                         row=2, col=1)
    return fig

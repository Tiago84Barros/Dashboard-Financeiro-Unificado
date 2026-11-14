"""
views/inteligencia_ativos_resumida.py
Página resumida da Inteligência dos Ativos (30/09/2026).

Segue o rascunho do usuário: reserva de emergência e renda fixa com um card
curto por título; ações, FIIs e internacional com uma caixa por ativo. Dentro da caixa,
na ordem do rascunho: quanto tem e quanto deveria ter, a ação sugerida
(manter, comprar, acompanhar, rebalancear, reduzir parcialmente, vender ou
substituir) com justificativa, convicção, condições e limitações, os alertas
para investigar numa caixa à parte (não são ordens), o substituto se for
sair, dois pares numa tabela, o papel na carteira, notícias, relatórios e o
que do macro pesa. O detalhe completo
(13 etapas e Portfolio Fit) segue na aba, sob "Análise detalhada".

Linguagem (12/11/2026, pedido do usuário): a caixa conversa com o
investidor iniciante. Os cartões de cima e a conversa saem de
core/inteligencia_ativos/linguagem_simples.py; o texto técnico (justificativa,
alertas, condições, limitações e a avaliação por dimensão) continua inteiro,
num bloco recolhido "Ver a análise técnica completa" no fim da caixa.

Visual (12/11/2026, pedido do usuário: "estilo mais profissional"): os
cartões usam as classes ``ia-*`` de ``CSS``, injetado uma vez por
``render``, no lugar de estilo solto em cada ``<div>``. Uma escala só de
tipo (rótulo .68rem em caixa alta, corpo .9rem com entrelinha 1,6, número
em ``tabular-nums``), espaçamento em múltiplos de 4 px, texto secundário em
``--app-muted`` (o ``--app-subtle`` ficava abaixo do contraste legível em
letra pequena) e cor só onde ela carrega sentido: a ação, o tom de cada
leitura e o aviso. A fonte é a do app inteiro, para a tela não destoar.

Os cartões saem num st.markdown só e só usam tokens var(--app-*).
Dados: core/inteligencia_ativos/resumida.py (puro).
"""
from __future__ import annotations

from html import escape

import streamlit as st

from core.inteligencia_ativos import avaliacao as av_
from core.inteligencia_ativos import destaques_relatorios as dr
from core.inteligencia_ativos import linguagem_simples as ls
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import resumida as rs
from core.utils import fmt_moeda
from design.lacunas import aviso_lacuna, detalhe_tecnico

DETALHE_KEY = "ia_detalhe"   # liga a "Análise detalhada" abaixo
SELECAO_KEY = "ia_ativo"     # o mesmo seletor da análise detalhada

_ICONE = {rs.RESERVA: "🛟", rs.RENDA_FIXA: "🏦", rs.ACOES: "📈",
          rs.FIIS: "🏢", rs.EXTERIOR: "🌎", rs.OUTROS: "📦"}
_COR_DECISAO = {rs.COMPRAR: "var(--app-info)",
                rs.MANTER: "var(--app-muted)",
                rs.ACOMPANHAR: "var(--app-warning)",
                rs.REBALANCEAR: "var(--app-info)",
                rs.REDUZIR_PARCIAL: "var(--app-warning)",
                rs.VENDER: "var(--app-danger)",
                rs.SUBSTITUIR: "var(--app-danger)"}

# Uma linha só: linha em branco fecha o bloco HTML do Streamlit, e recuo de
# quatro espaços vira bloco de código. Seletores com ``.ia-caixa`` na frente
# para vencer o estilo que o Streamlit dá a p/ul/li dentro do markdown.
CSS = "<style>" + "".join((
    ".ia-caixa{background:var(--app-surface);border:1px solid var(--app-border);"
    "border-radius:var(--app-radius-md);padding:16px 18px;margin:10px 0;"
    "color:var(--app-text);font-size:.9rem;line-height:1.6;}",
    ".ia-caixa b,.ia-caixa strong{font-weight:600;}",
    ".ia-titulo{font-size:.68rem;font-weight:700;letter-spacing:.08em;"
    "text-transform:uppercase;color:var(--app-muted);margin:20px 0 8px;"
    "line-height:1.3;}",
    ".ia-caixa>.ia-titulo:first-child,.ia-bloco>.ia-titulo:first-child"
    "{margin-top:0;}",
    ".ia-bloco{border-top:1px solid var(--app-border);margin-top:16px;"
    "padding-top:16px;}",
    ".ia-bloco:first-child{border-top:0;margin-top:0;padding-top:0;}",
    ".ia-caixa p.ia-p{margin:0 0 8px;font-size:.9rem;line-height:1.6;"
    "color:var(--app-text);}",
    ".ia-caixa p.ia-p:last-child{margin-bottom:0;}",
    ".ia-caixa p.ia-lead{font-size:.98rem;font-weight:500;}",
    ".ia-nota{font-size:.75rem;line-height:1.5;color:var(--app-muted);"
    "margin-top:12px;}",
    ".ia-vazio{color:var(--app-muted);font-size:.86rem;}",
    ".ia-num{font-variant-numeric:tabular-nums;}",
    # listas
    ".ia-caixa ul.ia-lista{list-style:none;margin:0;padding:0;}",
    ".ia-caixa ul.ia-lista>li{position:relative;margin:0 0 6px;"
    "padding-left:16px;font-size:.88rem;line-height:1.55;color:var(--app-text);}",
    ".ia-caixa ul.ia-lista>li:last-child{margin-bottom:0;}",
    ".ia-caixa ul.ia-lista>li::before{content:'';position:absolute;left:3px;"
    "top:.62em;width:5px;height:5px;border-radius:50%;"
    "background:var(--app-border-strong);}",
    # cartões de cima
    ".ia-kpis{display:grid;grid-template-columns:repeat(auto-fit,"
    "minmax(160px,1fr));gap:12px;}",
    ".ia-kpi{background:var(--app-surface-raised);border:1px solid "
    "var(--app-border);border-radius:var(--app-radius-sm);padding:12px 14px;}",
    ".ia-kpi--acao{box-shadow:inset 3px 0 0 var(--ia-cor);}",
    ".ia-kpi-rot{font-size:.68rem;font-weight:700;letter-spacing:.08em;"
    "text-transform:uppercase;color:var(--app-muted);}",
    ".ia-kpi-val{font-size:1.45rem;font-weight:700;line-height:1.2;"
    "letter-spacing:-.01em;margin-top:6px;color:var(--app-text);"
    "font-variant-numeric:tabular-nums;}",
    ".ia-kpi-det{font-size:.78rem;line-height:1.4;color:var(--app-muted);"
    "margin-top:4px;}",
    # grade de peças (leituras, dimensões, métricas, cenário)
    ".ia-grade{display:grid;grid-template-columns:repeat(auto-fill,"
    "minmax(230px,1fr));gap:12px;}",
    ".ia-peca{background:var(--app-surface-raised);border:1px solid "
    "var(--app-border);border-radius:var(--app-radius-sm);padding:12px 14px;}",
    ".ia-peca-rot{font-size:.68rem;font-weight:700;letter-spacing:.08em;"
    "text-transform:uppercase;color:var(--app-muted);}",
    ".ia-peca-val{display:flex;align-items:center;gap:8px;font-size:1rem;"
    "font-weight:700;margin:6px 0 8px;}",
    ".ia-peca-val::before{content:'';flex:none;width:8px;height:8px;"
    "border-radius:50%;background:currentColor;}",
    ".ia-peca-det{font-size:.76rem;line-height:1.45;color:var(--app-muted);}",
    ".ia-peca .ia-lista>li{font-size:.84rem;}",
    # a favor e contra
    ".ia-peso{display:grid;grid-template-columns:72px 1fr;gap:12px;"
    "align-items:baseline;margin:0 0 6px;font-size:.88rem;line-height:1.55;}",
    ".ia-peso:last-child{margin-bottom:0;}",
    ".ia-peso-rot{font-size:.68rem;font-weight:700;letter-spacing:.08em;"
    "text-transform:uppercase;}",
    # aviso "vale conferir" e alertas
    ".ia-aviso{background:color-mix(in srgb,var(--app-warning) 6%,"
    "var(--app-surface));border-color:color-mix(in srgb,var(--app-warning) "
    "30%,var(--app-border));border-left:3px solid var(--app-warning);}",
    ".ia-aviso .ia-titulo{color:var(--app-warning);}",
    ".ia-alerta{margin:0 0 6px;font-size:.86rem;line-height:1.55;}",
    # critérios com sinal
    ".ia-crit{display:flex;gap:8px;margin:0 0 4px;font-size:.84rem;"
    "line-height:1.5;color:var(--app-text);}",
    ".ia-crit>span:first-child{flex:none;width:10px;font-weight:700;}",
    # métricas contra os pares
    ".ia-metrica-val{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;"
    "margin-top:6px;}",
    ".ia-metrica-val>b{font-size:1.2rem;font-weight:700;letter-spacing:-.01em;"
    "font-variant-numeric:tabular-nums;}",
    ".ia-legenda{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:.8rem;"
    "color:var(--app-text);margin-bottom:12px;}",
    ".ia-legenda>span{white-space:nowrap;}",
    # manchetes e documentos
    ".ia-item{padding:10px 0;border-top:1px solid var(--app-border);}",
    ".ia-item:first-child{border-top:0;padding-top:0;}",
    ".ia-meta{font-size:.74rem;color:var(--app-muted);"
    "font-variant-numeric:tabular-nums;margin-bottom:2px;}",
    ".ia-caixa a{color:var(--app-text);text-decoration:none;border-bottom:1px "
    "solid var(--app-border-strong);}",
    ".ia-caixa a:hover{color:var(--app-info);border-bottom-color:var(--app-info);}",
    # cabeçalho de grupo e lista de renda fixa
    ".ia-grupo{display:flex;justify-content:space-between;align-items:baseline;"
    "flex-wrap:wrap;gap:8px;margin:28px 0 4px;padding-bottom:8px;"
    "border-bottom:1px solid var(--app-border);}",
    ".ia-grupo-nome{font-size:1.1rem;font-weight:700;color:var(--app-text);}",
    ".ia-grupo-valor{font-size:.95rem;font-weight:600;color:var(--app-text);"
    "font-variant-numeric:tabular-nums;}",
    ".ia-grupo-extra{font-size:.8rem;color:var(--app-muted);margin-bottom:6px;}",
    # um card por título da reserva e da renda fixa
    ".ia-titulos{display:grid;grid-template-columns:repeat(auto-fill,"
    "minmax(220px,1fr));gap:12px;margin:12px 0 4px;}",
    ".ia-tit{display:flex;flex-direction:column;background:var(--app-surface);"
    "border:1px solid var(--app-border);border-radius:var(--app-radius-sm);"
    "padding:14px 16px;box-shadow:inset 0 3px 0 var(--app-primary);"
    "color:var(--app-text);}",
    ".ia-tit-topo{display:flex;justify-content:space-between;"
    "align-items:baseline;gap:8px;}",
    ".ia-tit-tk{font-size:.95rem;font-weight:700;letter-spacing:.01em;"
    "overflow-wrap:anywhere;}",
    ".ia-tit-peso{flex:none;font-size:.78rem;font-weight:700;padding:1px 8px;"
    "border-radius:999px;background:var(--app-surface-raised);"
    "border:1px solid var(--app-border);font-variant-numeric:tabular-nums;}",
    ".ia-tit-nome{font-size:.78rem;line-height:1.4;color:var(--app-muted);"
    "margin-top:2px;}",
    ".ia-tit-val{font-size:1.3rem;font-weight:700;letter-spacing:-.01em;"
    "line-height:1.2;margin-top:auto;padding-top:12px;"
    "font-variant-numeric:tabular-nums;white-space:nowrap;}",
    ".ia-tit-barra{height:4px;border-radius:2px;background:var(--app-border);"
    "margin-top:10px;overflow:hidden;}",
    ".ia-tit-barra>span{display:block;height:100%;border-radius:2px;"
    "background:var(--app-primary);}",
    ".ia-tit-det{font-size:.72rem;color:var(--app-muted);margin-top:4px;"
    "font-variant-numeric:tabular-nums;}",
    "@media (max-width:640px){.ia-titulos{grid-template-columns:repeat(2,"
    "minmax(0,1fr));gap:8px;}.ia-tit{padding:12px;}"
    ".ia-tit-topo{flex-wrap:wrap;gap:4px;}"
    ".ia-tit-val{font-size:1.02rem;white-space:normal;}}",
    # bloco técnico recolhido
    ".ia-tecnico{margin:12px 0 4px;border:1px solid var(--app-border);"
    "border-radius:var(--app-radius-sm);background:var(--app-surface);}",
    ".ia-tecnico>summary{cursor:pointer;list-style:none;display:flex;"
    "align-items:center;gap:8px;padding:10px 14px;font-size:.84rem;"
    "font-weight:600;color:var(--app-muted);}",
    ".ia-tecnico>summary::-webkit-details-marker{display:none;}",
    ".ia-tecnico>summary::before{content:'›';display:inline-block;"
    "font-size:1.1rem;line-height:1;transition:transform .15s ease;}",
    ".ia-tecnico[open]>summary::before{transform:rotate(90deg);}",
    ".ia-tecnico>summary:hover{color:var(--app-text);}",
    ".ia-tecnico-corpo{padding:0 14px 4px;}",
    ".ia-tecnico .ia-caixa{background:var(--app-surface-raised);}",
)) + "</style>"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def _moeda(x: float | None, moeda: str = "BRL") -> str:
    if x is None:
        return "—"
    return fmt_moeda(x) if moeda == "BRL" else f"{moeda} {x:,.2f}"


def _link(texto: str, url: str | None) -> str:
    if url and url.lower().startswith(("http://", "https://")):
        return (f'<a href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{escape(texto)}</a>')
    return escape(texto)


def _caixa(corpo: str, extra: str = "") -> str:
    return f'<div class="ia-caixa{" " + extra if extra else ""}">{corpo}</div>'


def _secao(titulo: str) -> str:
    return f'<div class="ia-titulo">{escape(titulo)}</div>'


def _nota(texto: str) -> str:
    return f'<div class="ia-nota">{escape(texto)}</div>'


def _vazio(texto: str) -> str:
    return f'<div class="ia-vazio">{escape(texto)}</div>'


# -- cabeçalho de grupo e lista simples ---------------------------------------------

def cabecalho_grupo(b: rs.Bloco, meta_reserva: str | None = None) -> str:
    """Nome do grupo, valor e % da carteira, e o alvo da classe. Puro."""
    alvo = ""
    if b.alvo_classe is not None:
        if b.chave == rs.RESERVA:
            alvo = (f"entra na meta da renda fixa (meta "
                    f"{_pct(b.alvo_classe)}; hoje {_pct(b.peso_classe)})")
        elif b.chave == rs.RENDA_FIXA:
            alvo = (f"meta da renda fixa, com a reserva: "
                    f"{_pct(b.alvo_classe)}; hoje {_pct(b.peso_classe)}")
        else:
            alvo = f"sua meta para esta classe: {_pct(b.alvo_classe)}"
    extra = " · ".join(x for x in (alvo, meta_reserva or "") if x)
    return (
        f'<div class="ia-grupo"><div class="ia-grupo-nome">'
        f'{_ICONE.get(b.chave, "")} {escape(b.rotulo)}</div>'
        f'<div class="ia-grupo-valor">'
        f'{escape(_moeda(b.valor))} · {escape(_pct(b.peso))}</div></div>'
        + (f'<div class="ia-grupo-extra">{escape(extra)}</div>'
           if extra else ""))


def _parte_do_grupo(valor: float | None, total: float | None) -> float | None:
    """% do título dentro do grupo, que a barra do card desenha."""
    if valor is None or not total or total <= 0:
        return None
    return max(0.0, min(100.0, valor / total * 100))


def cartao_lista(b: rs.Bloco) -> str:
    """Reserva e renda fixa: um card por título, com valor, % da carteira e
    uma barra com a parte do grupo (14/11/2026, pedido do usuário: cards no
    lugar da tabela simples). Puro."""
    def _card(ln: rs.Linha) -> str:
        parte = _parte_do_grupo(ln.valor, b.valor)
        barra = ("" if parte is None else
                 f'<div class="ia-tit-barra"><span style="width:{parte:.1f}%">'
                 f'</span></div><div class="ia-tit-det">'
                 f'{escape(_pct(parte))} do grupo</div>')
        return (f'<div class="ia-tit"><div class="ia-tit-topo">'
                f'<span class="ia-tit-tk">{escape(ln.ticker)}</span>'
                f'<span class="ia-tit-peso" title="% da carteira">'
                f'{escape(_pct(ln.peso))}</span></div>'
                + (f'<div class="ia-tit-nome">{escape(ln.nome)}</div>'
                   if ln.nome else "")
                + f'<div class="ia-tit-val">'
                f'{escape(_moeda(ln.valor, ln.moeda))}</div>{barra}</div>')
    if not b.linhas:
        return _caixa(_vazio("Nenhum título neste grupo."))
    return f'<div class="ia-titulos">{"".join(map(_card, b.linhas))}</div>'


# -- a caixa de cada ativo ------------------------------------------------------------

def rotulo_expander(a: m.AnaliseAtivo,
                    sugerido: rs.AlvoSugerido | None = None,
                    avaliacao: av_.Avaliacao | None = None,
                    d: rs.Decisao | None = None) -> str:
    """"BBAS3 · pesa 8,5% (ideal 2,3%) · Rebalancear": peso atual, % devida
    e a ação, sem a seta que pedia decifrar (13/11/2026). ``d`` reaproveita
    a decisão já calculada."""
    d = d if d is not None else rs.decisao(a, sugerido, avaliacao)
    alvo = rs.alvo_do_ativo(a, sugerido)
    peso = f"pesa {_pct(a.ativo.peso_atual)}"
    if alvo is not None:
        peso += f" (ideal {_pct(alvo)})"
    return f"{a.ativo.ticker} · {peso} · {d.rotulo}"


ROTULOS_POSICAO = ("Quanto pesa hoje", "Tamanho ideal", "O que fazer",
                   "Certeza")


def cartao_posicao(a: m.AnaliseAtivo,
                   sugerido: rs.AlvoSugerido | None = None,
                   avaliacao: av_.Avaliacao | None = None,
                   d: rs.Decisao | None = None,
                   conversa: ls.Conversa | None = None) -> str:
    """Quanto pesa hoje, o tamanho ideal, o que fazer e com quanta certeza,
    em linguagem simples. Puro."""
    d = d if d is not None else rs.decisao(a, sugerido, avaliacao)
    cv = conversa if conversa is not None else ls.conversar(a, d, sugerido)
    devido = rs.peso_devido(a, sugerido)
    cor = _COR_DECISAO[d.codigo]

    def _kpi(rotulo, valor, detalhe="", cor_valor=""):
        acao = (f' ia-kpi--acao" style="--ia-cor:{cor_valor}' if cor_valor
                else "")
        estilo = f' style="color:{cor_valor}"' if cor_valor else ""
        return (f'<div class="ia-kpi{acao}">'
                f'<div class="ia-kpi-rot">{escape(rotulo)}</div>'
                f'<div class="ia-kpi-val"{estilo}>{escape(valor)}</div>'
                + (f'<div class="ia-kpi-det">{escape(detalhe)}</div>'
                   if detalhe else "")
                + '</div>')
    hoje, ideal, fazer, certeza = ROTULOS_POSICAO
    return _caixa(
        '<div class="ia-kpis">'
        + _kpi(hoje, _pct(a.ativo.peso_atual),
               _moeda(a.ativo.valor_mercado, a.ativo.moeda))
        + _kpi(ideal, devido.valor, cv.devida_curta)
        + _kpi(fazer, d.rotulo, cv.acao_curta, cor)
        + _kpi(certeza, ls.CERTEZA[d.conviccao], cv.certeza_curta)
        + "</div>")


def _paragrafos(frases, lead: bool = False) -> str:
    frases = [f for f in frases if f]
    return "".join(
        f'<p class="ia-p{" ia-lead" if lead and i == 0 else ""}">'
        f'{escape(f)}</p>' for i, f in enumerate(frases))


_COR_TOM = {1: "var(--app-primary)", -1: "var(--app-danger)",
            0: "var(--app-muted)"}


def _lista(itens) -> str:
    return ('<ul class="ia-lista">'
            + "".join(f"<li>{escape(x)}</li>" for x in itens if x)
            + "</ul>")


def _bloco_leitura(lt: ls.Leitura) -> str:
    return (f'<div class="ia-peca"><div class="ia-peca-rot">'
            f'{escape(lt.rotulo)}</div>'
            f'<div class="ia-peca-val" style="color:{_COR_TOM[lt.tom]}">'
            f'{escape(lt.leitura.capitalize())}</div>'
            + (_lista(lt.frases) if lt.frases else "")
            + "</div>")


_ROTULO_PESO = {"A favor": "var(--app-primary)", "Contra": "var(--app-danger)"}


def _pesos(itens) -> str:
    """"A favor: x." vira rótulo colorido e texto lado a lado."""
    linhas = []
    for x in itens:
        rot, _, resto = x.partition(": ")
        if rot in _ROTULO_PESO and resto:
            linhas.append(
                f'<div class="ia-peso"><span class="ia-peso-rot" '
                f'style="color:{_ROTULO_PESO[rot]}">{escape(rot)}</span>'
                f'<span>{escape(resto)}</span></div>')
        else:
            linhas.append(_lista((x,)))
    return "".join(linhas)


def _bloco(titulo: str, corpo: str) -> str:
    return f'<div class="ia-bloco">{_secao(titulo)}{corpo}</div>'


def cartao_conversa(cv: ls.Conversa) -> str:
    """A recomendação em linguagem simples, sem repetir os cartões de cima:
    por quê, o que eu vi, na prática, a favor e contra e o que mudaria.
    Seção sem conteúdo não aparece. Puro."""
    partes = [_bloco("Por quê", _paragrafos(cv.porque, lead=True))]
    if cv.leituras:
        partes.append(_bloco(
            "O que eu vi", '<div class="ia-grade">'
            + "".join(_bloco_leitura(x) for x in cv.leituras) + "</div>"))
    if cv.pratica:
        partes.append(_bloco("Na prática", _lista(cv.pratica)))
    if cv.pesos:
        partes.append(_bloco("A favor e contra", _pesos(cv.pesos)))
    if cv.mudaria:
        partes.append(_bloco("O que mudaria a sugestão", _lista(cv.mudaria)))
    html = _caixa("".join(partes))
    if cv.investigar:
        html += _caixa(_secao(TITULO_INVESTIGAR) + _lista(cv.investigar),
                       "ia-aviso")
    return html


TITULO_INVESTIGAR = "Vale conferir"


def bloco_tecnico(d: rs.Decisao, av: av_.Avaliacao) -> str:
    """O texto técnico de sempre (motivo, alertas, justificativa, condições,
    limitações e a avaliação por dimensão), recolhido. O aviso de
    metodologia (``rs.AVISO_DECISAO``) fica só para as LLMs: na tela ele
    cansava sem informar (pedido do usuário, 12/11/2026). Puro."""
    motivo = (_caixa(_paragrafos((d.motivo,))) if d.motivo else "")
    corpo = (motivo + cartao_alertas(d) + cartao_recomendacao(d)
             + cartao_avaliacao(av))
    return ('<details class="ia-tecnico"><summary>Ver a análise técnica '
            'completa</summary>'
            f'<div class="ia-tecnico-corpo">{corpo}</div></details>')


def _itens(itens) -> str:
    return _lista(itens)


def cartao_recomendacao(d: rs.Decisao) -> str:
    """O porquê da ação: fatores pesados, proporção e custo da venda,
    substituto, o que mudaria a recomendação, dados ausentes e o pano de
    fundo. Puro."""
    partes = [_secao("Justificativa"), _itens(d.justificativas)]
    momento = d.momento.linha() if d.momento is not None else None
    if momento:
        # A queda à vista, antes da venda (pedido de 08/11/2026): vender
        # num preço deprimido pode transformar oscilação em perda.
        partes += [_secao("Momento: o ativo está em queda"),
                   _itens((momento.removeprefix("Momento: "),))]
    if d.proporcao:
        venda = [f"Vender cerca de {d.proporcao * 100:.0f}% da posição."]
        if d.custos is not None:
            venda += list(d.custos.linhas)
        partes += [_secao("Proporção e custo estimado da venda"),
                   _itens(venda)]
    if d.substituto is not None:
        partes += [_secao("Substituto comparado"),
                   _itens((d.substituto.resumo + ".",))]
    for titulo, itens in (("Condições que mudariam a recomendação",
                           d.condicoes),
                          ("Dados ausentes e limitações", d.limitacoes),
                          ("Contexto: cenário, noticiário e valorização",
                           tuple(x for x in d.contexto if x != momento))):
        if itens:
            partes += [_secao(titulo), _itens(itens)]
    return _caixa("".join(partes))


TITULO_ALERTAS = "Alertas para investigar (não são ordens de compra ou venda)"


def cartao_alertas(d: rs.Decisao) -> str:
    """Os sinais que pedem olhar mais de perto, separados da ação: peso acima
    da meta, preço no topo do histórico, manchete a confirmar. Vazio sem
    alerta. Puro."""
    if not d.alertas:
        return ""
    return _caixa(_secao(TITULO_ALERTAS) + _itens(d.alertas), "ia-aviso")


AVISO_AVALIACAO = ("Avaliação por regras sobre os dados do app, não "
                   "recomendação. O momento de preço descreve o passado "
                   "recente e não promete o próximo ano.")

_COR_LEITURA = {
    av_.FORTE: "var(--app-primary)", av_.BARATO: "var(--app-primary)",
    av_.POSITIVO: "var(--app-primary)",
    av_.ADEQUADA: "var(--app-text)", av_.JUSTO: "var(--app-text)",
    av_.NEUTRO: "var(--app-text)", av_.MISTO: "var(--app-warning)",
    av_.FRAGIL: "var(--app-danger)", av_.CARO: "var(--app-warning)",
    av_.NEGATIVO: "var(--app-danger)",
    av_.INSUFICIENTE: "var(--app-muted)", av_.SEM_LEITURA: "var(--app-muted)",
}
_SINAL = {1: ("+", "var(--app-primary)"), -1: ("−", "var(--app-danger)"),
          0: ("·", "var(--app-muted)")}


def _bloco_dimensao(d: av_.Dimensao) -> str:
    if d.nota:
        detalhe_tecnico(f"{d.rotulo}: {d.nota}",
                        codigo="inteligencia.avaliacao_nota_dimensao")
    cor = _COR_LEITURA.get(d.leitura, "var(--app-text)")
    itens = "".join(
        f'<div class="ia-crit"><span style="color:{_SINAL[c.sinal][1]}">'
        f'{_SINAL[c.sinal][0]}</span><span>{escape(c.texto)}</span></div>'
        for c in d.criterios)
    contagem = (f"{d.favoraveis} a favor · {d.desfavoraveis} contra"
                if d.criterios else "")
    return (
        f'<div class="ia-peca"><div class="ia-peca-rot">{escape(d.rotulo)}'
        f'</div><div class="ia-peca-val" style="color:{cor}">'
        f'{escape(d.rotulo_leitura)}</div>'
        + (f'<div class="ia-peca-det" style="margin:-4px 0 8px">'
           f'{escape(contagem)}</div>' if contagem else "")
        + itens
        + "</div>")


def _rotulo_alerta(al: av_.Alerta) -> str:
    if not al.critico:
        return "Alerta"
    if al.noticia:
        return "Alerta eliminatório no noticiário (a confirmar)"
    return "Alerta eliminatório"


def cartao_avaliacao(av: av_.Avaliacao) -> str:
    """Qualidade, preço e mercado com os critérios que pesaram e os alertas.
    Puro."""
    detalhe_tecnico(f"Régua setorial: {av.rotulo_perfil}",
                    codigo="inteligencia.avaliacao_regua_setorial")
    detalhe_tecnico(av_.AVISO, codigo="inteligencia.avaliacao_metodologia")
    alertas = "".join(
        f'<div class="ia-alerta" style="color:'
        f'{"var(--app-danger)" if al.critico else "var(--app-warning)"}">'
        f'<b>{_rotulo_alerta(al)}:</b> '
        f'{escape(al.texto)}</div>' for al in av.alertas)
    grade = "".join(_bloco_dimensao(d) for d in av.dimensoes)
    return _caixa(f'{_secao("Avaliação do ativo")}{alertas}'
                  f'<div class="ia-grade" style="margin-top:8px">{grade}</div>'
                  f'{_nota(AVISO_AVALIACAO)}')


def _troca(t: ls.Troca) -> str:
    vant = "".join(f'<div class="ia-p">✓ {escape(x)}</div>' for x in t.vantagens)
    desv = "".join(f'<div class="ia-p">✗ {escape(x)}</div>'
                   for x in t.desvantagens)
    return (f'<div class="ia-item"><b>{escape(t.ticker)}</b> '
            f'<span class="ia-meta">{escape(t.nome)}</span>'
            f'<div class="ia-peca-det">{escape(t.apresentacao)}</div>'
            f'<div style="margin-top:6px">{vant}{desv}</div>'
            f'<p class="ia-p" style="margin-top:6px"><b>{escape(t.conclusao)}'
            f'</b></p></div>')


def cartao_substitutos(a: m.AnaliseAtivo, subs: tuple[prs.Par, ...]) -> str:
    """Só aparece quando a ação é vender ou substituir: cada alternativa com
    o que tem de melhor (✓) e de pior (✗) que o ativo, em palavras, e se
    passa no critério de troca (``linguagem_simples.trocar_por``). Puro."""
    if not subs:
        corpo = _vazio("Não achamos outra empresa do mesmo setor, fora da sua "
                       "carteira, para comparar. Quando há poucas, a "
                       "comparação junta setores diferentes, e trocar de "
                       "setor não é sugerido.")
    else:
        corpo = "".join(_troca(ls.trocar_por(a, p)) for p in subs)
    return _caixa(f'{_secao("Se vender, por qual trocar?")}'
                  f'{corpo}{_nota(rs.AVISO_SUBSTITUTO)}')


def _regua(mt: rs.MetricaPares) -> str:
    """Uma régua por métrica: o ativo (ponto cheio), os pares (vazados) e o
    típico do setor, a mediana (traço). A régua vai do menor ao maior valor
    mostrado; não há lado bom nem ruim."""
    marcas = []
    if mt.mediana_pos is not None:
        marcas.append(
            f'<div title="{ls.TIPICO}" style="position:absolute;'
            f'left:{mt.mediana_pos:.1f}%;top:-4px;width:2px;height:14px;'
            f'margin-left:-1px;background:var(--app-text);opacity:.55"></div>')
    for pt in sorted(mt.pontos, key=lambda x: x.eh_ativo):
        tam = 12 if pt.eh_ativo else 9
        estilo = ("background:var(--app-primary);border:2px solid "
                  "var(--app-surface)" if pt.eh_ativo else
                  "background:var(--app-surface);border:2px solid var(--app-muted)")
        marcas.append(
            f'<div title="{escape(pt.ticker, quote=True)}: '
            f'{escape(pt.texto, quote=True)}" style="position:absolute;'
            f'left:{pt.posicao:.1f}%;top:{3 - tam / 2:.1f}px;width:{tam}px;'
            f'height:{tam}px;margin-left:-{tam / 2:.1f}px;border-radius:50%;'
            f'{estilo}"></div>')
    return ('<div style="position:relative;height:6px;margin:14px 6px 10px;'
            'border-radius:3px;background:var(--app-border)">'
            + "".join(marcas) + "</div>")


def _bloco_metrica(mt: rs.MetricaPares) -> str:
    pares = " · ".join(f"{escape(pt.ticker)} {escape(pt.texto)}"
                       for pt in mt.pontos if not pt.eh_ativo)
    return (
        f'<div class="ia-peca" title="{escape(mt.leitura, quote=True)}">'
        f'<div class="ia-peca-rot">'
        f'{escape(ls.rotulo_metrica(mt.chave, mt.rotulo))}</div>'
        + (f'<div class="ia-peca-det">{escape(ls.O_QUE_MEDE[mt.chave])}</div>'
           if mt.chave in ls.O_QUE_MEDE else "")
        + f'<div class="ia-metrica-val"><b>{escape(mt.texto_ativo)}</b>'
        f'<span class="ia-peca-det">'
        f'{escape(ls.ROTULO_POSICAO_SIMPLES.get(mt.posicao, ""))}</span></div>'
        + _regua(mt)
        + f'<div class="ia-peca-det ia-num">'
          f'Típico do setor: {escape(mt.texto_mediana)} (entre '
          f'{mt.n_pares} {"empresa" if mt.n_pares == 1 else "empresas"})</div>'
        + (f'<div class="ia-peca-det ia-num">{pares}</div>' if pares else "")
        + "</div>")


def cartao_pares(t: rs.TabelaPares) -> str:
    """O ativo contra empresas parecidas, uma régua por indicador. Puro."""
    titulo = "Como se compara com empresas parecidas"
    if not t.linhas:
        aviso_lacuna(f"Comparação com o segmento: {t.motivo or 'sem dado'}",
                     codigo="tela.inteligencia.pares_sem_dado")
        return _caixa(_secao(titulo)
                      + _vazio("Comparação indisponível no momento."))
    ponto = ('<span style="display:inline-block;width:9px;height:9px;'
             'border-radius:50%;margin-right:6px;vertical-align:middle;{}">'
             '</span>')
    legenda = []
    for i, (tk, nome) in enumerate(t.nomes):
        estilo = ("background:var(--app-primary)" if i == 0 else
                  "border:2px solid var(--app-muted)")
        legenda.append(f'<span>{ponto.format(estilo)}<b>{escape(tk)}</b> '
                       f'<span class="ia-meta">{escape(nome)}</span></span>')
    legenda.append('<span><span style="display:inline-block;width:2px;'
                   'height:11px;margin-right:6px;vertical-align:middle;'
                   'background:var(--app-text);opacity:.55"></span>'
                   f'{ls.TIPICO}</span>')
    grade = "".join(_bloco_metrica(mt) for mt in t.metricas)
    setor = f" · {t.setor}" if t.setor else ""
    return _caixa(f'{_secao(f"{titulo}{setor}")}'
                  f'<div class="ia-legenda">{"".join(legenda)}</div>'
                  f'<div class="ia-grade">{grade}</div>'
                  f'{_nota(ls.RODAPE_PARES)}')


def cartao_papel(a: m.AnaliseAtivo) -> str:
    """Por que o ativo está na carteira, em palavras de conversa. Puro."""
    papel = a.papel_principal.rotulo if a.papel_principal else "—"
    outros = [p.rotulo for p in a.papeis
              if a.papel_principal is None or p.codigo != a.papel_principal.codigo]
    tese = a.tese
    linhas = [f"<b>Função na carteira:</b> {escape(papel)}"
              + (f' <span class="ia-meta">(também: '
                 f'{escape(", ".join(outros))})</span>' if outros else "")]
    if tese.por_que_esta_na_carteira:
        linhas.append(f"<b>O motivo:</b> "
                      f"{escape(tese.por_que_esta_na_carteira)}")
    if tese.alinhamento:
        linhas.append(f"<b>Combina com a sua estratégia?</b> "
                      f"{escape(tese.alinhamento)}")
    return _caixa(_secao("Por que você tem este ativo")
                  + "".join(f'<p class="ia-p">{x}</p>' for x in linhas))


def _manchete(i) -> str:
    origem = f"{i.ticker} · " if getattr(i, "ticker", None) else ""
    return (f'<div class="ia-item"><div class="ia-meta">{escape(origem)}'
            f'{escape(rs.inf._data_br(i.date))} · {escape(i.source or "—")}'
            f'</div><div>{_link(i.headline, i.url)}</div></div>')


def _manchete_cenario(i: rs.ItemCenario) -> str:
    pais = f" · {i.pais}" if i.pais else ""
    return (f'<div class="ia-item"><div class="ia-meta">'
            f'{escape(i.tema.capitalize())}{escape(pais)} · '
            f'{escape(i.data)} · {escape(i.veiculo or "—")}</div>'
            f'<div>{_link(i.titulo, i.url)}</div></div>')


def _manchetes(html: str) -> str:
    return f"<div>{html}</div>"


def cartao_noticias(a: m.AnaliseAtivo,
                    gerais: tuple[list[dict], str] | None = None) -> str:
    """Notícias do ativo; sem elas, as do segmento; e, se ainda faltar, o
    cenário econômico e político que pesa na classe do ativo. Nunca em
    branco. Puro."""
    itens, motivo = rs.noticias(a)
    partes = []
    if itens:
        partes.append(_manchetes("".join(_manchete(i) for i in itens)))
    else:
        aviso_lacuna(f"Notícias do ativo: {motivo or 'sem notícias'}",
                     codigo="tela.inteligencia.noticias_do_ativo_sem_dado",
                     entidade=str(a.ativo.ticker))
        partes.append(_vazio("Nenhuma notícia disponível para este ativo."))
    setor, rotulo = rs.noticias_setor(a)
    if setor:
        partes.append(_secao("Como está o setor"
                             + (f" · {rotulo}" if rotulo else "")))
        partes.append(_manchetes("".join(_manchete(i) for i in setor)))
        from core.inteligencia_ativos.secoes import ROTULO_POSICOES_ETF
        partes.append(_nota(
            "Notícias das maiores posições do fundo, não do fundo."
            if str(rotulo or "").startswith(ROTULO_POSICOES_ETF) else
            "Notícias de outras empresas do mesmo setor, não desta."))
    if gerais is not None and rs.precisa_noticiario_geral(a):
        brutos, origem = gerais
        falta = max(rs.N_NOTICIAS - len(itens) - len(setor), 1)
        cenario = rs.noticias_cenario(a, brutos, falta)
        temas = rs.temas_do_ativo(a.ativo.classe_politica)
        partes.append(_secao("Economia e política"))
        if cenario:
            partes.append(_manchetes(
                "".join(_manchete_cenario(i) for i in cenario)))
        else:
            partes.append(_vazio("Nenhuma manchete de juros, inflação, "
                                 "câmbio, fiscal ou política no período."))
        detalhe_tecnico("Notícias do cenário"
                        + (f" escolhidas por {temas}" if temas else "")
                        + (f"; fonte: {origem}" if origem else ""),
                        codigo="inteligencia.noticias_cenario_origem",
                        entidade=str(a.ativo.ticker))
        partes.append(_nota("Fatos do país que mexem com este ativo."))
    elif not itens and not setor:
        aviso_lacuna("Cenário econômico e político indisponível",
                     codigo="tela.inteligencia.cenario_noticias_indisponivel",
                     entidade=str(a.ativo.ticker))
    return _caixa(_secao("Notícias") + "".join(partes))


@st.cache_data(ttl=900, show_spinner=False)
def _noticiario_geral() -> tuple[list[dict], str]:
    """Noticiário geral cru (acervo, túnel ou vitrine), com o tipo de evento
    de cada item. Falha vira lista vazia com a fonte nomeada."""
    try:
        from core.contexto_mercado import itens_gerais
        return itens_gerais()
    except Exception as exc:  # noqa: BLE001 - fonte fora do ar não derruba a página
        return [], f"leitura falhou ({type(exc).__name__})"


@st.cache_data(ttl=3600, show_spinner=False)
def _destaques_crus(ticker: str) -> tuple[tuple, ...]:
    """Frases de fato dos documentos do ativo, do corpus RAG, em tuplas
    simples. Falha vira vazio: a caixa cai na lista de documentos.

    O cache guarda tuplas, e não ``dr.Destaque``: no deploy,
    ``core/modulos_frescos`` descarta o módulo enquanto outra sessão ainda
    roda o velho, e o pickle recusa o ``Destaque`` dele por não ser o objeto
    do módulo novo (``UnserializableReturnValueError``, 03/10/2026)."""
    try:
        return tuple((d.titulo, d.data, d.tipo, tuple(d.frases))
                     for d in dr.ler(ticker))
    except Exception:  # noqa: BLE001 - corpus ilegível não derruba a página
        return ()


def _destaques(ticker: str) -> tuple[dr.Destaque, ...]:
    return tuple(dr.Destaque(*c) for c in _destaques_crus(ticker))


def cartao_relatorios(a: m.AnaliseAtivo,
                      destaques: tuple[dr.Destaque, ...] = ()) -> str:
    """O que os documentos dizem (frases do emissor com fato e número), não
    o link para o documento inteiro. Documento sem texto extraído aparece
    só com título e data. Puro."""
    docs, motivo = rs.relatorios(a)
    partes = []
    for d in destaques:
        partes.append(
            f'<div class="ia-item"><div class="ia-meta">'
            f'{escape(rs.inf._data_br(d.data))} · {escape(d.tipo)}</div>'
            f'<div style="font-weight:600;margin:2px 0 6px">'
            f'{escape(d.titulo)}</div>{_lista(d.frases)}</div>')
    restantes = [d for d in docs
                 if not any(dr.mesmo_documento(d.titulo, d.reference_date, x)
                            for x in destaques)]
    if restantes:
        partes.append(
            _secao("Outros documentos recentes" if destaques
                   else "Documentos recentes")
            + "".join(
                f'<div class="ia-item"><div class="ia-meta">'
                f'{escape(rs.inf._data_br(d.reference_date))} · '
                f'{escape(d.rotulo or "")}</div>'
                f'<div>{escape(d.titulo or "")}</div></div>'
                for d in restantes))
    if restantes:
        aviso_lacuna("Relatórios: documentos recentes ainda sem texto "
                     "extraído no acervo",
                     codigo="tela.inteligencia.relatorios_sem_texto",
                     entidade=str(a.ativo.ticker))
    if not partes:
        aviso_lacuna(f"Relatórios: {motivo or rs.inf.NAO_DISPONIVEL}",
                     codigo="tela.inteligencia.relatorios_sem_dado",
                     entidade=str(a.ativo.ticker))
        partes.append(_vazio("Nenhum relatório disponível para este ativo."))
    nota = ("Trechos copiados dos documentos oficiais da empresa. O que isso "
            "muda na sua decisão está na análise completa." if destaques else
            "Os pontos principais aparecem aqui quando o texto dos "
            "documentos estiver disponível.")
    return _caixa(_secao("O que a empresa divulgou") + "".join(partes)
                  + _nota(nota))


def _item_macro(classe: str | None, chave: str, valor: str,
                direcao: str | None, divergente: bool) -> str:
    tend = ls.TENDENCIA_SIMPLES.get(direcao or "")
    rot = ls.ROTULO_CENARIO.get(chave, chave)
    porque = ls.por_que_pesa(classe, chave)
    return (f'<div class="ia-item"><b>{escape(rot)}</b>'
            + (f' <span class="ia-meta">· {escape(tend)}</span>' if tend else "")
            + f'<div class="ia-p">{escape(ls.valor_simples(valor))}</div>'
            + (f'<div class="ia-peca-det">Por que importa: {escape(porque)}'
               f'</div>' if porque else "")
            + ('<div class="ia-peca-det" style="color:var(--app-warning)">'
               'O dado mais novo difere do registrado no seu cenário; vale '
               'revisar.</div>' if divergente else "")
            + "</div>")


def cartao_macro(mc: rs.Macro, impacto: str | None = None) -> str:
    """O que da economia mexe com este ativo: cada variável que pesa na
    classe, com o valor de hoje em palavras, a direção e por que importa.
    Sem chaves (Macro antigo), cai na lista de premissas. Puro."""
    nomes = [ls.ROTULO_CENARIO.get(k, k) for k in mc.chaves] or list(mc.canais)
    partes = [f'<p class="ia-p"><b>O que mais mexe com este tipo de '
              f'investimento:</b> {escape(", ".join(nomes) or "—")}</p>']
    if mc.sem_cenario:
        aviso_lacuna("Macro: as séries do banco não puderam ser lidas",
                     codigo="tela.inteligencia.macro_series_ilegiveis")
        partes.append(_vazio("Os dados da economia estão indisponíveis no momento."))
    elif mc.itens:
        partes.append("".join(
            _item_macro(mc.classe, k, v, dr_, k in mc.sinais_chaves)
            for k, v, dr_ in mc.itens))
    elif mc.premissas:
        partes.append(_lista(mc.premissas))
    else:
        aviso_lacuna("Macro: sem dado para as variáveis desta classe",
                     codigo="tela.inteligencia.macro_sem_variaveis")
        partes.append(_vazio("Os dados da economia estão indisponíveis no momento."))
    # Sinal de variável sem item na tela (ou Macro antigo, sem chaves) não
    # some: vai no texto original.
    com_item = {k for k, _, _ in mc.itens}
    soltos = [s for i, s in enumerate(mc.sinais)
              if i >= len(mc.sinais_chaves) or mc.sinais_chaves[i] not in com_item]
    if soltos:
        partes.append('<div style="color:var(--app-warning);margin-top:8px">'
                      + "".join(f'<div class="ia-alerta">{escape(s)}</div>'
                                for s in soltos) + "</div>")
    if impacto:
        partes.append(f'<p class="ia-p" style="margin-top:8px"><b>O que a '
                      f'análise completa concluiu:</b> {escape(impacto)}</p>')
    else:
        partes.append(_nota("O efeito disso neste ativo em particular é lido "
                            "na análise completa."))
    return _caixa(_secao("O que da economia mexe com este ativo")
                  + "".join(partes))


_SETA_TENDENCIA = {"alta": ("▲ subindo", "var(--app-warning)"),
                   "queda": ("▼ caindo", "var(--app-info)"),
                   "estavel": ("● estável", "var(--app-muted)"),
                   "incerta": ("? sem direção clara", "var(--app-muted)")}
TITULO_CENARIO = "🌎 Como está a economia agora"


def cartao_cenario(linhas: tuple[rs.LinhaCenario, ...]) -> str:
    """Como está a economia agora, lido dos dados: uma peça por variável,
    com o que ela é, o valor em palavras e a direção calculada. Fonte e data
    vão para o detalhe técnico. Puro."""
    if not linhas or not any(ln.valor for ln in linhas):
        aviso_lacuna("Cenário econômico: as séries macro do banco não puderam "
                     "ser lidas",
                     codigo="tela.inteligencia.cenario_series_ilegiveis")
        return _caixa(_secao(TITULO_CENARIO)
                      + _vazio("Os dados da economia estão indisponíveis no momento."))
    detalhe_tecnico("Cenário lido pelo programa das séries do banco (Selic, "
                    "IPCA, curva do Tesouro, dólar, juros e crédito nos EUA). "
                    "Commodities e risco geopolítico não têm série: as LLMs "
                    "os leem nas notícias.",
                    codigo="inteligencia.cenario_metodologia")
    pecas = []
    for ln in linhas:
        if ln.valor:
            seta, cor = _SETA_TENDENCIA.get(ln.tendencia or "incerta",
                                            _SETA_TENDENCIA["incerta"])
            detalhe_tecnico(f"{ln.rotulo}: {ln.fonte}"
                            + (f" · {ln.referencia}" if ln.referencia else ""),
                            codigo="inteligencia.cenario_fonte_serie",
                            entidade=ln.rotulo)
            oque = ls.O_QUE_E.get(ln.chave, "")
            corpo = ((f'<div class="ia-peca-det">{escape(oque)}</div>'
                      if oque else "")
                     + f'<div class="ia-num" style="font-size:.88rem;'
                     f'margin-top:6px">{escape(ls.valor_simples(ln.valor))}</div>'
                     f'<div style="font-size:.74rem;font-weight:700;color:{cor};'
                     f'margin-top:6px">{escape(seta)}</div>')
        else:
            aviso_lacuna(f"Cenário econômico — {ln.rotulo}: sem dado "
                         f"({ln.fonte or 'ausente'})",
                         codigo="tela.inteligencia.cenario_variavel_sem_dado",
                         entidade=ln.rotulo)
            corpo = ('<div class="ia-vazio" style="margin-top:6px">'
                     'Sem dado.</div>')
        rot = ls.ROTULO_CENARIO.get(ln.chave, ln.rotulo)
        pecas.append(f'<div class="ia-peca"><div class="ia-peca-rot">'
                     f'{escape(rot)}</div>{corpo}</div>')
    return _caixa(_secao(TITULO_CENARIO)
                  + f'<div class="ia-grade">{"".join(pecas)}</div>'
                  + _nota("A direção (subindo, caindo) é calculada pelos "
                          "números, não é opinião de ninguém."))


def _impacto_fit(a: m.AnaliseAtivo) -> str | None:
    """Leitura de Portfolio Fit já gerada nesta sessão, se houver."""
    prefixo = f"ia_fit_{a.ativo.ticker}_{a.versao_politica}_"
    for k, v in st.session_state.items():
        if str(k).startswith(prefixo):
            textos = getattr(v, "textos", None) or {}
            if textos.get("scenario_impact"):
                return str(textos["scenario_impact"])
    return None


def _abrir_detalhe(ticker: str) -> None:
    st.session_state[SELECAO_KEY] = ticker
    st.session_state[DETALHE_KEY] = True


def _render_ativo(a: m.AnaliseAtivo, ctx: m.ContextoInvestidor,
                  na_carteira: tuple[str, ...],
                  sugerido: rs.AlvoSugerido | None = None) -> None:
    av = av_.avaliar(a)
    d = rs.decisao(a, sugerido, av, na_carteira=na_carteira, ctx=ctx)
    cv = ls.conversar(a, d, sugerido)
    with st.expander(rotulo_expander(a, sugerido, av, d)):
        html = cartao_posicao(a, sugerido, av, d, cv) + cartao_conversa(cv)
        if d.codigo in (rs.VENDER, rs.SUBSTITUIR):
            html += cartao_substitutos(a, rs.substitutos(a, na_carteira))
        html += (cartao_pares(rs.tabela_pares(a)) + cartao_papel(a)
                 + cartao_noticias(a, _noticiario_geral()
                                   if rs.precisa_noticiario_geral(a) else None)
                 + cartao_relatorios(a, _destaques(a.ativo.ticker))
                 + cartao_macro(rs.macro(a, ctx), _impacto_fit(a))
                 + bloco_tecnico(d, av))
        st.markdown(html, unsafe_allow_html=True)
        st.button("Ver análise completa", key=f"ia_resumo_{a.ativo.ticker}",
                  on_click=_abrir_detalhe, args=(a.ativo.ticker,))


def render(analises, ctx: m.ContextoInvestidor,
           politica: dict | None = None) -> None:
    na_carteira = tuple(a.ativo.ticker for a in analises)
    st.markdown(CSS, unsafe_allow_html=True)
    with st.expander(TITULO_CENARIO):
        st.markdown(cartao_cenario(rs.cenario_atual(ctx.cenario)),
                    unsafe_allow_html=True)
    for b in rs.blocos(analises, ctx):
        meta = rs.meta_reserva(politica) if b.chave == rs.RESERVA else None
        st.markdown(cabecalho_grupo(b, meta), unsafe_allow_html=True)
        if b.chave in rs.GRUPOS_EM_LISTA:
            st.markdown(cartao_lista(b), unsafe_allow_html=True)
            continue
        alvos = b.alvos
        for a in b.analises:
            _render_ativo(a, ctx, na_carteira, alvos.get(a.ativo.ticker))

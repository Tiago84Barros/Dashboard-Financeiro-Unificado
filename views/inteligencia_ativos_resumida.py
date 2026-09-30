"""
views/inteligencia_ativos_resumida.py
Página resumida da Inteligência dos Ativos (30/09/2026).

Segue o rascunho do usuário: reserva de emergência e renda fixa numa lista
curta; ações, FIIs e internacional com uma caixa por ativo. Dentro da caixa,
na ordem do rascunho: quanto tem e quanto deveria ter, manter / comprar /
vender, o substituto se for vender, dois pares numa tabela, o papel na
carteira, notícias, relatórios e o que do macro pesa. O detalhe completo
(13 etapas, Portfolio Fit, histórico) segue na aba, sob "Análise detalhada".

Os cartões saem num st.markdown só e só usam tokens var(--app-*).
Dados: core/inteligencia_ativos/resumida.py (puro).
"""
from __future__ import annotations

from html import escape

import streamlit as st

from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import resumida as rs
from core.utils import fmt_moeda

DETALHE_KEY = "ia_detalhe"   # liga a "Análise detalhada" abaixo
SELECAO_KEY = "ia_ativo"     # o mesmo seletor da análise detalhada

_ICONE = {rs.RESERVA: "🛟", rs.RENDA_FIXA: "🏦", rs.ACOES: "📈",
          rs.FIIS: "🏢", rs.EXTERIOR: "🌎", rs.OUTROS: "📦"}
_COR_DECISAO = {rs.COMPRAR: "var(--app-info)",
                rs.VENDER: "var(--app-danger)",
                rs.MANTER: "var(--app-muted)"}

_CAIXA = ('border:1px solid var(--app-border);border-radius:10px;'
          'background:var(--app-surface);padding:10px 14px;margin:6px 0;')
_SUB = ('font-size:0.7rem;font-weight:700;letter-spacing:.05em;'
        'text-transform:uppercase;color:var(--app-subtle);margin:10px 0 4px')
_TD = 'style="padding:3px 12px 3px 0;vertical-align:top;color:var(--app-text)"'
_TH = ('style="padding:3px 12px 3px 0;text-align:left;font-weight:600;'
       'color:var(--app-muted);font-size:0.78rem"')


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1f}%".replace(".", ",")


def _moeda(x: float | None, moeda: str = "BRL") -> str:
    if x is None:
        return "—"
    return fmt_moeda(x) if moeda == "BRL" else f"{moeda} {x:,.2f}"


def _link(texto: str, url: str | None) -> str:
    if url and url.lower().startswith(("http://", "https://")):
        return (f'<a href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer" style="color:var(--app-text)">'
                f'{escape(texto)}</a>')
    return escape(texto)


def _secao(titulo: str) -> str:
    return f'<div style="{_SUB}">{escape(titulo)}</div>'


def _nota(texto: str) -> str:
    return (f'<div style="font-size:0.75rem;color:var(--app-subtle);'
            f'margin-top:4px">{escape(texto)}</div>')


# -- cabeçalho de grupo e lista simples ---------------------------------------------

def cabecalho_grupo(b: rs.Bloco, meta_reserva: str | None = None) -> str:
    """Nome do grupo, valor e % da carteira, e o alvo da classe. Puro."""
    alvo = ""
    if b.alvo_classe is not None:
        if b.chave == rs.RESERVA:
            alvo = (f"conta no alvo da renda fixa ({_pct(b.alvo_classe)}; "
                    f"renda fixa hoje {_pct(b.peso_classe)})")
        elif b.chave == rs.RENDA_FIXA:
            alvo = (f"alvo da renda fixa {_pct(b.alvo_classe)}, reserva "
                    f"incluída; hoje {_pct(b.peso_classe)}")
        else:
            alvo = f"alvo da classe {_pct(b.alvo_classe)}"
    extra = " · ".join(x for x in (alvo, meta_reserva or "") if x)
    return (
        f'<div style="display:flex;justify-content:space-between;'
        f'align-items:baseline;flex-wrap:wrap;gap:6px;margin-top:14px">'
        f'<div style="font-size:1.05rem;font-weight:700;color:var(--app-text)">'
        f'{_ICONE.get(b.chave, "")} {escape(b.rotulo)}</div>'
        f'<div style="color:var(--app-text);font-weight:600">'
        f'{escape(_moeda(b.valor))} · {escape(_pct(b.peso))}</div></div>'
        + (f'<div style="font-size:0.8rem;color:var(--app-subtle)">'
           f'{escape(extra)}</div>' if extra else ""))


def cartao_lista(b: rs.Bloco) -> str:
    """Reserva e renda fixa: cada título com valor e % da carteira. Puro."""
    linhas = "".join(
        f'<tr><td {_TD}><b>{escape(ln.ticker)}</b>'
        f'<div style="font-size:0.78rem;color:var(--app-subtle)">'
        f'{escape(ln.nome or "")}</div></td>'
        f'<td {_TD}>{escape(_moeda(ln.valor, ln.moeda))}</td>'
        f'<td {_TD}>{escape(_pct(ln.peso))}</td></tr>' for ln in b.linhas)
    return (f'<div style="{_CAIXA}"><table style="border-collapse:collapse;'
            f'width:100%;font-size:0.88rem">{linhas}</table></div>')


# -- a caixa de cada ativo ------------------------------------------------------------

def rotulo_expander(a: m.AnaliseAtivo) -> str:
    d = rs.decisao(a)
    return f"{a.ativo.ticker} · {_pct(a.ativo.peso_atual)} · {d.rotulo}"


def cartao_posicao(a: m.AnaliseAtivo) -> str:
    """% atual, % devida e manter / comprar / vender. Puro."""
    d = rs.decisao(a)
    devido = rs.peso_devido(a)
    cor = _COR_DECISAO[d.codigo]

    def _kpi(rotulo, valor, detalhe="", estilo=""):
        return (f'<div style="flex:1;min-width:140px">'
                f'<div style="{_SUB};margin-top:0">{escape(rotulo)}</div>'
                f'<div style="font-size:1.25rem;font-weight:700;'
                f'color:var(--app-text);{estilo}">{escape(valor)}</div>'
                f'<div style="font-size:0.78rem;color:var(--app-subtle)">'
                f'{escape(detalhe)}</div></div>')
    return (
        f'<div style="{_CAIXA}"><div style="display:flex;gap:14px;'
        f'flex-wrap:wrap">'
        + _kpi("Porcentagem atual", _pct(a.ativo.peso_atual),
               _moeda(a.ativo.valor_mercado, a.ativo.moeda))
        + _kpi("Porcentagem devida", devido.valor, devido.detalhe)
        + _kpi("Manter, comprar ou vender", d.rotulo, d.detalhe,
               f"color:{cor}")
        + "</div>"
        + (f'<div style="font-size:0.82rem;color:var(--app-muted);'
           f'margin-top:6px">{escape(d.motivo)}</div>' if d.motivo else "")
        + _nota(rs.AVISO_DECISAO) + "</div>")


def cartao_substitutos(subs: tuple[prs.Par, ...]) -> str:
    """Só aparece quando a leitura é vender. Puro."""
    if not subs:
        corpo = ('<div style="color:var(--app-muted)">Nenhum par do mesmo '
                 'grupo fora da sua carteira para comparar.</div>')
    else:
        corpo = "".join(
            f'<div style="margin:2px 0"><b style="color:var(--app-text)">'
            f'{escape(p.ticker)}</b> <span style="color:var(--app-muted)">'
            f'{escape(p.nome or "")}</span><div style="font-size:0.78rem;'
            f'color:var(--app-subtle)">{escape(p.motivo)}</div></div>'
            for p in subs)
    return (f'<div style="{_CAIXA}">{_secao("Se vender, qual substituir?")}'
            f'{corpo}{_nota(rs.AVISO_SUBSTITUTO)}</div>')


def cartao_pares(t: rs.TabelaPares) -> str:
    """O ativo e dois pares do mesmo segmento, lado a lado. Puro."""
    titulo = "Comparação com o mesmo segmento"
    if not t.linhas:
        return (f'<div style="{_CAIXA}">{_secao(titulo)}<div style="color:'
                f'var(--app-muted)">{escape(t.motivo or "")}</div></div>')
    cab = "".join(f"<th {_TH}>{escape(c)}</th>" for c in ("Ativo", *t.colunas))
    corpo = "".join(
        f"<tr><td {_TD}><b>{escape(tk)}</b></td>"
        + "".join(f"<td {_TD}>{escape(v)}</td>" for v in vals) + "</tr>"
        for tk, vals in t.linhas)
    return (f'<div style="{_CAIXA}">{_secao(f"{titulo} · {t.titulo}")}'
            f'<div style="overflow-x:auto"><table style="border-collapse:'
            f'collapse;font-size:0.85rem"><tr>{cab}</tr>{corpo}</table></div>'
            f'{_nota(prs.RODAPE)}</div>')


def cartao_papel(a: m.AnaliseAtivo) -> str:
    """Papel na carteira e se a tese segue de pé. Puro."""
    papel = a.papel_principal.rotulo if a.papel_principal else "—"
    outros = [p.rotulo for p in a.papeis
              if a.papel_principal is None or p.codigo != a.papel_principal.codigo]
    tese = a.tese
    linhas = [f"<b>Papel:</b> {escape(papel)}"
              + (f' <span style="color:var(--app-subtle)">(também: '
                 f'{escape(", ".join(outros))})</span>' if outros else "")]
    if tese.por_que_esta_na_carteira:
        linhas.append(f"<b>Por que está na carteira:</b> "
                      f"{escape(tese.por_que_esta_na_carteira)}")
    if tese.alinhamento:
        linhas.append(f"<b>Alinhamento com a estratégia:</b> "
                      f"{escape(tese.alinhamento)}")
    return (f'<div style="{_CAIXA}">{_secao("Papel na carteira")}'
            + "".join(f'<div style="margin:2px 0;color:var(--app-text)">'
                      f'{x}</div>' for x in linhas) + "</div>")


def cartao_noticias(a: m.AnaliseAtivo) -> str:
    itens, motivo = rs.noticias(a)
    if not itens:
        corpo = f'<div style="color:var(--app-muted)">{escape(motivo or "")}</div>'
    else:
        corpo = "".join(
            f'<div style="margin:3px 0"><span style="color:var(--app-subtle);'
            f'font-size:0.78rem">{escape(rs.inf._data_br(i.date))} · '
            f'{escape(i.source or "—")}</span><div style="color:var(--app-text)">'
            f'{_link(i.headline, i.url)}</div></div>' for i in itens)
    return f'<div style="{_CAIXA}">{_secao("Notícias")}{corpo}</div>'


def cartao_relatorios(a: m.AnaliseAtivo) -> str:
    docs, motivo = rs.relatorios(a)
    if not docs:
        corpo = f'<div style="color:var(--app-muted)">{escape(motivo or "")}</div>'
    else:
        corpo = "".join(
            f'<div style="margin:3px 0"><span style="color:var(--app-subtle);'
            f'font-size:0.78rem">{escape(rs.inf._data_br(d.reference_date))} · '
            f'{escape(d.rotulo or "")}</span><div style="color:var(--app-text)">'
            f'{_link(d.titulo or d.rotulo or "documento", d.source_url)}</div>'
            f'</div>' for d in docs)
    return (f'<div style="{_CAIXA}">{_secao("Relatórios relevantes")}{corpo}'
            f'{_nota("Documentos oficiais mais recentes. O que dizem sobre o futuro está na análise detalhada.")}'
            f'</div>')


def cartao_macro(mc: rs.Macro, impacto: str | None = None) -> str:
    """Como o macro influencia: canais da classe, o seu cenário e, se já
    gerada, a leitura do Portfolio Fit. Puro."""
    partes = [f'<div style="color:var(--app-text)"><b>O que mais pesa nesta '
              f'classe:</b> {escape(", ".join(mc.canais) or "—")}</div>']
    if mc.sem_cenario:
        partes.append('<div style="color:var(--app-muted)">Nenhum cenário '
                      'cadastrado. Preencha "Meu cenário" abaixo para ver as '
                      'suas premissas aqui.</div>')
    elif mc.premissas:
        partes.append("<div style=\"color:var(--app-text);margin-top:4px\">"
                      "<b>O seu cenário:</b></div><ul style=\"margin:2px 0 0 "
                      "18px;padding:0;color:var(--app-text)\">"
                      + "".join(f"<li>{escape(p)}</li>" for p in mc.premissas)
                      + "</ul>")
    else:
        partes.append('<div style="color:var(--app-muted)">O seu cenário não '
                      'preenche as variáveis desta classe.</div>')
    if mc.sinais:
        partes.append("<ul style=\"margin:4px 0 0 18px;padding:0;"
                      "color:var(--app-warning)\">"
                      + "".join(f"<li>{escape(s)}</li>" for s in mc.sinais)
                      + "</ul>")
    if impacto:
        partes.append(f'<div style="color:var(--app-text);margin-top:6px">'
                      f'<b>Leitura do Portfolio Fit:</b> {escape(impacto)}</div>')
    else:
        partes.append(_nota("A leitura do impacto sai do Portfolio Fit, na "
                            "análise detalhada."))
    return (f'<div style="{_CAIXA}">{_secao("Como o macro influencia")}'
            + "".join(partes) + "</div>")


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
                  na_carteira: tuple[str, ...]) -> None:
    with st.expander(rotulo_expander(a)):
        d = rs.decisao(a)
        html = cartao_posicao(a)
        if d.codigo == rs.VENDER:
            html += cartao_substitutos(rs.substitutos(a, na_carteira))
        html += (cartao_pares(rs.tabela_pares(a)) + cartao_papel(a)
                 + cartao_noticias(a) + cartao_relatorios(a)
                 + cartao_macro(rs.macro(a, ctx), _impacto_fit(a)))
        st.markdown(html, unsafe_allow_html=True)
        st.button("Ver análise completa", key=f"ia_resumo_{a.ativo.ticker}",
                  on_click=_abrir_detalhe, args=(a.ativo.ticker,))


def render(analises, ctx: m.ContextoInvestidor,
           politica: dict | None = None) -> None:
    na_carteira = tuple(a.ativo.ticker for a in analises)
    for b in rs.blocos(analises, ctx):
        meta = rs.meta_reserva(politica) if b.chave == rs.RESERVA else None
        st.markdown(cabecalho_grupo(b, meta), unsafe_allow_html=True)
        if b.chave in rs.GRUPOS_EM_LISTA:
            st.markdown(cartao_lista(b), unsafe_allow_html=True)
            continue
        for a in b.analises:
            _render_ativo(a, ctx, na_carteira)

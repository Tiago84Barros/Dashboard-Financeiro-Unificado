"""
views/inteligencia_ativos_painel.py
Dashboard da aba Inteligência dos Ativos: resumo da carteira, um cartão por
ativo (clique abre a análise completa). O histórico é gravado, não exibido.

As regras estão em ``core/inteligencia_ativos/painel.py`` (o que mostrar) e
``historico.py`` (quando salvar e como comparar); aqui só HTML e Streamlit.
Cada cartão sai num st.markdown só e usa apenas var(--app-*).

Coberto por tests/test_inteligencia_ativos_painel.py.
"""
from __future__ import annotations

import datetime as dt
import logging
from html import escape

import streamlit as st

from core.inteligencia_ativos import calculos as calc
from core.inteligencia_ativos import historico as hist
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import painel as pn
from core.utils import fmt_moeda
from design.lacunas import aviso_lacuna, detalhe_tecnico

logger = logging.getLogger(__name__)

SELECAO_KEY = "ia_ativo"          # mesma chave do selectbox da análise completa
HISTORICO_KEY = "ia_historico"    # (histórico, chaves gravadas nesta sessão)
LLM_GRAVADAS_KEY = "ia_historico_llm"

_COR_ACAO = {
    m.REAVALIAR_TESE: "var(--app-danger)",
    m.REDUZIR_CONCENTRACAO: "var(--app-warning)",
    m.COMPARAR_ALTERNATIVAS: "var(--app-warning)",
    m.REAVALIAR_APORTES: "var(--app-warning)",
    m.APORTE_COMPATIVEL: "var(--app-primary)",
    m.EXPOSICAO_ADEQUADA: "var(--app-primary)",
    m.MANTER: "var(--app-info)",
}
_COR_TESE = {pn.TESE_VALIDA: "var(--app-primary)",
             pn.TESE_EM_RISCO: "var(--app-danger)",
             pn.TESE_SEM_VEREDITO: "var(--app-muted)"}
_COR_STATUS = {calc.ACIMA: "var(--app-danger)", calc.ABAIXO: "var(--app-warning)",
               calc.DENTRO: "var(--app-primary)",
               calc.SEM_REFERENCIA: "var(--app-muted)"}
_COR_SEVERIDADE = {calc.ALTA: "var(--app-danger)", calc.MEDIA: "var(--app-warning)",
                   calc.INFO: "var(--app-info)"}

_ESTILO_ROTULO = ("font-size:0.72rem;font-weight:700;letter-spacing:.05em;"
                  "text-transform:uppercase;color:var(--app-subtle)")
_ROTULO_SECAO = f'<div style="{_ESTILO_ROTULO};margin:0 0 6px 0">'


def _pct(x: float | None, casas: int = 1) -> str:
    return "—" if x is None else f"{x:.{casas}f}%".replace(".", ",")


def _pp(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f} pp".replace(".", ",")


def _moeda(x: float | None, moeda: str = "BRL") -> str:
    if x is None:
        return "—"
    return fmt_moeda(x, "US$" if moeda == "USD" else "R$")


def _data_br(iso: str | None) -> str:
    try:
        return dt.date.fromisoformat((iso or "")[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso or "—"


def _caixa(corpo: str, *, borda: str = "var(--app-border)",
           fundo: str = "var(--app-surface)") -> str:
    return (f'<div style="background:{fundo};border:1px solid var(--app-border);'
            f'border-left:4px solid {borda};border-radius:10px;'
            f'padding:12px 16px;margin:0 0 10px 0;height:100%">{corpo}</div>')


def _kpi(rotulo: str, valor: str, detalhe: str = "") -> str:
    det = (f'<div style="font-size:0.74rem;color:var(--app-subtle)">'
           f'{escape(detalhe)}</div>' if detalhe else "")
    return ('<div style="flex:1 1 150px;min-width:140px;background:'
            'var(--app-surface-raised);border:1px solid var(--app-border);'
            'border-radius:10px;padding:10px 14px">'
            f'<div style="font-size:0.72rem;color:var(--app-muted)">{escape(rotulo)}</div>'
            '<div style="font-size:1.15rem;font-weight:800;color:'
            f'var(--app-kpi-accent)">{escape(valor)}</div>{det}</div>')


def _bloco(titulo: str, corpo: str) -> str:
    return ('<div style="flex:1 1 260px;min-width:0;background:var(--app-surface);'
            'border:1px solid var(--app-border);border-radius:10px;'
            f'padding:10px 14px">{_ROTULO_SECAO}{escape(titulo)}</div>'
            f'<div style="font-size:0.86rem;color:var(--app-text);'
            f'line-height:1.5">{corpo}</div></div>')


def _vazio(texto: str) -> str:
    return f'<div style="color:var(--app-muted)">{escape(texto)}</div>'


# -- resumo da carteira ------------------------------------------------------------

def cartao_resumo(r: pn.ResumoCarteira, comparacao: list[str] | None = None) -> str:
    """O resumo inteiro num bloco só. Puro."""
    if r.rentabilidade_pct is None:
        aviso_lacuna("Rentabilidade da carteira sem cotação suficiente",
                     codigo="tela.inteligencia.rentabilidade_sem_cotacao")
    if r.renda_total is None:
        aviso_lacuna("Renda gerada pela carteira sem proventos disponíveis",
                     codigo="tela.inteligencia.renda_sem_proventos")
    detalhe_tecnico(f"Estratégia versão {r.versao_politica}",
                    codigo="inteligencia.versao_estrategia")
    kpis = "".join([
        _kpi("Patrimônio total", _moeda(r.patrimonio),
             f"investido {_moeda(r.total_investido)}" if r.total_investido else ""),
        _kpi("Rentabilidade", _pct(r.rentabilidade_pct),
             f"resultado {_moeda(r.resultado)}"
             if r.resultado is not None else ""),
        _kpi("Renda gerada (12 meses)", _moeda(r.renda_12m),
             f"desde o início {_moeda(r.renda_total)}"
             if r.renda_total is not None else ""),
        _kpi("Ativos que merecem revisão", str(len(r.revisao))),
    ])

    linhas = "".join(
        '<tr><td style="padding:2px 10px 2px 0;color:var(--app-muted)">'
        f'{escape(ln.rotulo)}</td>'
        f'<td style="padding:2px 10px 2px 0">{_pct(ln.atual)}</td>'
        f'<td style="padding:2px 10px 2px 0;color:var(--app-muted)">{_pct(ln.alvo, 0)}</td>'
        f'<td style="padding:2px 0;color:{_COR_STATUS[ln.status]};font-weight:700">'
        f'{_pp(ln.desvio)}</td></tr>' for ln in r.alocacao)
    alocacao = (
        '<div style="overflow-x:auto"><table style="border-collapse:collapse;'
        'font-size:0.84rem"><tr>' + "".join(
            '<th style="text-align:left;padding:2px 10px 2px 0;font-weight:600;'
            f'color:var(--app-subtle)">{h}</th>'
            for h in ("Classe", "Atual", "Alvo", "Desvio")) + f"</tr>{linhas}</table></div>"
        + (f'<div style="color:var(--app-muted);font-size:0.8rem;margin-top:4px">'
           f'Fora das classes da estratégia: {_pct(r.fora_da_politica)}</div>'
           if r.fora_da_politica else "")) if r.alocacao else _vazio("Sem alocação-alvo.")

    conc = "".join(
        f'<div>{escape(c.dimensao)}: <strong>{escape(c.chave)}</strong> '
        f'{_pct(c.peso)} <span style="color:var(--app-subtle)">da '
        f'{escape(c.base)}</span></div>' for c in r.concentracoes
    ) or _vazio("Sem concentração identificada.")

    premissas = (
        f'<div>Objetivo: <strong>{escape(r.objetivo)}</strong></div>'
        f'<div>Estratégia: <strong>{escape(r.estrategia)}</strong></div>'
        f'<div>Horizonte: {escape(r.horizonte)} · Perfil: {escape(r.perfil)}</div>')

    cor_cen = "var(--app-warning)" if r.cenario_revisao else "var(--app-text)"
    cenario = (f'<div style="color:{cor_cen}">{escape(r.cenario)}</div>'
               + ('<div style="color:var(--app-warning);font-weight:700">Existem '
                  'mudanças relevantes que podem justificar revisão do '
                  'cenário.</div>' if r.cenario_revisao else ""))

    alertas = "".join(
        f'<div style="margin:2px 0"><span style="color:'
        f'{_COR_SEVERIDADE[a.severidade]}">●</span> {escape(a.mensagem)}</div>'
        for a in r.alertas
    ) or '<div style="color:var(--app-primary)">Nenhum limite ou faixa da sua estratégia foi ultrapassado.</div>'

    eventos = "".join(
        f'<div>{_data_br(e.data)} · <strong>{escape(e.ticker)}</strong> — '
        f'{escape(e.rotulo)}</div>' for e in r.eventos
    ) or _vazio("Nenhum evento com data conhecida à frente.")

    revisao = "".join(
        f'<div style="margin:2px 0"><strong>{escape(a.ticker)}</strong> · '
        f'<span style="color:{_COR_ACAO[a.acao]};font-weight:700">'
        f'{escape(a.rotulo)}</span>'
        + (f'<div style="font-size:0.78rem;color:var(--app-muted)">'
           f'{escape(a.motivo)}</div>' if a.motivo else "") + '</div>'
        for a in r.revisao
    ) or '<div style="color:var(--app-primary)">Nenhum ativo pede revisão agora.</div>'

    blocos = "".join([
        _bloco("Alocação atual vs alvo", alocacao),
        _bloco("Concentrações", conc),
        _bloco("Objetivo e estratégia", premissas),
        _bloco("Cenário", cenario),
        _bloco("Alertas relevantes", alertas),
        _bloco("Próximos eventos", eventos),
        _bloco("Ativos que merecem revisão", revisao),
    ])
    if comparacao:
        blocos += _bloco("Desde a última análise",
                         "".join(f"<div>• {escape(f)}</div>" for f in comparacao))
    return (
        '<div style="background:var(--app-surface-raised);border:1px solid '
        'var(--app-border);border-radius:12px;padding:14px 16px;margin:6px 0 14px 0">'
        '<div style="font-size:0.72rem;font-weight:700;letter-spacing:.05em;'
        'text-transform:uppercase;color:var(--app-info);margin-bottom:10px">'
        'Resumo da carteira</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:10px">{kpis}</div>'
        '<div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:10px">'
        f'{blocos}</div></div>')


# -- cartões dos ativos ---------------------------------------------------------------

def cartao_ativo(c: pn.CardAtivo) -> str:
    """Um ativo, com os dez campos do painel. Puro."""
    cor = _COR_ACAO[c.acao]

    def _campo(rotulo: str, valor: str, cor_valor: str = "var(--app-text)") -> str:
        return ('<div style="margin:3px 0"><span style="font-size:0.72rem;'
                f'color:var(--app-muted)">{escape(rotulo)}</span><div style="'
                f'color:{cor_valor};font-size:0.86rem">{escape(valor)}</div></div>')

    corpo = (
        '<div style="display:flex;justify-content:space-between;gap:8px;'
        'align-items:baseline"><div style="font-size:1.1rem;font-weight:800;'
        f'color:var(--app-text)">{escape(c.ticker)}</div>'
        f'<div style="font-weight:700;color:var(--app-kpi-accent)">'
        f'{_moeda(c.valor, c.moeda)}</div></div>'
        f'<div style="font-size:0.78rem;color:var(--app-subtle);margin-bottom:4px">'
        f'{escape(c.nome)}</div>'
        '<div style="display:flex;gap:16px;flex-wrap:wrap">'
        + _campo("Peso na carteira", _pct(c.peso))
        + _campo("Target / faixa", c.faixa) + '</div>'
        + _campo("Papel", c.papel)
        + _campo("Status da tese", c.tese_rotulo, _COR_TESE[c.tese])
        + _campo("Valuation", c.valuation)
        + _campo("Principal risco", c.risco,
                 "var(--app-text)" if c.risco != pn.SEM_RISCO else "var(--app-muted)")
        + _campo("Próximo evento", c.evento)
        + '<div style="margin-top:6px;font-size:0.72rem;color:var(--app-muted)">'
        'Ação a considerar</div>'
        f'<div style="font-weight:800;color:{cor}">{escape(c.acao_rotulo)}</div>')
    return _caixa(corpo, borda=cor)


def _abrir(ticker: str) -> None:
    """Callback: escolhe o ativo antes de o selectbox ser desenhado."""
    st.session_state[SELECAO_KEY] = ticker


def render_cards(cards: list[pn.CardAtivo], *, colunas: int = 3) -> None:
    for inicio in range(0, len(cards), colunas):
        cols = st.columns(colunas)
        for col, c in zip(cols, cards[inicio:inicio + colunas]):
            with col:
                st.markdown(cartao_ativo(c), unsafe_allow_html=True)
                st.button("Abrir análise completa", key=f"ia_abrir_{c.ticker}",
                          on_click=_abrir, args=(c.ticker,),
                          use_container_width=True)


# -- histórico (gravado, não exibido) ----------------------------------------------------------

def anterior(lista: list[hist.Snapshot], gravada_agora: bool) -> hist.Snapshot | None:
    """A foto com que a análise de agora se compara: se a de agora acabou de
    ser gravada, é a penúltima; senão, a última."""
    if gravada_agora:
        return lista[-2] if len(lista) >= 2 else None
    return lista[-1] if lista else None


def _estado() -> tuple[dict, set]:
    historico, gravadas = st.session_state.get(HISTORICO_KEY) or ({}, set())
    return historico, gravadas


def registrar(fotos, *, forcar: str | None = None) -> None:
    """Grava pelo repositório e guarda o resultado na sessão. Falha de banco
    vira aviso no log, nunca erro na tela: o histórico é acessório."""
    from core.inteligencia_ativos import historico_repo as repo

    historico, gravadas = _estado()
    try:
        historico, novas = repo.registrar(fotos, forcar=forcar)
    except Exception:  # noqa: BLE001
        logger.warning("inteligencia_ativos: histórico não gravado", exc_info=True)
        return
    st.session_state[HISTORICO_KEY] = (historico, gravadas | set(novas))


def registrar_uma_vez(ctx: m.ContextoInvestidor, analises,
                      resumo: pn.ResumoCarteira) -> None:
    """Fotos automáticas: uma tentativa por sessão. O repositório decide, pela
    regra de mudança material, o que de fato entra."""
    if HISTORICO_KEY in st.session_state:
        return
    agora = dt.datetime.now(dt.timezone.utc)
    st.session_state[HISTORICO_KEY] = ({}, set())
    registrar([hist.capturar(a, ctx, agora=agora) for a in analises]
              + [hist.capturar_carteira(resumo, ctx, agora=agora)])


def comparacao_de(ticker: str, atual: hist.Snapshot) -> tuple[list, list[str]]:
    historico, gravadas = _estado()
    lista = historico.get(ticker, [])
    return lista, hist.comparar(anterior(lista, ticker in gravadas), atual)


def registrar_leitura_llm(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor,
                          leitura=None) -> None:
    """Leitura por LLM nova gera foto com o modelo que respondeu. O histórico
    fica no banco para comparar análises; a tela não mostra a trilha."""
    if leitura is None:
        return
    ticker = analise.ativo.ticker
    feitas = st.session_state.setdefault(LLM_GRAVADAS_KEY, set())
    marca = (ticker, id(leitura))
    if marca in feitas:
        return
    feitas.add(marca)
    modelo = getattr(leitura, "modelo", None)
    registrar([hist.capturar(analise, ctx, agora=dt.datetime.now(dt.timezone.utc),
                             modelo=modelo or "LLM (modelo não informado)")],
              forcar=hist.LEITURA_LLM)

"""
views/inteligencia_ativos.py
Aba "Inteligência dos Ativos" de Investimentos.

Depende da Estratégia de Investimentos, que se configura aqui mesmo. Sem uma
estratégia concluída, a aba continua visível (com 🔒 no rótulo) e mostra o que
falta, quanto já foi feito e um botão que abre a configuração logo abaixo, na
própria aba. Não é erro nem página vazia; é a etapa que falta para a análise
ser do usuário, e não genérica. Liberada, a aba termina com "Minha
estratégia", onde a configuração pode ser alterada depois.

Não há mais "Meu cenário" (removido em 30/09/2026): o ambiente econômico é
lido das séries do banco por ``core/cenario/automatico.py`` e aparece no topo
do painel, sem pergunta ao usuário.

Até 27/09/2026 a estratégia e o cenário moravam em Configurações → Geral (e o
botão navegava para lá). Mudou para que tudo aconteça na mesma aba.

A decisão de liberar é de ``core/estrategia/portao.py``; a análise passa por
``core/inteligencia_ativos.py``, que pergunta ao portão de novo. Esta tela
não decide nada sozinha.

Liberada, a aba é um painel, de cima para baixo: resumo da carteira,
cartões dos ativos (o botão de cada um abre a análise completa), análise
completa e Portfolio Fit do ativo escolhido, e o histórico com a auditoria de
cada foto. A carteira recomendada do Portfólio Global aparece como referência
comparativa (``core/inteligencia_ativos/referencia_modelo.py``), em cartão
próprio, fora das 13 etapas e da "Ação a considerar". Os cartões vêm de ``views/inteligencia_ativos_painel.py``; os
números, de ``core/inteligencia_ativos/painel.py`` e ``historico.py``.

Coberto por tests/test_inteligencia_ativos_tela.py e
tests/test_inteligencia_ativos_painel.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from html import escape

import streamlit as st

from core import inteligencia_ativos as servico
from core.estrategia import politica as pol
from core.estrategia import portao
from core.inteligencia_ativos import calculos as calc
from core.inteligencia_ativos import fundamentos as fund
from core.inteligencia_ativos import historico as hist
from core.inteligencia_ativos import informacoes as inf
from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import painel, papeis
from core.inteligencia_ativos import pares as prs
from core.inteligencia_ativos import referencia_modelo as refm
from core.inteligencia_ativos import valuation as val
from core.utils import fmt_moeda
from views import configuracoes_estrategia as tela_estrategia
from views import inteligencia_ativos_fit as tela_fit
from views import inteligencia_ativos_painel as tela_painel
from views import inteligencia_ativos_resumida as tela_resumida

ROTULO = "Inteligência dos Ativos"
# Configuração aberta na própria aba (onboarding ou "Minha estratégia").
ESTRATEGIA_ABERTA = "ia_estrategia_aberta"

_BOTAO = {
    pol.NOT_STARTED: "Configurar minha estratégia",
    pol.IN_PROGRESS: "Continuar configuração",
    pol.NEEDS_REVIEW: "Revisar minha estratégia",
}


def rotulo_aba(liberacao: portao.Liberacao) -> str:
    icone = "🧠" if liberacao.disponivel else "🔒"
    return f"{icone}  {ROTULO}"


def render(liberacao: portao.Liberacao, carteira: dict | None = None,
           proventos: dict | None = None) -> None:
    if liberacao.disponivel:
        _render_liberada(liberacao, carteira or {}, proventos)
    else:
        _render_onboarding(liberacao)


# -- bloqueada: onboarding -----------------------------------------------------

def _titulo_e_texto(liberacao: portao.Liberacao) -> tuple[str, str]:
    if liberacao.motivo == portao.REVISAO_NECESSARIA:
        return ("Revise sua estratégia para liberar esta análise",
                "Sua estratégia de investimentos precisa de uma revisão antes "
                "de voltar a orientar a análise dos ativos. Confirme ou ajuste "
                "as respostas e conclua de novo.")
    return ("Configure sua estratégia para liberar esta análise",
            "Antes de analisar individualmente os ativos da sua carteira, "
            "precisamos entender o que você pretende construir com seus "
            "investimentos.<br><br>Essas informações permitem que a "
            "inteligência do sistema avalie cada ativo dentro do contexto da "
            "sua carteira, dos seus objetivos e do seu horizonte de "
            "investimento.")


def checklist(liberacao: portao.Liberacao) -> list[tuple[str, bool]]:
    """(campo mínimo, já respondido) na ordem do roteiro. Puro."""
    faltantes = set(liberacao.faltantes)
    return [(c, c not in faltantes) for c in pol.OBRIGATORIOS]


def cartao_onboarding(liberacao: portao.Liberacao) -> str:
    """HTML do cartão de onboarding, num bloco só. Puro."""
    titulo, texto = _titulo_e_texto(liberacao)
    itens = ""
    for chave, feito in checklist(liberacao):
        marca, cor = ("✓", "var(--app-primary)") if feito else ("○", "var(--app-muted)")
        itens += (f'<li style="list-style:none;margin:4px 0;color:{cor}">'
                  f'{marca} {escape(pol.POR_CHAVE[chave].rotulo)}</li>')
    return (
        '<div style="background:var(--app-surface);'
        'border:1px solid var(--app-border);border-radius:12px;'
        'padding:20px 22px;margin:8px 0 14px 0;">'
        '<div style="font-size:0.78rem;font-weight:600;letter-spacing:.04em;'
        'text-transform:uppercase;color:var(--app-info)">'
        'Falta uma etapa para personalizar suas análises</div>'
        '<div style="font-size:1.25rem;font-weight:800;color:var(--app-text);'
        f'margin:6px 0 10px 0">{escape(titulo)}</div>'
        '<div style="font-size:0.92rem;color:var(--app-muted);'
        f'line-height:1.5">{texto}</div>'
        '<div style="font-size:0.88rem;color:var(--app-text);'
        'margin-top:14px">📍 Onde resolver: <strong>aqui mesmo</strong>, na '
        'configuração logo abaixo. Nada muda de página.</div>'
        '<div style="font-size:0.88rem;font-weight:600;color:var(--app-text);'
        'margin-top:14px">Para liberar a análise:</div>'
        f'<ul style="padding-left:4px;margin:6px 0 0 0">{itens}</ul>'
        '<div style="font-size:0.82rem;color:var(--app-subtle);'
        'margin-top:12px">O que será liberado: a leitura de cada ativo da '
        'carteira à luz do seu objetivo, horizonte, risco e alocação '
        'desejada.</div>'
        '</div>'
    )


def _render_onboarding(liberacao: portao.Liberacao) -> None:
    if liberacao.motivo == portao.ESTRATEGIA_INDISPONIVEL:
        st.info("Não foi possível ler sua estratégia de investimentos agora. "
                "A análise dos ativos depende dela; tente de novo em "
                "instantes. Se persistir, o administrador precisa conferir a "
                "tabela da estratégia (migration 076).")
        return
    st.markdown(cartao_onboarding(liberacao), unsafe_allow_html=True)
    if not st.session_state.get(ESTRATEGIA_ABERTA):
        st.progress(liberacao.pct / 100, text="Configuração da estratégia: "
                                              f"{liberacao.pct:.0f}% concluída")
        if st.button(_BOTAO.get(liberacao.status, _BOTAO[pol.NOT_STARTED]),
                     key="ia_abrir_estrategia", type="primary"):
            st.session_state[ESTRATEGIA_ABERTA] = True
            if liberacao.status == pol.NOT_STARTED:
                tela_estrategia.iniciar()      # abre o rascunho e reexecuta
            st.rerun()
        return
    # Aberta, a configuração mostra o próprio progresso: o da aba sai.
    # Concluir reexecuta o app, e o portão reavaliado libera a análise.
    with st.container(border=True, key="ia_estrategia_onboarding"):
        st.markdown("##### 🎯 Estratégia de Investimentos")
        tela_estrategia.render()


def _render_minha_estrategia() -> None:
    """Fim da aba liberada: a estratégia vigente, e como alterá-la."""
    st.markdown("#### Minha estratégia")
    st.caption("A premissa de toda a análise acima. Alterar abre uma nova "
               "versão; a atual continua valendo até você concluir a edição.")
    with st.expander("✏️ Ver ou alterar minha estratégia",
                     expanded=bool(st.session_state.get(ESTRATEGIA_ABERTA))):
        tela_estrategia.render()


# -- liberada ------------------------------------------------------------------
# Cada cartão sai num st.markdown só e usa apenas var(--app-*): assim o tema
# claro alcança tudo.

_COR_ACAO = {
    m.REAVALIAR_TESE: "var(--app-danger)",
    m.REDUZIR_CONCENTRACAO: "var(--app-warning)",
    m.COMPARAR_ALTERNATIVAS: "var(--app-warning)",
    m.REAVALIAR_APORTES: "var(--app-warning)",
    m.APORTE_COMPATIVEL: "var(--app-primary)",
    m.EXPOSICAO_ADEQUADA: "var(--app-primary)",
    m.MANTER: "var(--app-info)",
}

_ROTULO_CLASSE = dict(pol.CLASSES)
_SETA = ('<div style="text-align:center;color:var(--app-subtle);'
         'font-size:0.8rem;line-height:1.2">↓</div>')


def _pct(x: float | None, casas: int = 1) -> str:
    return "—" if x is None else f"{x:.{casas}f}%".replace(".", ",")


def _pp(x: float | None) -> str:
    return "—" if x is None else f"{x:+.1f} pp".replace(".", ",")


def _moeda(x: float | None, moeda: str = "BRL") -> str:
    if x is None:
        return "—"
    return fmt_moeda(x, "US$" if moeda == "USD" else "R$")


def _cartao(num: int, titulo: str, corpo: str, *, pendente: bool = False,
            destaque: str | None = None) -> str:
    borda = destaque or "var(--app-border)"
    chip = ('<span style="margin-left:8px;font-size:0.7rem;font-weight:600;'
            'padding:2px 8px;border-radius:999px;border:1px solid '
            'var(--app-border);color:var(--app-subtle)">em preparação</span>'
            if pendente else "")
    opacidade = "opacity:.75;" if pendente else ""
    return (
        f'<div style="background:var(--app-surface);border:1px solid {borda};'
        f'border-left:4px solid {borda};border-radius:10px;padding:12px 16px;'
        f'{opacidade}">'
        '<div style="font-size:0.72rem;font-weight:700;letter-spacing:.05em;'
        f'text-transform:uppercase;color:var(--app-subtle)">{num:02d} · '
        f'{escape(titulo)}{chip}</div>'
        '<div style="font-size:0.9rem;color:var(--app-text);margin-top:6px;'
        f'line-height:1.5">{corpo}</div></div>'
    )


def _grade(pares: list[tuple[str, str]]) -> str:
    itens = "".join(
        '<div style="min-width:140px"><div style="font-size:0.72rem;'
        f'color:var(--app-muted)">{escape(r)}</div><div style="font-weight:'
        f'700;color:var(--app-text)">{escape(v)}</div></div>'
        for r, v in pares)
    return f'<div style="display:flex;flex-wrap:wrap;gap:12px 24px">{itens}</div>'


def _lista(itens) -> str:
    return "".join(f'<div style="margin:2px 0">• {escape(i)}</div>'
                   for i in itens)


def cartao_premissa(ctx: m.ContextoInvestidor) -> str:
    """A estratégia que orienta todas as análises, no topo da aba. Puro."""
    def _rot(chave: str, valor) -> str:
        return pol.formatar(chave, valor) if valor is not None else "—"
    linhas = "".join(
        '<tr><td style="padding:2px 12px 2px 0;color:var(--app-muted)">'
        f'{escape(rot)}</td><td style="padding:2px 12px 2px 0;'
        f'color:var(--app-text)">{_pct(ctx.peso_por_classe.get(c))}</td>'
        '<td style="color:var(--app-muted)">alvo '
        f'{_pct(ctx.alocacao_alvo.get(c), 0)}</td></tr>'
        for c, rot in pol.CLASSES)
    fora = ('<div style="color:var(--app-muted);margin-top:4px">Fora das '
            f'classes da política: {_pct(ctx.peso_fora_da_politica)}</div>'
            if ctx.peso_fora_da_politica else "")
    grade = _grade([
        ("Objetivo", _rot("objective", ctx.objetivo)),
        ("Horizonte", _rot("time_horizon", ctx.horizonte)),
        ("Perfil de risco", _rot("risk_profile", ctx.perfil_risco)),
        ("Estratégia", _rot("predominant_strategy", ctx.estrategia)),
        ("Limite por ativo", _pct(ctx.limite_por_ativo, 0)),
        ("Limite por setor", _pct(ctx.limite_por_setor, 0)),
    ])
    return (
        '<div style="background:var(--app-surface-raised);border:1px solid '
        'var(--app-border);border-radius:12px;padding:14px 18px;'
        'margin:6px 0 14px 0">'
        '<div style="font-size:0.72rem;font-weight:700;letter-spacing:.05em;'
        'text-transform:uppercase;color:var(--app-info)">Premissa de toda '
        f'análise · estratégia versão {ctx.versao_politica}</div>'
        f'<div style="margin-top:8px">{grade}</div>'
        '<table style="margin-top:10px;font-size:0.86rem;'
        f'border-collapse:collapse">{linhas}</table>{fora}</div>'
    )


_COR_SEVERIDADE = {calc.ALTA: "var(--app-danger)",
                   calc.MEDIA: "var(--app-warning)",
                   calc.INFO: "var(--app-info)"}
_COR_STATUS = {calc.ACIMA: "var(--app-danger)", calc.ABAIXO: "var(--app-warning)",
               calc.DENTRO: "var(--app-primary)",
               calc.SEM_REFERENCIA: "var(--app-muted)"}
_ROTULO_STATUS = {calc.ACIMA: "acima", calc.ABAIXO: "abaixo",
                  calc.DENTRO: "na faixa", calc.SEM_REFERENCIA: "sem alvo"}


def _faixa_txt(f: calc.Faixa) -> str:
    if f.vazia:
        return "—"
    return f"{_pct(f.piso)} a {_pct(f.teto)}"


def cartao_calculos(c: calc.Calculos) -> str:
    """Alocação vs alvo, concentração e alertas: só números do código. Puro."""
    cel = 'style="padding:3px 10px 3px 0;'
    th = ('<th style="text-align:left;padding:3px 10px 3px 0;font-weight:600;'
          'color:var(--app-muted)">')
    linhas = ""
    for a in c.alocacao[calc.DIM_CLASSE]:
        cor = _COR_STATUS[a.status]
        linhas += (
            f'<tr><td {cel}color:var(--app-text)">{escape(a.rotulo)}</td>'
            f'<td {cel}color:var(--app-text)">{_pct(a.atual)}</td>'
            f'<td {cel}color:var(--app-muted)">{_pct(a.faixa.alvo, 0)}</td>'
            f'<td {cel}color:var(--app-muted)">{_faixa_txt(a.faixa)}</td>'
            f'<td {cel}color:var(--app-text)">{_pp(a.diferenca_para_alvo)}</td>'
            f'<td {cel}color:var(--app-text)">{_pp(a.overweight)}</td>'
            f'<td {cel}color:var(--app-text)">{_pp(a.underweight)}</td>'
            f'<td {cel}color:{cor};font-weight:700">'
            f'{_ROTULO_STATUS[a.status]}</td></tr>')
    tabela = (
        '<table style="font-size:0.84rem;border-collapse:collapse;'
        'margin-top:6px"><tr>' + "".join(
            f"{th}{h}</th>" for h in ("Classe", "Atual", "Alvo", "Faixa",
                                      "Dif. alvo", "Overweight",
                                      "Underweight", "Status"))
        + f"</tr>{linhas}</table>")

    conc = ""
    for dim in calc.DIMENSOES_CONCENTRACAO:
        k = c.concentracao[dim]
        if k.peso_da_base <= 0:
            valor, detalhe = "—", f"sem posições em {k.base}"
        else:
            maior = k.maior
            valor = (f"{escape(maior.chave)} {_pct(maior.peso)}" if maior
                     else "não identificado")
            hhi = ("HHI —" if k.hhi is None else
                   f"HHI {k.hhi:.2f} · ≈ {k.numero_efetivo:.1f} iguais"
                   .replace(".", ","))
            detalhe = f"{hhi} · base {k.base}"
            if k.cobertura < 99.95:
                detalhe += f" · identificado {_pct(k.cobertura)}"
        conc += (
            '<div style="min-width:170px;flex:1 1 170px"><div style="font-size:'
            f'0.72rem;color:var(--app-muted)">{calc.ROTULO_DIMENSAO[dim]}</div>'
            f'<div style="font-weight:700;color:var(--app-text)">{valor}</div>'
            '<div style="font-size:0.74rem;color:var(--app-subtle)">'
            f'{escape(detalhe)}</div></div>')

    if c.alertas:
        alertas = "".join(
            f'<div style="margin:3px 0;color:{_COR_SEVERIDADE[a.severidade]}">'
            f'● <span style="color:var(--app-text)">{escape(a.mensagem)}'
            '</span></div>' for a in c.alertas)
    else:
        alertas = ('<div style="color:var(--app-primary)">Nenhum limite ou '
                   'faixa da sua estratégia foi ultrapassado.</div>')

    sub_t = ('<div style="font-size:0.72rem;font-weight:700;letter-spacing:.05em;'
             'text-transform:uppercase;color:var(--app-subtle);margin-top:12px">')
    return (
        '<div style="background:var(--app-surface);border:1px solid '
        'var(--app-border);border-radius:12px;padding:14px 18px;'
        'margin:0 0 14px 0">'
        '<div style="font-size:0.72rem;font-weight:700;letter-spacing:.05em;'
        'text-transform:uppercase;color:var(--app-info)">Cálculos da carteira'
        ' · feitos pelo sistema, não pela IA</div>'
        f'{sub_t}Alertas objetivos</div>'
        f'<div style="font-size:0.88rem;margin-top:4px">{alertas}</div>'
        f'{sub_t}Alocação atual vs alvo</div>{tabela}'
        f'{sub_t}Concentração</div>'
        '<div style="display:flex;flex-wrap:wrap;gap:10px 20px;'
        f'margin-top:6px">{conc}</div></div>')


def _gatilhos(tese: m.Tese) -> str:
    saida = ""
    for g in tese.gatilhos:
        if g.disparado:
            marca, cor = "⚠", "var(--app-danger)"
        elif g.disparado is None:
            marca, cor = "○", "var(--app-muted)"
        else:
            marca, cor = "✓", "var(--app-primary)"
        saida += (f'<div style="margin:2px 0;color:{cor}">{marca} '
                  f'{escape(g.descricao)} — {escape(g.detalhe)}</div>')
    return saida


_VALIDADE = {
    True: "Sim.",
    False: "Há sinal de que não: veja os gatilhos abaixo.",
    None: "Ainda sem veredito: depende das etapas de fundamentos e "
          "relatórios.",
}


def corpo_fundamentos(f: fund.Fundamentos) -> str:
    """DADO (tabela do sistema) separado de INTERPRETAÇÃO (da LLM). Puro."""
    if f.tipo is None:
        return escape(f.motivo or fund.NAO_DISPONIVEL)
    sub_t = ('<div style="font-size:0.7rem;font-weight:700;letter-spacing:.05em;'
             'text-transform:uppercase;color:var(--app-subtle);margin-top:8px">')
    cel = 'style="padding:2px 10px 2px 0;vertical-align:top;'
    linhas = ""
    for i in f.indicadores:
        cor = "var(--app-text)" if i.disponivel else "var(--app-subtle)"
        origem = " · ".join(x for x in (i.referencia, i.nota) if x)
        linhas += (
            f'<tr><td {cel}color:var(--app-muted)">{escape(i.rotulo)}</td>'
            f'<td {cel}color:{cor};font-weight:600">'
            f'{escape(i.texto(f.moeda))}</td>'
            f'<td {cel}color:var(--app-subtle);font-size:0.76rem">'
            f'{escape(origem)}</td></tr>')
    fontes = "; ".join(f.fontes) or "nenhuma fonte com dado para este ativo"
    return (
        f'<div>{escape(fund.ROTULO_TIPO[f.tipo])}: indicadores próprios da '
        f'classe ({len(f.disponiveis)} de {len(f.indicadores)} com dado).</div>'
        f'{sub_t}Dado · fornecido pelo sistema</div>'
        '<table style="font-size:0.84rem;border-collapse:collapse;'
        f'margin-top:4px">{linhas}</table>'
        '<div style="font-size:0.76rem;color:var(--app-subtle);margin-top:4px">'
        f'Fontes: {escape(fontes)}</div>'
        f'{sub_t}Interpretação</div>'
        '<div style="font-size:0.84rem;color:var(--app-muted)">Cabe à análise '
        'por LLM, usando só o bloco de dados acima; o que estiver como '
        f'"{escape(fund.NAO_DISPONIVEL)}" não é estimado.</div>')


_SUB_T = ('<div style="font-size:0.7rem;font-weight:700;letter-spacing:.05em;'
          'text-transform:uppercase;color:var(--app-subtle);margin-top:8px">')
_TH = ('style="padding:2px 10px 2px 0;text-align:left;font-weight:600;'
       'color:var(--app-subtle);font-size:0.74rem;vertical-align:bottom"')
_TD = 'style="padding:2px 10px 2px 0;vertical-align:top;'


def _tabela(cabecalho: list[str], corpo: str) -> str:
    """Tabela com rolagem horizontal própria: no celular não empurra a página."""
    ths = "".join(f"<th {_TH}>{escape(c)}</th>" for c in cabecalho)
    return ('<div style="overflow-x:auto;margin-top:4px">'
            '<table style="font-size:0.84rem;border-collapse:collapse">'
            f"<thead><tr>{ths}</tr></thead><tbody>{corpo}</tbody></table></div>")


def _nota(texto: str) -> str:
    return ('<div style="font-size:0.76rem;color:var(--app-subtle);'
            f'margin-top:4px">{escape(texto)}</div>')


def corpo_valuation(v: val.Valuation) -> str:
    """Valor atual, histórico, pares, faixas e premissas (DADO) separados das
    frases de comparação (INTERPRETAÇÃO). Nunca "barato"/"caro". Puro."""
    if v.tipo is None or not v.linhas:
        return escape(v.motivo or fund.NAO_DISPONIVEL)
    fmt = lambda x, ln: fund.formatar(x, ln.unidade, v.moeda)  # noqa: E731
    linhas, faixas, leituras = "", "", ""
    for ln in v.linhas:
        if not ln.aplicavel:
            linhas += (f'<tr><td {_TD}color:var(--app-muted)">{escape(ln.rotulo)}'
                       f'</td><td {_TD}color:var(--app-subtle)" colspan="4">'
                       f'Não se aplica: {escape(ln.motivo or "")}</td></tr>')
            continue
        h, p = ln.historico, ln.pares
        cor = "var(--app-text)" if ln.atual is not None else "var(--app-subtle)"
        hist = (f"{fmt(h.media, ln)} / {fmt(h.mediana, ln)} "
                f"({h.n} obs., {h.inicio}–{h.fim})" if h else fund.NAO_DISPONIVEL)
        par = (f"{fmt(p.mediana, ln)} ({p.n})" if p else fund.NAO_DISPONIVEL)
        origem = " · ".join(x for x in (ln.referencia, ln.fonte) if x)
        linhas += (
            f'<tr><td {_TD}color:var(--app-muted)">{escape(ln.rotulo)}</td>'
            f'<td {_TD}color:{cor};font-weight:600">'
            f'{escape(ln.texto_atual(v.moeda))}</td>'
            f'<td {_TD}color:var(--app-text)">{escape(hist)}</td>'
            f'<td {_TD}color:var(--app-text)">{escape(par)}</td>'
            f'<td {_TD}color:var(--app-subtle);font-size:0.76rem">'
            f'{escape(origem)}</td></tr>')
        for fx in ln.faixas:
            faixas += (f"<li>{escape(ln.rotulo)} — {escape(fx.rotulo)}: "
                       f"{escape(fmt(fx.minimo, ln))} a "
                       f"{escape(fmt(fx.maximo, ln))}</li>")
        if ln.atual is not None:
            leituras += (f"<li>{escape(ln.comparacao_historica)}</li>"
                         f"<li>{escape(ln.comparacao_pares)}</li>")
    lista = ('<ul style="margin:2px 0 0 18px;padding:0;font-size:0.82rem;'
             'color:var(--app-muted)">')
    return (
        f'<div>{escape(fund.ROTULO_TIPO[v.tipo])}: métricas de valuation que '
        f'fazem sentido para a classe ({len(v.com_dado)} de {len(v.linhas)} '
        'com dado).</div>'
        f'{_SUB_T}Dado · fornecido pelo sistema</div>'
        + _tabela(["Métrica", "Atual", "Histórico: média / mediana",
                   "Mediana dos pares (n)", "Referência"], linhas)
        + (f'{_SUB_T}Faixas de referência (observadas, não alvo)</div>'
           f'{lista}{faixas}</ul>' if faixas else "")
        + f'{_SUB_T}Premissas</div>{lista}'
        + "".join(f"<li>{escape(x)}</li>" for x in v.premissas) + "</ul>"
        + f'{_SUB_T}Interpretação · comparação, não veredito</div>'
        + (f'{lista}{leituras}</ul>' if leituras
           else _nota("Sem valor atual para comparar."))
        + _nota(val.AVISO))


def corpo_pares(c: prs.ComparacaoPares) -> str:
    """Grupo escolhido por regra e a tabela Ativo | Métrica | Valor | Mediana
    dos pares | Diferença | Interpretação. Puro."""
    g = c.grupo
    if not g.pares:
        return escape(c.motivo or g.motivo or fund.NAO_DISPONIVEL)
    lista = ('<ul style="margin:2px 0 0 18px;padding:0;font-size:0.82rem;'
             'color:var(--app-muted)">')
    pares_li = "".join(
        f"<li><strong>{escape(p.ticker)}</strong>"
        f"{' — ' + escape(p.nome) if p.nome else ''}: {escape(p.motivo)}</li>"
        for p in g.pares)
    linhas = ""
    for ln in c.linhas:
        cor = "var(--app-text)" if ln.valor is not None else "var(--app-subtle)"
        linhas += (
            f'<tr><td {_TD}color:var(--app-muted)">{escape(c.ativo)}</td>'
            f'<td {_TD}color:var(--app-muted)">{escape(ln.metrica)}</td>'
            f'<td {_TD}color:{cor};font-weight:600">'
            f'{escape(ln.texto_valor(c.moeda))}</td>'
            f'<td {_TD}color:var(--app-text)">'
            f'{escape(ln.texto_mediana(c.moeda))}'
            f'{f" ({ln.n_pares})" if ln.n_pares else ""}</td>'
            f'<td {_TD}color:var(--app-text)">'
            f'{escape(ln.texto_diferenca(c.moeda))}</td>'
            f'<td {_TD}color:var(--app-muted);font-size:0.8rem">'
            f'{escape(ln.interpretacao)}</td></tr>')
    return (
        f'<div>{escape(g.descricao or "")}.</div>'
        f'{_SUB_T}Como o grupo foi escolhido</div>{lista}'
        + "".join(f"<li>{escape(x)}</li>" for x in g.criterios + g.relaxamentos)
        + f'</ul>{_SUB_T}Pares</div>{lista}{pares_li}</ul>'
        f'{_SUB_T}Dado · comparação</div>'
        + _tabela(["Ativo", "Métrica", "Valor", "Mediana dos pares (n)",
                   "Diferença", "Interpretação"], linhas)
        + _nota(prs.RODAPE))


_COR_NIVEL = {inf.HIGH: "var(--app-danger)", inf.MEDIUM: "var(--app-warning)",
              inf.LOW: "var(--app-subtle)"}


def _nivel(nivel: str) -> str:
    return (f'<span style="font-weight:700;color:{_COR_NIVEL.get(nivel, "var(--app-subtle)")}">'
            f'{escape(inf.ROTULO_NIVEL.get(nivel, nivel))}</span>')


def _link(texto: str, url: str | None) -> str:
    """Link só para http(s); qualquer outra coisa vira texto."""
    if url and url.lower().startswith(("http://", "https://")):
        return (f'<a href="{escape(url, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer" style="color:var(--app-text)">'
                f'{escape(texto)}</a>')
    return escape(texto)


def _data(iso: str | None) -> str:
    return inf._data_br(iso)


def corpo_noticias(n: inf.Noticias) -> str:
    """Tabela Data | Impacto | Dimensões | Manchete | Fonte, o que o filtro
    descartou e o aviso de que o nível vem da manchete. Puro."""
    if not n.itens:
        return escape(n.motivo or inf.NAO_DISPONIVEL)
    linhas = ""
    for i in n.itens:
        dims = ", ".join(inf.ROTULO_DIMENSAO.get(d, d) for d in i.affected_dimension)
        resumo = (f'<div style="font-size:0.78rem;color:var(--app-subtle)">'
                  f'{escape(i.summary)}</div>' if i.summary else "")
        linhas += (
            f'<tr><td {_TD}color:var(--app-muted);white-space:nowrap">'
            f'{_data(i.date)}</td>'
            f'<td {_TD}">{_nivel(i.impact_level)}</td>'
            f'<td {_TD}color:var(--app-muted);font-size:0.8rem">{escape(dims)}'
            f'<div style="color:var(--app-subtle)">{escape(i.motivo)}</div></td>'
            f'<td {_TD}color:var(--app-text)">{_link(i.headline, i.url)}{resumo}</td>'
            f'<td {_TD}color:var(--app-subtle);font-size:0.78rem">'
            f'{escape(i.source or "—")}</td></tr>')
    descartes = sum(n.descartadas.values())
    lista = ('<ul style="margin:2px 0 0 18px;padding:0;font-size:0.8rem;'
             'color:var(--app-muted)">')
    return (
        f'<div>{escape(inf.resumo_noticias(n))}</div>'
        f'{_SUB_T}Dado · manchetes da fonte</div>'
        + _tabela(["Data", "Impacto", "Dimensões", "Manchete", "Fonte"], linhas)
        + (f'{_SUB_T}Filtro de relevância · {descartes} descartada(s)</div>'
           f'{lista}' + "".join(f"<li>{escape(k)}: {v}</li>"
                               for k, v in n.descartadas.items()) + "</ul>"
           if descartes else "")
        + _nota("Impacto e dimensões saem de regras sobre a manchete, não da "
                "leitura da matéria. Acervo até "
                f"{_data(n.base_ate)}; janela de {n.janela_dias} dias. "
                f"Fonte: {n.fonte or '—'}."))


def corpo_relatorios(r: inf.Relatorios) -> str:
    """Documentos oficiais (metadados) e, para cada uma das sete perguntas,
    os documentos cujo título aponta para ela — ou "Dado não disponível.".
    Puro."""
    if not r.documentos:
        return escape(r.motivo or inf.NAO_DISPONIVEL)
    linhas = "".join(
        f'<tr><td {_TD}color:var(--app-muted);white-space:nowrap">'
        f'{_data(d.reference_date)}</td>'
        f'<td {_TD}color:var(--app-muted)">{escape(d.rotulo)}</td>'
        f'<td {_TD}color:var(--app-text)">{_link(d.titulo, d.source_url)}</td>'
        f'<td {_TD}color:var(--app-subtle);font-size:0.78rem">'
        f'{escape(d.source or "—")}</td></tr>' for d in r.documentos)
    ind = r.indicios()
    perguntas = "".join(
        f'<tr><td {_TD}color:var(--app-muted)">{escape(rot)}</td>'
        f'<td {_TD}color:{"var(--app-text)" if ind[k] else "var(--app-subtle)"}">'
        + (escape("; ".join(f"{d.rotulo} de {_data(d.reference_date)}"
                            for d in ind[k])) if ind[k] else escape(inf.NAO_DISPONIVEL))
        + "</td></tr>" for k, rot in inf.PERGUNTAS)
    return (
        f'<div>{escape(inf.resumo_relatorios(r))}</div>'
        f'{_SUB_T}Dado · documentos publicados</div>'
        + _tabela(["Data", "Tipo", "Documento", "Fonte"], linhas)
        + f'{_SUB_T}Onde procurar · indício pelo título, não conclusão</div>'
        + _tabela(["Pergunta", "Documentos"], perguntas)
        + _nota("O conteúdo dos documentos não é lido aqui; melhora, piora e "
                "oportunidade exigem a leitura e ficam como dado não "
                f"disponível. Base até {_data(r.base_ate)}. "
                f"Fonte: {r.fonte or '—'}."))


def corpo_eventos(e: inf.Eventos) -> str:
    """Linha do tempo Evento | Data | Relevância | Possível impacto, com a
    natureza da data e a fonte; e os tipos sem fonte de data. Puro."""
    sem = ", ".join(inf.TIPOS_EVENTO[t][0] for t in e.sem_dado)
    rodape = _nota(f"Sem fonte de data para: {sem}.") if sem else ""
    if not e.itens:
        return escape(e.motivo or inf.NAO_DISPONIVEL) + rodape
    linhas = "".join(
        f'<tr><td {_TD}color:var(--app-text);font-weight:600">{escape(x.rotulo)}'
        f'<div style="font-weight:400;font-size:0.78rem;color:var(--app-muted)">'
        f'{escape(x.descricao)}</div></td>'
        f'<td {_TD}color:var(--app-muted);white-space:nowrap">{_data(x.data)}'
        f'<div style="font-size:0.74rem;color:var(--app-subtle)">'
        f'{escape(x.natureza)}</div></td>'
        f'<td {_TD}">{_nivel(x.relevancia)}</td>'
        f'<td {_TD}color:var(--app-muted);font-size:0.8rem">{escape(x.impacto)}'
        f'<div style="color:var(--app-subtle)">{_link(x.source or "—", x.source_url)}'
        f'</div></td></tr>' for x in e.itens)
    return (f'<div>{escape(inf.resumo_eventos(e))}</div>'
            + _tabela(["Evento", "Data", "Relevância", "Possível impacto"], linhas)
            + rodape)


def cartoes_analise(a: m.AnaliseAtivo) -> list[str]:
    """Um cartão por etapa, na ordem ATIVO → ... → AÇÃO. Puro."""
    i, fx = a.ativo, a.faixa
    cartoes = [_cartao(1, "Ativo", _grade([
        ("Nome", i.nome), ("Ticker", i.ticker), ("Classe", i.classe),
        ("Subclasse", i.subclasse or "—"), ("Setor", i.setor or "—"),
        ("Valor investido", _moeda(i.valor_investido, i.moeda)),
        ("Valor de mercado", _moeda(i.valor_mercado, i.moeda)),
    ]))]

    frases = "".join(f"<div><strong>{escape(f)}</strong></div>"
                     for f in papeis.em_linguagem_natural(a.papeis))
    origem = ""
    if a.papeis:
        origem = ('<div style="color:var(--app-muted);font-size:0.82rem">'
                  'Como foi identificado: '
                  f'{escape("; ".join(p.motivo for p in a.papeis))}.</div>')
    cartoes.append(_cartao(2, "Papel na carteira", (
        frases + origem
        + '<div style="margin-top:10px;font-weight:700">Tese do ativo</div>'
        f'<div>Por que está na carteira? '
        f'{escape(a.tese.por_que_esta_na_carteira)}</div>'
        f'<div>{escape(a.tese.alinhamento)}</div>'
        f'<div>O motivo continua válido? {escape(_VALIDADE[a.tese.valida])}</div>'
        f'<div style="margin-top:6px;font-size:0.84rem">{_gatilhos(a.tese)}</div>'
    )))

    peso_itens = [
        ("Na carteira", _pct(i.peso_atual)),
        ("Da classe na carteira", _pct(fx.peso_classe)),
        ("Do setor na carteira", _pct(fx.peso_setor)),
        ("Emissor", fx.emissor or "—"),
    ]
    if fx.indexador is not None:
        peso_itens.append(("Indexador", fx.indexador))
    cartoes.append(_cartao(3, "Peso atual", _grade(peso_itens)))

    classe = (_ROTULO_CLASSE.get(i.classe_politica, "—")
              if i.classe_politica else "fora da política")
    faixa_itens = [
        (f"Alvo da classe ({classe})", _pct(fx.alvo_classe, 0)),
        ("Diferença da classe para o alvo", _pp(fx.desvio_classe)),
        ("Limite por ativo", _pct(fx.teto_ativo, 0)),
        ("Folga até o limite", _pp(fx.folga_ativo)),
    ]
    if fx.piso_ativo is not None or fx.alvo_ativo is not None:
        faixa_itens += [
            ("Faixa do ativo", f"{_pct(fx.piso_ativo)} a {_pct(fx.teto_ativo)}"),
            ("Overweight", _pp(fx.overweight_ativo)),
            ("Underweight", _pp(fx.underweight_ativo)),
        ]
        nota = "Faixa do ativo definida por você."
    else:
        nota = ("Sua estratégia define alvo por classe, não por ativo. A "
                "% sugerida para o ativo (alvo da classe dividido pelo "
                "risco) está no resumo, em \"% devida\".")
    cartoes.append(_cartao(4, "Peso desejado / faixa desejada", _grade(
        faixa_itens) + ('<div style="color:var(--app-muted);font-size:0.82rem;'
                        f'margin-top:6px">{escape(nota)}</div>')))

    for n, s in enumerate(a.secoes_externas, start=5):
        if s.chave == "fundamentos" and s.dados:
            corpo = corpo_fundamentos(fund.Fundamentos.de_dict(s.dados))
        elif s.chave == "valuation" and s.dados:
            corpo = corpo_valuation(val.Valuation.de_dict(s.dados))
        elif s.chave == "pares" and s.dados:
            corpo = corpo_pares(prs.ComparacaoPares.de_dict(s.dados))
        elif s.chave == "noticias" and s.dados:
            corpo = corpo_noticias(inf.Noticias.de_dict(s.dados))
        elif s.chave == "relatorios" and s.dados:
            corpo = corpo_relatorios(inf.Relatorios.de_dict(s.dados))
        elif s.chave == "eventos" and s.dados:
            corpo = corpo_eventos(inf.Eventos.de_dict(s.dados))
        else:
            corpo = escape(s.resumo)
        cartoes.append(_cartao(n, s.titulo, corpo,
                               pendente=s.estado == m.PENDENTE))

    cartoes.append(_cartao(12, "Impacto na carteira",
                           _lista(a.impacto.observacoes)))

    cor = _COR_ACAO[a.acao.estado]
    cartoes.append(_cartao(13, "Ação a considerar", (
        f'<div style="font-size:1.1rem;font-weight:800;color:{cor}">'
        f'{escape(a.acao.rotulo)}</div>' + _lista(a.acao.justificativas)
    ), destaque=cor))
    return cartoes


def fluxo_html(a: m.AnaliseAtivo) -> str:
    """Os 13 cartões ligados por setas, num bloco só."""
    return _SETA.join(cartoes_analise(a))


def cartao_questoes(a: m.AnaliseAtivo) -> str:
    """As quatro perguntas lado a lado, cada uma com a sua resposta. Puro."""
    blocos = ""
    for r in a.questoes.values():
        cor = "text" if r.estado == m.DISPONIVEL else "subtle"
        blocos += (
            '<div style="flex:1 1 220px;background:var(--app-surface);'
            'border:1px solid var(--app-border);border-radius:10px;'
            'padding:10px 12px"><div style="font-size:0.78rem;font-weight:'
            f'700;color:var(--app-{cor})">{escape(r.pergunta)}</div>'
            '<div style="font-size:0.84rem;color:var(--app-muted);'
            f'margin-top:4px">{escape(r.resposta)}</div></div>')
    return ('<div style="display:flex;flex-wrap:wrap;gap:8px;'
            f'margin:4px 0 12px 0">{blocos}</div>')


def _cartao_ref(titulo: str, corpo: str) -> str:
    return (
        '<div style="background:var(--app-surface);border:1px dashed '
        'var(--app-border);border-radius:10px;padding:12px 16px;'
        'margin-top:12px"><div style="font-size:0.72rem;font-weight:700;'
        'letter-spacing:.05em;text-transform:uppercase;'
        f'color:var(--app-subtle)">{escape(titulo)}</div>'
        '<div style="font-size:0.9rem;color:var(--app-text);margin-top:6px;'
        f'line-height:1.5">{corpo}</div>'
        '<div style="font-size:0.78rem;color:var(--app-muted);margin-top:8px">'
        f'{escape(refm.AVISO)}</div></div>')


def cartao_referencia_ativo(ref: refm.ReferenciaModelo, ticker: str) -> str:
    """O ativo escolhido contra a carteira recomendada do Portfólio Global.
    Fora das 13 etapas: é comparação, não etapa da decisão. Puro."""
    titulo = "Referência · carteira recomendada do Portfólio Global"
    if not ref.disponivel:
        return _cartao_ref(titulo, escape(ref.motivo))
    linha = ref.linha(ticker)
    if linha is None:
        return _cartao_ref(titulo, escape(
            f"{ticker} não foi identificado na carteira nem no modelo."))
    corpo = (f'<div style="font-weight:700">{escape(linha.ticker)} '
             f'{escape(refm.ROTULO_SITUACAO[linha.situacao])}.</div>'
             + _grade([("Peso no modelo", _pct(linha.peso_modelo)),
                       ("Peso real", _pct(linha.peso_real)),
                       ("Real − modelo", _pp(linha.diferenca))])
             + '<div style="font-size:0.82rem;color:var(--app-muted);'
             f'margin-top:6px">Base dos pesos: '
             f'{escape(refm.ROTULO_BASE[ref.base])}.</div>')
    return _cartao_ref(titulo, corpo)


def cartao_referencia_carteira(ref: refm.ReferenciaModelo) -> str:
    """Classes contra o alvo do modelo e os maiores ativos do modelo que a
    carteira não tem. Puro."""
    titulo = "Referência · carteira recomendada do Portfólio Global"
    if not ref.disponivel:
        return _cartao_ref(titulo, escape(ref.motivo))
    td = '<td style="padding:2px 10px 2px 0">'
    linhas = "".join(
        f"<tr>{td}{escape(c.rotulo)}</td>{td}{_pct(c.alvo)}</td>"
        f"{td}{_pct(c.real)}</td>{td}{_pp(c.diferenca)}</td></tr>"
        for c in ref.classes)
    corpo = _tabela(["Classe", "Modelo", "Real", "Real − modelo"], linhas)
    fora = ref.fora_da_carteira()
    if fora:
        corpo += ('<div style="margin-top:8px;font-weight:700">'
                  'Do modelo, fora da sua carteira</div>'
                  + _lista(f"{x.ticker} · {x.nome} · {_pct(x.peso_modelo)}"
                           for x in fora))
    corpo += _lista(ref.avisos)
    corpo += ('<div style="font-size:0.82rem;color:var(--app-muted);'
              f'margin-top:6px">Base dos pesos: '
              f'{escape(refm.ROTULO_BASE[ref.base])}.</div>')
    return _cartao_ref(titulo, corpo)


def _render_liberada(liberacao: portao.Liberacao, carteira: dict,
                     proventos: dict | None = None) -> None:
    versao = liberacao.politica.version
    st.caption("A análise inteligente dos seus ativos já está disponível. "
               f"Premissa: estratégia versão {versao}.")
    _render_painel(liberacao, carteira, proventos)
    _render_minha_estrategia()


_ANALISE_MEMO_KEY = "_ia_analise_carteira_memo"
# Mesmo prazo dos leitores de fonte (st.cache_data ttl=900): passado isso, a
# análise é refeita e pega cenário, notícias e preços novos.
_ANALISE_MEMO_TTL = 900


def _chave_analise(liberacao: portao.Liberacao, carteira: dict) -> tuple:
    reg = liberacao.politica
    assinatura = hashlib.sha1(json.dumps(
        carteira, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return (getattr(reg, "id", None), getattr(reg, "version", None),
            str(getattr(reg, "updated_at", None)), liberacao.status,
            assinatura)


def _analisar_carteira_memo(liberacao: portao.Liberacao,
                            carteira: dict) -> dict:
    """``servico.analisar_carteira`` lembrado na sessão até algo mudar.

    Cada clique na tela (abrir o detalhe, trocar o ativo) refazia a análise
    das ~30 posições, e isso custava segundos de CPU por rerun (medição de
    30/09/2026, throttle do Streamlit Cloud). A chave é a política (id,
    versão, gravação, status) mais a carteira inteira; o prazo renova
    cenário e fontes. Fica em ``session_state`` e não em ``st.cache_data``
    porque o resultado carrega objetos de domínio grandes, que o
    ``cache_data`` copiaria por pickle a cada acerto.
    """
    chave = _chave_analise(liberacao, carteira)
    agora = time.monotonic()
    memo = st.session_state.get(_ANALISE_MEMO_KEY)
    if memo and memo[0] == chave and memo[1] > agora:
        return memo[2]
    resultado = servico.analisar_carteira(carteira=carteira,
                                          liberacao=liberacao)
    if resultado.get("analysis_available"):
        st.session_state[_ANALISE_MEMO_KEY] = (
            chave, agora + _ANALISE_MEMO_TTL, resultado)
    return resultado


_REFERENCIA_MEMO_KEY = "_ia_referencia_modelo_memo"


def _referencia_memo(carteira: dict) -> refm.ReferenciaModelo:
    """``refm.carregar`` lembrado na sessão pelo mesmo prazo da análise: a
    carteira recomendada muda quando o Portfólio Global salva modelo ou
    alocação, não a cada clique."""
    assinatura = hashlib.sha1(json.dumps(
        carteira.get("posicoes") or [], sort_keys=True,
        default=str).encode("utf-8")).hexdigest()
    agora = time.monotonic()
    memo = st.session_state.get(_REFERENCIA_MEMO_KEY)
    if memo and memo[0] == assinatura and memo[1] > agora:
        return memo[2]
    ref = refm.carregar(carteira.get("posicoes"))
    st.session_state[_REFERENCIA_MEMO_KEY] = (
        assinatura, agora + _ANALISE_MEMO_TTL, ref)
    return ref


def _render_painel(liberacao: portao.Liberacao, carteira: dict,
                   proventos: dict | None) -> None:
    if not carteira.get("posicoes"):
        st.info("Nenhum ativo na carteira para analisar.")
        return
    resultado = _analisar_carteira_memo(liberacao, carteira)
    if not resultado.get("analysis_available"):
        st.info("Sua estratégia mudou de situação. Recarregue a página.")
        return
    ctx, analises = resultado["contexto"], resultado["analises"]

    # Página resumida (rascunho do usuário, 30/09/2026): reserva, renda
    # fixa e uma caixa por ativo. Visão geral e análise detalhada ficam
    # abaixo, fechadas até o usuário pedir.
    resumo = painel.resumo(ctx, analises, carteira, proventos)
    tela_painel.registrar_uma_vez(ctx, analises, resumo)
    politica = liberacao.politica.politica if liberacao.politica else None
    tela_resumida.render(analises, ctx, politica)

    with st.expander("📊 Visão geral da carteira"):
        _, comparacao = tela_painel.comparacao_de(
            hist.CARTEIRA, hist.capturar_carteira(
                resumo, ctx, agora=dt.datetime.now(dt.timezone.utc)))
        st.markdown(tela_painel.cartao_resumo(resumo, comparacao),
                    unsafe_allow_html=True)
        st.markdown(cartao_premissa(ctx), unsafe_allow_html=True)
        st.markdown(cartao_calculos(ctx.calculos), unsafe_allow_html=True)
        _tabela_carteira(analises)
        st.markdown(cartao_referencia_carteira(_referencia_memo(carteira)),
                    unsafe_allow_html=True)

    # Toggle, não expander: o Portfolio Fit tem expander próprio e o
    # Streamlit não aninha expanders.
    if not st.toggle("🔎 Análise detalhada (13 etapas, Portfolio Fit e "
                     "histórico)", key=tela_resumida.DETALHE_KEY):
        return
    por_ticker = {a.ativo.ticker: a for a in analises}
    if st.session_state.get(tela_painel.SELECAO_KEY) not in por_ticker:
        st.session_state.pop(tela_painel.SELECAO_KEY, None)
    escolha = st.selectbox(
        "Ativo", list(por_ticker), key=tela_painel.SELECAO_KEY,
        format_func=lambda t: f"{t} · {por_ticker[t].ativo.nome}")
    analise_ = por_ticker[escolha]
    st.markdown(cartao_questoes(analise_), unsafe_allow_html=True)
    st.markdown(fluxo_html(analise_), unsafe_allow_html=True)
    referencia = _referencia_memo(carteira)
    st.markdown(cartao_referencia_ativo(referencia, analise_.ativo.ticker),
                unsafe_allow_html=True)
    st.markdown("#### Portfolio Fit")
    leitura = tela_fit.render(analise_, ctx,
                              referencia.para_llm(analise_.ativo.ticker))
    st.markdown("#### Histórico e auditoria")
    tela_painel.render_historico(analise_, ctx, leitura)


def _tabela_carteira(analises) -> None:
    st.dataframe(
        [{"Ativo": a.ativo.ticker,
          "Papel principal": (a.papel_principal.rotulo
                              if a.papel_principal else "—"),
          "Peso": round(a.ativo.peso_atual, 1),
          "Classe vs alvo (pp)": a.faixa.desvio_classe,
          "Ação a considerar": a.acao.rotulo} for a in analises],
        hide_index=True, use_container_width=True,
        column_config={"Peso": st.column_config.NumberColumn(format="%.1f%%")})

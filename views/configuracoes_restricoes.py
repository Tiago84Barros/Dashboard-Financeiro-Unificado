"""
views/configuracoes_restricoes.py
Aba "Restrições" de Configurações — só o administrador vê.

Desde 05/10/2026 nenhuma tela de uso mostra limitação técnica, fonte fora do
ar, procedência ou versão de metodologia: tudo isso é registrado em silêncio
(``design/lacunas.py``) e aparece aqui, para quem administra o app decidir o
que fazer com cada item. Três naturezas:

  Restrição       — falta dado, fonte, histórico ou cálculo (fila do corretor)
  Erro            — exceção que chegou à fronteira de uma tela
  Detalhe técnico — procedência, metodologia, data de vitrine, amostra

A decisão (Pendente / Em análise / Aceita / Resolvida) vale para o corretor
diário de lacunas, que lê o mesmo log: "Aceita" tira o item da fila dele.

Apresentação
------------
A tela é o painel de trabalho do admin e está organizada nas quatro faixas da
ordem em que se usa: **estado do log** (de onde veio e quando), **contagem por
natureza**, **fila de itens** (filtros + tabela) e **decisão**.

As contagens são cards em CSS, não ``st.metric``: a régua do app é card CSS
(``memoria: ui-cards-css``) e o ``st.metric`` nativo não aceita o tom por
natureza nem a linha que explica o que cada número significa — o ``help`` do
metric escondia essa explicação atrás de um ícone. Todo texto que vem do log
passa por ``escape``: mensagem de exceção é dado de terceiro, não HTML de
confiança.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from core.lacunas import painel

_FILTRO_NAT = "cfg_restr_natureza"
_FILTRO_STATUS = "cfg_restr_status"
_FILTRO_TELA = "cfg_restr_tela"
_FILTRO_BUSCA = "cfg_restr_busca"
_ESCOLHA = "cfg_restr_escolha"
_TODAS = "Todas"

#: Cores por token, nunca literais: ``design/tema.py`` e ``design/theme_light.py``
#: redefinem os mesmos nomes, então a tela acompanha os dois temas sozinha
#: (``memoria: tema-claro-so-alcanca-o-que-passa-por-token``).
_RESTR_CSS = """
<style>
.restr-estado {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 7px;
    margin: 2px 0 4px;
}
.restr-chip {
    --restr-tom: var(--app-muted, #8A99AE);
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 10px;
    border: 1px solid color-mix(in srgb, var(--restr-tom) 30%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--restr-tom) 10%, transparent);
    color: var(--app-text, #E2E8F0);
    font-size: .66rem;
    font-weight: 700;
    white-space: nowrap;
}
.restr-chip b { color: var(--restr-tom); font-weight: 820; }
.restr-chip-nome {
    color: var(--app-muted, #8A99AE);
    font-size: .6rem;
    font-weight: 700;
    letter-spacing: .06em;
    text-transform: uppercase;
}
.restr-kpis {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin: 12px 0 18px;
}
.restr-kpi {
    --restr-tom: var(--app-info, #4A9EFF);
    display: flex;
    flex-direction: column;
    min-width: 0;
    padding: 13px 15px 14px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-left: 3px solid var(--restr-tom);
    border-radius: 12px;
    background: linear-gradient(140deg,
        color-mix(in srgb, var(--restr-tom) 8%, transparent),
        var(--app-surface, rgba(17,24,39,.72)) 60%);
}
.restr-kpi-label {
    /* Duas linhas reservadas: sem a reserva, o rotulo longo empurra o numero
       de um card so e a fileira das quatro contagens sai torta -- mesma causa
       do ajuste nos KPIs do Dashboard Geral. */
    display: flex;
    align-items: center;
    min-height: 2.3em;
    color: var(--app-muted, #8A99AE);
    font-size: .63rem;
    font-weight: 760;
    letter-spacing: .07em;
    line-height: 1.15;
    text-transform: uppercase;
}
.restr-kpi-value {
    margin-top: 6px;
    color: var(--restr-tom);
    font-size: 1.72rem;
    font-variant-numeric: tabular-nums;
    font-weight: 850;
    letter-spacing: -.02em;
    line-height: 1.05;
}
.restr-kpi-detail {
    margin-top: 7px;
    color: var(--app-subtle, #718096);
    font-size: .66rem;
    line-height: 1.4;
}
.restr-secao {
    --restr-tom: var(--app-info, #4A9EFF);
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 20px 0 12px;
    padding: 8px 13px;
    border: 1px solid var(--app-border, rgba(148,163,184,.12));
    border-left: 3px solid var(--restr-tom);
    border-radius: 10px;
    background: linear-gradient(90deg,
        color-mix(in srgb, var(--restr-tom) 7%, transparent),
        var(--app-surface, #121722) 45%);
}
.restr-secao-titulo {
    flex: 0 0 auto;
    color: var(--app-text, #F1F5F9);
    font-size: .78rem;
    font-weight: 820;
    letter-spacing: -.01em;
}
.restr-secao-texto {
    flex: 1 1 auto;
    min-width: 0;
    color: var(--app-muted, #8A99AE);
    font-size: .71rem;
    line-height: 1.45;
}
.restr-secao-selo {
    flex: 0 0 auto;
    padding: 3px 9px;
    border: 1px solid color-mix(in srgb, var(--restr-tom) 28%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--restr-tom) 9%, transparent);
    color: var(--app-text, #DCE7F5);
    font-size: .61rem;
    font-weight: 720;
    white-space: nowrap;
}
.restr-item {
    --restr-tom: var(--app-info, #4A9EFF);
    padding: 15px 17px;
    border: 1px solid var(--app-border, rgba(148,163,184,.14));
    border-left: 3px solid var(--restr-tom);
    border-radius: 12px;
    background: var(--app-surface, rgba(17,24,39,.72));
}
.restr-item-msg {
    color: var(--app-text, #F1F5F9);
    font-size: .93rem;
    font-weight: 700;
    line-height: 1.4;
}
.restr-item-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
}
.restr-campos {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 11px 18px;
    margin-top: 14px;
    padding-top: 13px;
    border-top: 1px solid var(--app-border, rgba(148,163,184,.12));
}
.restr-campo { min-width: 0; }
.restr-campo-rotulo {
    color: var(--app-subtle, #718096);
    font-size: .59rem;
    font-weight: 760;
    letter-spacing: .07em;
    text-transform: uppercase;
}
.restr-campo-valor {
    margin-top: 3px;
    color: var(--app-text, #E2E8F0);
    font-size: .76rem;
    line-height: 1.4;
    overflow-wrap: anywhere;
}
.restr-campo-valor code {
    padding: 1px 5px;
    border-radius: 5px;
    background: color-mix(in srgb, var(--app-muted, #8A99AE) 14%, transparent);
    font-size: .72rem;
}
@media (max-width: 1100px) {
    .restr-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 760px) {
    .restr-kpis { grid-template-columns: 1fr; }
    .restr-secao { align-items: flex-start; flex-direction: column; gap: 6px; }
}
</style>
"""

#: Tom de cada contagem e de cada selo: a cor diz a natureza antes de o numero
#: ser lido. Chave simbolica, e nao a cor -- o valor sai de token do tema.
_TOM = {
    "danger": "var(--app-danger, #FC5C7D)",
    "warning": "var(--app-warning, #F6C90E)",
    "info": "var(--app-info, #4A9EFF)",
    "ok": "var(--app-primary, #00C896)",
    "muted": "var(--app-muted, #8A99AE)",
}

#: Ponto colorido na coluna Situacao. O dataframe desenha em canvas e ignora
#: CSS (``memoria: canvas-do-data-editor-ignora-css``), entao o estado precisa
#: entrar no proprio texto da celula.
_PONTO_STATUS = {
    "aberta": "🔴",
    "incerta": "🟡",
    "em_pr": "🔵",
    "legitima": "⚪",
    "resolvida": "🟢",
}


@st.cache_data(ttl=300, show_spinner=False)
def _carregar() -> painel.Painel:
    return painel.carregar()


def _limpar_cache() -> None:
    # Sem Streamlit (alguns testes) o decorador e identidade e nao tem .clear.
    limpar = getattr(_carregar, "clear", None)
    if callable(limpar):
        limpar()


def _data_curta(valor: str | None) -> str:
    if not valor:
        return "—"
    try:
        return pd.Timestamp(valor).tz_convert("America/Sao_Paulo").strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return str(valor)[:16]


def render() -> None:
    from core.user_context import is_admin

    if not is_admin():
        return
    render_painel(_carregar())


def _kpi_html(label: str, valor: int, detalhe: str, tom: str) -> str:
    return (
        f'<article class="restr-kpi" style="--restr-tom:{_TOM[tom]}">'
        f'<div class="restr-kpi-label">{escape(label)}</div>'
        f'<div class="restr-kpi-value">{valor}</div>'
        f'<div class="restr-kpi-detail">{escape(detalhe)}</div>'
        "</article>"
    )


def _chip_html(nome: str, valor: str, tom: str) -> str:
    rotulo = f'<span class="restr-chip-nome">{escape(nome)}</span>' if nome else ""
    return (f'<span class="restr-chip" style="--restr-tom:{_TOM[tom]}">'
            f"{rotulo}<b>{escape(valor)}</b></span>")


def _chips_estado_html(dados: painel.Painel) -> str:
    """Estado do log: cada fonte com o tom do que ela respondeu.

    A fonte que falhou continua nomeada na tela, nunca sumindo — é a mesma
    regra do bloco de contexto de mercado. Isso era um ``st.caption`` corrido,
    onde "cloud: indisponivel: OperationalError" lia como parte da frase; em
    chip com tom próprio, a falha salta.
    """
    chips = [
        _chip_html(nome, str(estado),
                   "ok" if str(estado).startswith("ok") else "danger")
        for nome, estado in dados.fontes.items()
    ]
    chips.append(_chip_html("lido em", _data_curta(dados.gerado_em), "muted"))
    chips.append(_chip_html("cache", "5 min", "muted"))
    return '<div class="restr-estado">' + "".join(chips) + "</div>"


def _faixa_secao(titulo: str, texto: str, selo: str, tom: str) -> None:
    """Faixa de seção, no mesmo molde da faixa de aba de Configurações."""
    st.markdown(
        f'<section class="restr-secao" style="--restr-tom:{_TOM[tom]}">'
        f'<span class="restr-secao-titulo">{escape(titulo)}</span>'
        f'<span class="restr-secao-texto">{escape(texto)}</span>'
        f'<span class="restr-secao-selo">{escape(selo)}</span>'
        "</section>",
        unsafe_allow_html=True,
    )


def render_painel(dados: painel.Painel) -> None:
    """Desenha o painel a partir do que ja foi lido (separado para teste)."""
    itens = dados.itens
    st.markdown(_RESTR_CSS, unsafe_allow_html=True)

    estado, recarregar = st.columns([1, 0.16], vertical_alignment="center")
    estado.markdown(_chips_estado_html(dados), unsafe_allow_html=True)
    if recarregar.button("↻ Recarregar", key="cfg_restr_recarregar",
                         use_container_width=True,
                         help="Relê o log local e o do Supabase, ignorando o cache."):
        _limpar_cache()
        st.rerun()

    ativos = [i for i in itens if i["status"] not in ("legitima", "resolvida")]
    st.markdown(
        '<section class="restr-kpis">'
        + _kpi_html("Restrições pendentes",
                    sum(1 for i in ativos if i["natureza"] == "Restrição"),
                    "Falta dado, fonte ou cálculo. Fila do corretor diário.",
                    "warning")
        + _kpi_html("Erros pendentes",
                    sum(1 for i in ativos if i["natureza"] == "Erro"),
                    "Exceção que chegou à fronteira de uma tela.",
                    "danger")
        + _kpi_html("Detalhes técnicos",
                    sum(1 for i in itens if i["natureza"] == "Detalhe técnico"),
                    "Procedência, metodologia e amostra. Informativo.",
                    "info")
        + _kpi_html("Reincidentes",
                    sum(1 for i in itens
                        if i.get("reincidente") and i["status"] != "resolvida"),
                    "Voltaram a aparecer depois de marcadas como resolvidas.",
                    "danger")
        + "</section>",
        unsafe_allow_html=True,
    )

    if not itens:
        st.info("Nenhuma restrição registrada até agora.", icon="✅")
        return

    _faixa_secao(
        "Fila de itens",
        "Natureza, situação e tela filtram a lista; a busca varre a mensagem e "
        "o código. A ordem é a do corretor: maior prioridade primeiro.",
        f"{len(itens)} no log",
        "info",
    )
    with st.container(border=True):
        f1, f2, f3, f4 = st.columns([1.1, 1.3, 1.1, 1.5])
        naturezas = f1.multiselect("Natureza", painel.NATUREZAS,
                                   default=["Restrição", "Erro"], key=_FILTRO_NAT)
        status_sel = f2.multiselect(
            "Situação", list(painel.STATUS_ROTULO),
            default=["aberta", "incerta", "em_pr"], key=_FILTRO_STATUS,
            format_func=painel.STATUS_ROTULO.get)
        telas = sorted({i["tela"] for i in itens})
        tela = f3.selectbox("Tela", [_TODAS, *telas], key=_FILTRO_TELA)
        busca = f4.text_input("Buscar no texto ou no código",
                              key=_FILTRO_BUSCA).strip().lower()

    vistos = [
        i for i in itens
        if (not naturezas or i["natureza"] in naturezas)
        and (not status_sel or i["status"] in status_sel)
        and (tela == _TODAS or i["tela"] == tela)
        and (not busca or busca in (i.get("ultima_mensagem") or "").lower()
             or busca in (i.get("codigo") or "").lower())
    ]
    if not vistos:
        st.caption(f"0 de {len(itens)} registros.")
        st.info("Nenhum registro atende a esses filtros — filtro estreito "
                "parece ausência de problema.", icon="🔎")
        return

    tabela = pd.DataFrame([{
        "!": "⚠" if i.get("reincidente") else "",
        "Tela": i["tela"],
        "Natureza": i["natureza"],
        "Situação": (_PONTO_STATUS.get(i["status"], "") + " "
                     + painel.STATUS_ROTULO.get(i["status"], i["status"])).strip(),
        "Mensagem": i.get("ultima_mensagem") or "",
        "Ativo": i.get("entidade") or "",
        "Vezes (14d)": i.get("ocorrencias_14d", 0),
        "Última vez": _data_curta(i.get("ultima_vez")),
    } for i in vistos])
    st.dataframe(
        tabela, hide_index=True, use_container_width=True,
        height=min(430, 60 + 35 * len(tabela)),
        column_config={
            "!": st.column_config.TextColumn(
                width="small", help="⚠ reincidente: voltou depois de resolvida."),
            "Tela": st.column_config.TextColumn(width="small"),
            "Natureza": st.column_config.TextColumn(width="small"),
            "Situação": st.column_config.TextColumn(width="small"),
            "Mensagem": st.column_config.TextColumn(width="large"),
            "Vezes (14d)": st.column_config.NumberColumn(width="small", format="%d"),
            "Última vez": st.column_config.TextColumn(width="small"),
        },
    )
    st.caption(f"{len(vistos)} de {len(itens)} registros.")

    _faixa_secao(
        "Decisão",
        'O corretor diário lê o que for marcado aqui: "Aceita" tira o item da '
        'fila dele, "Resolvida" reabre sozinha se o item voltar a aparecer.',
        "Grava no log",
        "ok",
    )
    por_chave = {i["impressao"]: i for i in vistos}
    escolhido = st.selectbox(
        "Item", list(por_chave), key=_ESCOLHA,
        format_func=lambda k: (f"{por_chave[k]['tela']} — "
                               f"{(por_chave[k].get('ultima_mensagem') or '')[:90]}"))
    item = por_chave[escolhido]
    _render_detalhe(item)
    _render_decisao(item)


def _tom_da_natureza(natureza: str) -> str:
    return ("danger" if natureza == "Erro"
            else "warning" if natureza == "Restrição" else "info")


def _campo_html(rotulo: str, valor: str) -> str:
    """Campo da ficha. ``valor`` já vem como HTML pronto e escapado."""
    return ('<div class="restr-campo">'
            f'<div class="restr-campo-rotulo">{escape(rotulo)}</div>'
            f'<div class="restr-campo-valor">{valor}</div></div>')


def _render_detalhe(item: dict) -> None:
    """Ficha do item escolhido, em campos rotulados.

    Era uma pilha de linhas em ``**negrito**`` separadas por ``·``, em que
    achar o código ou a primeira ocorrência exigia ler a frase inteira. Em
    grade de rótulo/valor cada campo tem lugar fixo, e a mensagem — o que de
    fato se lê primeiro — fica sozinha no topo do card.
    """
    tom = _tom_da_natureza(item["natureza"])
    tags = [(item["natureza"], tom),
            (painel.STATUS_ROTULO.get(item["status"], item["status"]), "muted")]
    if item.get("reincidente"):
        tags.append(("reincidente", "danger"))
    tags_html = "".join(_chip_html("", str(texto), t) for texto, t in tags)

    campos = [
        _campo_html("Onde", f"{escape(item['tela'])}<br>"
                            f"<code>{escape(item.get('modulo') or '—')}</code>"),
        _campo_html("Código",
                    f"<code>{escape(item.get('codigo') or '(sem código)')}</code>"),
        _campo_html("Ocorrências",
                    f"{item.get('ocorrencias', 0)} no total · "
                    f"{item.get('ocorrencias_14d', 0)} em 14 dias"),
        _campo_html("Primeira vez", escape(_data_curta(item.get("primeira_vez")))),
        _campo_html("Última vez", escape(_data_curta(item.get("ultima_vez")))),
    ]
    if item.get("entidade"):
        campos.insert(2, _campo_html("Ativo", escape(str(item["entidade"]))))
    if item.get("contexto"):
        campos.append(_campo_html("Contexto", escape(", ".join(
            f"{k}={v}" for k, v in sorted(item["contexto"].items())))))
    if item.get("pr_url"):
        campos.append(_campo_html("PR", escape(str(item["pr_url"]))))
    if item.get("nota_triagem"):
        campos.append(_campo_html("Nota da triagem", escape(str(item["nota_triagem"]))))

    st.markdown(
        f'<div class="restr-item" style="--restr-tom:{_TOM[tom]}">'
        f'<div class="restr-item-msg">{escape(item.get("ultima_mensagem") or "—")}</div>'
        f'<div class="restr-item-tags">{tags_html}</div>'
        f'<div class="restr-campos">{"".join(campos)}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def _render_decisao(item: dict) -> None:
    chave = item["impressao"][:12]
    opcoes = list(painel.STATUS_DECIDIVEIS)
    atual = item["status"] if item["status"] in opcoes else "aberta"
    explicacao = {
        "aberta": "Pendente — precisa de correção; fica na fila do corretor diário.",
        "incerta": "Em análise — volta à fila com prioridade reduzida.",
        "legitima": "Aceita — limitação conhecida, nada a corrigir; sai da fila.",
        "resolvida": "Resolvida — corrigida; reabre sozinha se voltar a aparecer.",
    }
    with st.container(border=True):
        with st.form(f"cfg_restr_form_{chave}"):
            novo = st.radio("Decisão", opcoes, index=opcoes.index(atual),
                            format_func=explicacao.get, key=f"cfg_restr_status_{chave}")
            obs = st.text_area("Observação (opcional)", key=f"cfg_restr_obs_{chave}",
                               max_chars=400,
                               placeholder="Por que esta decisão? Fica gravada no "
                                           "log, junto da data.")
            enviar = st.form_submit_button("Registrar decisão", type="primary")
    if not enviar:
        return
    resultado = painel.decidir(item["impressao"], novo, obs)
    if "ok" not in resultado.values():
        st.error("A decisão não foi gravada: "
                 + ("; ".join(f"{k}: {v}" for k, v in resultado.items()) or "nenhum destino"))
        return
    _limpar_cache()
    st.success("Decisão registrada — "
               + "; ".join(f"{k}: {v}" for k, v in resultado.items()))

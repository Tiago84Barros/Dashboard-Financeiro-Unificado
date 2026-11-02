"""
Aba "A pagar e receber" do Controle Financeiro.

Mostra os lançamentos previstos (``core.previstos``): o que vence, o que já
venceu sem baixa e como o saldo das contas de movimentação fica se tudo for
pago na data. Previsto nasce na barra lateral (Situação: Previsto) e recebe a
baixa aqui, à mão, ou sozinho quando o extrato importado mostra o pagamento.
"""
from __future__ import annotations

import html
from datetime import date as _date
from datetime import timedelta

import streamlit as st

from core.config import settings
from core.previstos import (
    PRAZO_PROXIMOS,
    dar_baixa,
    excluir_previsto,
    listar_previstos,
    projetar_saldo,
    resumir,
    saldo_contas_movimentacao,
    situacao,
)
from core.utils import escapar_cifrao, fmt_moeda
from design.componentes import card_metrica, estado_vazio
from design.lacunas import falha_de_acao

HORIZONTES = {"30 dias": 30, "60 dias": 60, "90 dias": 90}


def _rotulo(p: dict) -> str:
    serie = f" ({p['parcela']}/{p['total_parcelas']})" if p["total_parcelas"] > 1 else ""
    return (f"{p['vencimento'].strftime('%d/%m/%Y')} · {p['descricao']}{serie} · "
            f"{fmt_moeda(p['valor'])}")


def _tabela_html(linhas: list[dict], hoje: _date, com_saldo: bool) -> str:
    """Tabela em HTML com tokens do tema: o ``st.dataframe`` pinta num canvas
    que ignora o tema claro (memória: canvas-do-data-editor-ignora-css)."""
    cab = ["Vencimento", "Descrição", "Categoria", "Valor", "Situação"]
    if com_saldo:
        cab.append("Saldo após")
    th = "".join(
        f'<th style="text-align:left;padding:6px 8px;font-size:0.68rem;'
        f'text-transform:uppercase;letter-spacing:0.05em;color:var(--app-muted);'
        f'border-bottom:1px solid var(--app-border);">{c}</th>' for c in cab)
    corpo = []
    for p in linhas:
        sit = situacao(p["vencimento"], hoje)
        cor_sit = "var(--app-danger)" if sit == "vencido" else "var(--app-text)"
        cor_valor = "var(--app-primary)" if p["valor"] > 0 else "var(--app-danger)"
        serie = f" ({p['parcela']}/{p['total_parcelas']})" if p["total_parcelas"] > 1 else ""
        celulas = [
            (p["vencimento"].strftime("%d/%m/%Y"), "var(--app-text)"),
            (p["descricao"] + serie, "var(--app-text)"),
            (p["categoria"], "var(--app-subtle)"),
            (fmt_moeda(p["valor"]), cor_valor),
            (sit, cor_sit),
        ]
        if com_saldo:
            cor_saldo = "var(--app-danger)" if p["saldo_apos"] < 0 else "var(--app-text)"
            celulas.append((fmt_moeda(p["saldo_apos"]), cor_saldo))
        corpo.append("<tr>" + "".join(
            f'<td style="padding:6px 8px;font-size:0.82rem;color:{cor};'
            f'border-bottom:1px solid var(--app-border);">{html.escape(str(v))}</td>'
            for v, cor in celulas) + "</tr>")
    return (f'<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr>{th}</tr></thead><tbody>{"".join(corpo)}</tbody></table></div>')


def _acoes(previstos: list[dict], hoje: _date) -> None:
    st.markdown("#### Dar baixa ou excluir")
    st.caption("Baixa = o dinheiro já saiu ou entrou. Ao importar o extrato, a conciliação "
               "faz isso sozinha quando acha o pagamento.")
    por_id = {p["id"]: p for p in previstos}
    escolhido = st.selectbox(
        "Previsto", list(por_id), format_func=lambda i: _rotulo(por_id[i]),
        key="cf_prev_escolhido")
    p = por_id[escolhido]
    with st.form("cf_prev_baixa", clear_on_submit=False):
        c1, c2 = st.columns(2, gap="small")
        data_pg = c1.date_input("Data do pagamento", value=hoje, format="DD/MM/YYYY",
                                key="cf_prev_data")
        valor_pg = c2.number_input(
            "Valor pago (R$)", min_value=0.01, value=abs(p["valor"]), step=0.01, format="%.2f",
            key=f"cf_prev_valor_{escolhido}",
            help="Mude só se o pago diferiu do previsto (juros, multa, desconto).")
        b1, b2 = st.columns(2, gap="small")
        baixar = b1.form_submit_button("Dar baixa", type="primary", width="stretch",
                                       disabled=settings.MOCK_MODE)
        excluir = b2.form_submit_button("Excluir previsto", width="stretch",
                                        disabled=settings.MOCK_MODE)
    if baixar:
        valor = None if abs(valor_pg - abs(p["valor"])) < 0.005 else valor_pg
        ok, msg = dar_baixa(escolhido, data_pg, valor)
        if ok:
            st.session_state["cf_prev_msg"] = f"Baixa feita: {p['descricao']}."
            st.rerun()
        falha_de_acao("Não foi possível dar baixa.", RuntimeError(msg))
    if excluir:
        ok, msg = excluir_previsto(escolhido)
        if ok:
            st.session_state["cf_prev_msg"] = f"Previsto excluído: {p['descricao']}."
            st.rerun()
        falha_de_acao("Não foi possível excluir o previsto.", RuntimeError(msg))


def render_previstos() -> None:
    """Conteúdo da aba 📅 A pagar e receber."""
    hoje = _date.today()
    msg = st.session_state.pop("cf_prev_msg", None)
    if msg:
        st.success(msg)

    try:
        previstos = listar_previstos()
        saldo = saldo_contas_movimentacao()
    except Exception as exc:  # noqa: BLE001
        falha_de_acao("Não foi possível ler os lançamentos previstos.", exc)
        return

    if not previstos:
        estado_vazio("Nenhuma conta a pagar ou a receber em aberto. Para criar, use a barra "
                     "lateral: Novo lançamento › Situação: Previsto.", "📅")
        return

    r = resumir(previstos, hoje)
    k1, k2, k3, k4 = st.columns(4, gap="small")
    with k1:
        card_metrica("Vencidos sem baixa", r["n_vencidos"],
                     fmt_moeda(r["total_vencido"]) if r["n_vencidos"] else "nenhum",
                     positivo=False if r["n_vencidos"] else None)
    with k2:
        card_metrica(f"A pagar em {PRAZO_PROXIMOS} dias", fmt_moeda(r["a_pagar"]))
    with k3:
        card_metrica(f"A receber em {PRAZO_PROXIMOS} dias", fmt_moeda(r["a_receber"]))
    horizonte = st.session_state.get("cf_prev_horizonte", "30 dias")
    ate = hoje + timedelta(days=HORIZONTES.get(horizonte, 30))
    projecao = projetar_saldo(saldo or 0.0, previstos, hoje, ate) if saldo is not None else []
    with k4:
        final = projecao[-1]["saldo_apos"] if projecao else saldo
        card_metrica(f"Saldo projetado em {horizonte}",
                     fmt_moeda(final) if final is not None else "—",
                     f"hoje {fmt_moeda(saldo)}" if saldo is not None else None,
                     positivo=None if final is None else final >= 0)

    if r["n_vencidos"]:
        st.warning(escapar_cifrao(
            f"{r['n_vencidos']} previsto(s) vencido(s) somando {fmt_moeda(r['total_vencido'])}. "
            "Se já foram pagos, dê baixa (ou importe o extrato); se não, a projeção conta "
            "com eles saindo hoje."))

    st.radio("Horizonte da projeção", list(HORIZONTES), horizontal=True,
             key="cf_prev_horizonte")
    st.caption("Saldo das contas de movimentação (sem cartão) somado a cada previsto até o "
               "horizonte. Vencido entra como se fosse pago hoje. Previsto não entra nos totais "
               "do mês nem no saldo até receber baixa.")
    if projecao:
        st.markdown(_tabela_html(projecao, hoje, com_saldo=True), unsafe_allow_html=True)
    else:
        st.caption("Nenhum previsto dentro do horizonte.")

    fora = [p for p in previstos if not p.get("na_projecao", True)]
    if fora:
        with st.expander(f"Fora da projeção ({len(fora)})"):
            st.caption("Previstos em conta que não é de movimentação, ou está inativa: o "
                       "saldo dessas contas não está na base da projeção.")
            st.markdown(_tabela_html(fora, hoje, com_saldo=False), unsafe_allow_html=True)
    depois = [p for p in previstos if p["vencimento"] > ate and p.get("na_projecao", True)]
    if depois:
        with st.expander(f"Depois do horizonte ({len(depois)})"):
            st.markdown(_tabela_html(depois, hoje, com_saldo=False), unsafe_allow_html=True)

    _acoes(previstos, hoje)

"""
UI isolada para upload/importação de fatura de cartão de crédito.

Mantém a lógica de negócio em core.controle e renderiza apenas o fluxo de
arquivo CSV usado pela seção Configurações.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

import pandas as pd
import streamlit as st

from core.controle import (
    corrigir_classificacao_pagamentos_fatura,
    get_contas_cartao_credito,
    importar_fatura_cartao_csv,
    limpar_transacoes_cartao,
    parse_fatura_cartao_csv,
)
from core.utils import fmt_moeda
from design.componentes import card_metrica
from design.lacunas import aviso_lacuna

_COR_RECEITA = "var(--app-primary)"
_COR_DESPESA = "var(--app-danger)"
_COR_INVEST = "var(--app-info)"
_COR_NEUTRO = "var(--app-muted)"
_CC_FEE_TERMS = (
    "anuidade", "tarifa", "iof", "juros", "multa", "encargo",
    "rotativo", "mora",
)


def _norm_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return text.encode("ascii", "ignore").decode("ascii").casefold().strip()


def _infer_due_date_from_filename(filename: str | None) -> date:
    if filename:
        match = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", filename)
        if match:
            y, m, d = map(int, match.groups())
            try:
                return date(y, m, d)
            except ValueError:
                pass
    today = date.today()
    return date(today.year, today.month, min(today.day, 28))


_PAYMENT_TERMS_PREVIEW = (
    "pag fatura", "pagamento fatura",
    "inclusao de pagamento", "inclusao  de pagamento",
    "debito em conta", "pagto debito",
)


def _invoice_upload_summary(rows: list[dict]) -> dict:
    bruto = sum(abs(float(r.get("value_brl") or 0.0)) for r in rows)
    compras = 0.0
    tarifas = 0.0
    estornos = 0.0   # créditos reais (devolução de compra)
    pagamentos = 0.0  # pagamentos de fatura
    for row in rows:
        value = float(row.get("value_brl") or 0.0)
        cat_norm = _norm_text(row.get("category", ""))
        desc_norm = _norm_text(row.get("description_raw", ""))
        combined = f"{cat_norm} {desc_norm}"
        if value < 0:
            # Verifica se é pagamento de fatura ou estorno real
            is_payment = (
                "pagamento de cartao" in cat_norm
                or any(t in desc_norm for t in _PAYMENT_TERMS_PREVIEW)
            )
            if is_payment:
                pagamentos += abs(value)
            else:
                estornos += abs(value)
        elif any(term in combined for term in _CC_FEE_TERMS):
            tarifas += value
        else:
            compras += value
    return {
        "total_bruto": round(bruto, 2),
        "compras_reais": round(compras, 2),
        "tarifas": round(tarifas, 2),
        "pagamentos": round(pagamentos, 2),
        "estornos": round(estornos, 2),
        "creditos": round(pagamentos + estornos, 2),  # compatibilidade
        "net_total": round(compras + tarifas - estornos - pagamentos, 2),
    }


_MARCAS_TECNICAS = ("modo mock", "não configurado")


def _mostrar_falha(result: dict) -> None:
    """Falha de importação: problema do arquivo ou da conta escolhida fica na
    tela; configuração do ambiente vira frase neutra e registro."""
    msg = result.get("message") or "Falha ao importar fatura."
    if not any(m in msg.lower() for m in _MARCAS_TECNICAS):
        st.error(msg)
        return
    st.error("Não foi possível importar a fatura agora.")
    aviso_lacuna(
        "Importação de fatura recusada por configuração do ambiente (modo mock ou banco).",
        codigo="tela.controle.fatura_importacao_recusada",
    )


def render_upload_fatura_cartao(*, show_header: bool = True) -> None:
    """Renderiza o fluxo exclusivo de importacao da fatura do cartao."""
    if show_header:
        st.subheader("Upload de Fatura do Cartão")
        st.caption("Importe a fatura CSV. Depois, acompanhe os dados em Controle Financeiro > Cartão de Crédito.")

    last_result = st.session_state.get("cc_invoice_import_result")
    last_ok = bool(last_result and last_result.get("ok"))
    if last_ok:
        summary = last_result.get("summary", {})
        st.success(
            f"Fatura importada: {int(summary.get('inserted', 0))} novo(s) lançamento(s), "
            f"{int(summary.get('skipped', 0))} duplicado(s) ignorado(s)."
        )
    elif last_result:
        _mostrar_falha(last_result)

    contas = get_contas_cartao_credito()
    uploaded = st.file_uploader(
        "Arquivo CSV da fatura",
        type=["csv"],
        key="settings_cc_invoice_upload",
        help="Modelo com Data de Compra, Nome no Cartão, Final do Cartão, Categoria, Descrição, Parcela e valores.",
    )

    # O date_input já existe antes do upload e, por causa da chave persistente,
    # o Streamlit não reaplica seu argumento `value` quando o arquivo muda.
    # Sincroniza explicitamente uma nova fatura com a data presente no nome.
    if uploaded is not None:
        previous_file = st.session_state.get("settings_cc_invoice_due_date_file")
        if uploaded.name != previous_file:
            st.session_state["settings_cc_invoice_due_date"] = (
                _infer_due_date_from_filename(uploaded.name)
            )
            st.session_state["settings_cc_invoice_due_date_file"] = uploaded.name

    col_due, col_account = st.columns([1, 2], gap="small")
    with col_due:
        due_date = st.date_input(
            "Vencimento da fatura",
            value=_infer_due_date_from_filename(uploaded.name if uploaded else None),
            format="DD/MM/YYYY",
            key="settings_cc_invoice_due_date",
        )
    with col_account:
        if contas:
            account_idx = st.selectbox(
                "Conta do cartão",
                range(len(contas)),
                format_func=lambda i: contas[i]["nome"],
                key="settings_cc_invoice_account",
            )
            account_id = contas[account_idx]["id"]
        else:
            st.warning("Cadastre uma conta do tipo cartão de crédito antes de importar.")
            account_id = None

    if uploaded is None:
        st.caption("Selecione uma fatura CSV para visualizar a prévia e importar.")
        return

    file_bytes = uploaded.getvalue()
    parsed = parse_fatura_cartao_csv(file_bytes, due_date)
    rows = parsed.get("rows", [])
    upload_summary = _invoice_upload_summary(rows)

    if parsed.get("errors"):
        for err in parsed["errors"][:5]:
            st.error(err)
        if len(parsed["errors"]) > 5:
            st.caption(f"+ {len(parsed['errors']) - 5} erro(s) adicionais.")

    if not rows:
        st.caption("Nenhuma linha válida encontrada na fatura.")
        return

    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        card_metrica("Arquivo", uploaded.name[:24], f"{len(rows)} lançamento(s) válidos", accent=_COR_NEUTRO)
    with c2:
        card_metrica("Total bruto", fmt_moeda(upload_summary["total_bruto"]), "Soma absoluta da fatura.", accent=_COR_DESPESA)
    with c3:
        card_metrica("Compras reais", fmt_moeda(upload_summary["compras_reais"]), "Exclui pagamentos, estornos e tarifas.", accent=_COR_DESPESA)
    with c4:
        card_metrica("Líquido", fmt_moeda(upload_summary["net_total"]), "Compras + tarifas - créditos.", accent=_COR_NEUTRO)

    c5, c6, c7, c8 = st.columns(4, gap="small")
    with c5:
        card_metrica("Tarifas", fmt_moeda(upload_summary["tarifas"]), "Anuidade, IOF, juros, multa e encargos.", accent="var(--app-warning)")
    with c6:
        pag_val = upload_summary.get("pagamentos", 0.0)
        est_val = upload_summary.get("estornos", 0.0)
        subtitle = f"Pagamentos: {fmt_moeda(pag_val)} | Estornos: {fmt_moeda(est_val)}"
        card_metrica("Pagamentos + Estornos", fmt_moeda(pag_val + est_val), subtitle, accent=_COR_RECEITA)
    with c7:
        card_metrica("Parceladas", str(sum(1 for r in rows if r.get("installment_total", 1) > 1)), "Compras com parcela maior que 1.", accent=_COR_INVEST)
    with c8:
        cards = sorted({str(r.get("card_final")) for r in rows if r.get("card_final")})
        card_metrica("Cartões", str(len(cards)), ", ".join(cards)[:42] or "-", accent=_COR_INVEST)

    preview = pd.DataFrame([
        {
            "Compra": r["purchase_date"].strftime("%d/%m/%Y"),
            "Vencimento": r["due_date"].strftime("%d/%m/%Y"),
            "Final": r["card_final"],
            "Categoria": r["category"],
            "Descrição": r["description_raw"],
            "Parcela": r["installment_label"],
            "Tipo": "despesa" if r["type"] == "expense" else "crédito",
            "Valor (R$)": r["value_brl"],
        }
        for r in rows[:80]
    ])
    st.dataframe(
        preview,
        hide_index=True,
        width="stretch",
        column_config={
            "Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
        },
    )
    if len(rows) > 80:
        st.caption(f"Exibindo 80 de {len(rows)} linhas da prévia.")

    col_imp, col_fix, col_del = st.columns([3, 1, 1], gap="small")
    with col_imp:
        importar = st.button(
            "Importar fatura",
            type="primary",
            disabled=(not account_id or not rows or bool(parsed.get("errors"))),
            width="stretch",
            key="settings_cc_invoice_import_btn",
        )
    with col_fix:
        fix_btn = st.button(
            "🔧 Corrigir pagamentos",
            disabled=not account_id,
            width="stretch",
            key="settings_cc_fix_payments_btn",
            help="Reclassifica 'Inclusão de Pagamento' e 'Pag Fatura Boleto' "
                 "que foram importados incorretamente como estornos.",
        )
    with col_del:
        del_btn = st.button(
            "🗑️ Limpar fatura",
            disabled=not account_id,
            width="stretch",
            key="settings_cc_clear_btn",
            help="Apaga os lançamentos CSV desta fatura (vencimento selecionado) "
                 "para reimportação limpa.",
        )

    # ── Confirmação antes de limpar ──────────────────────────────────────────
    if del_btn and account_id:
        st.session_state["cc_confirm_clear"] = True

    if st.session_state.get("cc_confirm_clear"):
        st.warning(
            f"⚠️ Isso apagará **todos** os lançamentos CSV da fatura "
            f"com vencimento **{due_date.strftime('%d/%m/%Y')}** do cartão selecionado. "
            "Após apagar, reimporte o CSV para recriar os dados corretamente."
        )
        c_yes, c_no = st.columns(2)
        with c_yes:
            if st.button("✅ Confirmar limpeza", key="cc_confirm_yes", width="stretch"):
                with st.spinner("Apagando lançamentos..."):
                    deleted = limpar_transacoes_cartao(account_id, due_date)
                st.session_state["cc_confirm_clear"] = False
                st.cache_data.clear()
                if deleted:
                    st.success(
                        f"✅ {deleted} lançamento(s) apagado(s). "
                        "Agora clique em **Importar fatura** para reimportar."
                    )
                else:
                    st.info("Nenhum lançamento encontrado para esta fatura.")
                st.rerun()
        with c_no:
            if st.button("❌ Cancelar", key="cc_confirm_no", width="stretch"):
                st.session_state["cc_confirm_clear"] = False
                st.rerun()

    if fix_btn and account_id:
        with st.spinner("Corrigindo classificação dos pagamentos..."):
            n = corrigir_classificacao_pagamentos_fatura(account_id)
        if n:
            st.success(f"✅ {n} pagamento(s) de fatura reclassificado(s) corretamente.")
            st.cache_data.clear()
        else:
            st.info("Nenhum lançamento incorreto encontrado — dados já estão corretos.")

    if importar:
        result = importar_fatura_cartao_csv(file_bytes, due_date, account_id)
        result["file_name"] = uploaded.name
        st.session_state["cc_invoice_import_result"] = result
        if result.get("ok"):
            st.rerun()
        _mostrar_falha(result)

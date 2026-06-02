"""
UI de upload e revisao de extratos bancarios em PDF.

O upload fica em Configuracoes; os movimentos classificados sao publicados em
transactions e passam a aparecer no Controle Financeiro.
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from core.bank_statement_import import (
    SUPPORTED_BANKS,
    confirm_bank_statement_movement,
    get_bank_statement_accounts,
    get_bank_statement_categories,
    get_bank_statement_review_rows,
    import_bank_statement_pdf,
    preview_bank_statement_pdf,
)
from core.config import settings
from core.utils import fmt_moeda


_COR_RECEITA = "#00C896"
_COR_DESPESA = "#FC5C7D"
_COR_INVEST = "#4A9EFF"
_COR_NEUTRO = "#9CA3AF"


def _safe(value: object) -> str:
    return html.escape(str(value or ""))


def _fmt_date(value: object) -> str:
    return value.strftime("%d/%m/%Y") if hasattr(value, "strftime") else "-"


def _kpi_card(title: str, value: str, subtitle: str, color: str) -> str:
    return f"""
    <div style="
        background:#111827;
        border:1px solid #1F2937;
        border-radius:8px;
        padding:14px 16px;
        min-height:96px;">
        <div style="font-size:0.70rem;font-weight:800;letter-spacing:0.12em;
                    text-transform:uppercase;color:#7890B2;">{_safe(title)}</div>
        <div style="font-size:1.30rem;font-weight:900;color:{color};
                    margin-top:10px;line-height:1.1;">{_safe(value)}</div>
        <div style="font-size:0.76rem;color:#52607A;margin-top:8px;
                    line-height:1.3;">{_safe(subtitle)}</div>
    </div>
    """


def _summary_cards(summary: dict, file_name: str | None = None) -> None:
    c1, c2, c3, c4 = st.columns(4, gap="small")
    with c1:
        st.markdown(
            _kpi_card(
                "Arquivo",
                (file_name or "Extrato")[:24],
                f"{int(summary.get('rows', 0))} movimento(s) lido(s)",
                _COR_NEUTRO,
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            _kpi_card(
                "Entradas",
                fmt_moeda(summary.get("total_entradas", 0.0)),
                f"{int(summary.get('entradas', 0))} movimento(s)",
                _COR_RECEITA,
            ),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            _kpi_card(
                "Saidas",
                fmt_moeda(summary.get("total_saidas", 0.0)),
                f"{int(summary.get('saidas', 0))} movimento(s)",
                _COR_DESPESA,
            ),
            unsafe_allow_html=True,
        )
    with c4:
        st.markdown(
            _kpi_card(
                "Pendentes",
                str(int(summary.get("pendentes", 0))),
                f"{int(summary.get('classificados', 0))} classificados",
                _COR_INVEST if int(summary.get("pendentes", 0)) == 0 else "#F6C90E",
            ),
            unsafe_allow_html=True,
        )

    start = summary.get("periodo_inicio")
    end = summary.get("periodo_fim")
    if start and end:
        st.caption(f"Periodo identificado: {_fmt_date(start)} a {_fmt_date(end)}.")


def _preview_dataframe(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Data": _fmt_date(row.get("data_movimento")),
                "Banco": row.get("banco") or "-",
                "Tipo banco": row.get("tipo_original_banco") or "-",
                "Descricao": row.get("descricao_original") or "-",
                "Direcao": row.get("direcao") or "-",
                "Categoria sugerida": row.get("categoria_nome") or row.get("categoria_sugerida_texto") or "Pendente",
                "Status": row.get("status_classificacao") or "pendente",
                "Confianca": row.get("confianca_classificacao") or 0.0,
                "Valor (R$)": row.get("valor") or 0.0,
            }
            for row in rows
        ]
    )


def _render_upload() -> None:
    st.subheader("Upload de Extrato Bancario")
    st.caption("Importe PDFs de movimentacoes bancarias. O padrao inicial suportado e C6 Bank.")

    if settings.MOCK_MODE:
        st.warning("Modo mock ativo: a previa funciona, mas a gravacao no Supabase fica desabilitada.")

    last_result = st.session_state.get("bank_statement_import_result")
    if last_result:
        if last_result.get("ok"):
            st.success(last_result.get("message", "Extrato importado."))
        else:
            st.error(last_result.get("message", "Falha ao importar extrato."))

    accounts = get_bank_statement_accounts()
    banco = st.selectbox("Banco", SUPPORTED_BANKS, key="bank_statement_bank")
    uploaded = st.file_uploader(
        "Arquivo PDF do extrato",
        type=["pdf"],
        key="bank_statement_pdf_upload",
        help="Extrato bancario em PDF. No momento, o parser foi calibrado para C6 Bank.",
    )

    if accounts:
        account_index = st.selectbox(
            "Conta bancaria de destino",
            range(len(accounts)),
            format_func=lambda idx: accounts[idx]["nome"],
            key="bank_statement_account",
        )
        account_id = accounts[account_index]["id"]
    else:
        st.warning("Cadastre uma conta bancaria antes de importar extratos.")
        account_id = None

    if uploaded is None:
        st.caption("Selecione um PDF para visualizar a previa antes de gravar.")
        return

    file_bytes = uploaded.getvalue()
    with st.spinner("Lendo PDF e classificando movimentos..."):
        parsed = preview_bank_statement_pdf(file_bytes, uploaded.name, banco=banco)

    for err in parsed.get("errors", [])[:5]:
        st.error(err)
    rows = parsed.get("rows", [])
    if not rows:
        return

    _summary_cards(parsed.get("summary", {}), uploaded.name)
    st.dataframe(
        _preview_dataframe(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
            "Confianca": st.column_config.NumberColumn("Confianca", format="%.2f"),
        },
    )

    if st.button(
        "Importar extrato",
        type="primary",
        use_container_width=True,
        disabled=(not account_id or not rows or settings.MOCK_MODE),
        key="bank_statement_import_btn",
    ):
        result = import_bank_statement_pdf(file_bytes, uploaded.name, account_id, banco=banco)
        st.session_state["bank_statement_import_result"] = result
        if result.get("ok"):
            st.rerun()
        st.error(result.get("message", "Falha ao importar extrato."))


def _review_label(row: dict) -> str:
    value = fmt_moeda(row.get("valor") or 0.0)
    status = row.get("status_classificacao") or "pendente"
    desc = str(row.get("descricao_original") or "")[:64]
    return f"{_fmt_date(row.get('data_movimento'))} · {value} · {status} · {desc}"


def _category_label(category: dict) -> str:
    return f"{category.get('nome')} ({category.get('tipo')})"


def _render_review_queue() -> None:
    st.markdown("### Revisao de movimentacoes importadas")
    st.caption("Corrija categorias pendentes ou confirme sugestoes usando apenas categorias ja existentes no App4.")

    c1, c2, c3 = st.columns([1, 1, 1], gap="small")
    with c1:
        status_filter = st.selectbox(
            "Status",
            ["pendente", "sugerida", "confirmada", "Todos"],
            key="bank_statement_review_status",
        )
    with c2:
        year_filter = st.number_input("Ano", min_value=2020, max_value=2100, value=2026, step=1, key="bank_statement_review_year")
    with c3:
        month_filter = st.selectbox(
            "Mes",
            ["Todos"] + list(range(1, 13)),
            key="bank_statement_review_month",
        )

    rows = get_bank_statement_review_rows(
        status=status_filter,
        ano=int(year_filter) if year_filter else None,
        mes=None if month_filter == "Todos" else int(month_filter),
        limit=300,
    )
    if not rows:
        st.caption("Nenhuma movimentacao encontrada para os filtros atuais.")
        return

    df = pd.DataFrame(
        [
            {
                "Data": _fmt_date(row.get("data_movimento")),
                "Banco": row.get("banco"),
                "Descricao": row.get("descricao_original"),
                "Direcao": row.get("direcao"),
                "Categoria": row.get("categoria_confirmada_nome") or row.get("categoria_nome") or row.get("categoria_sugerida_texto") or "Pendente",
                "Status": row.get("status_classificacao"),
                "Valor (R$)": row.get("valor"),
            }
            for row in rows
        ]
    )
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={"Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f")},
    )

    categories = get_bank_statement_categories()
    accounts = get_bank_statement_accounts()
    if not categories or not accounts:
        st.warning("Categorias ou contas indisponiveis para confirmacao.")
        return

    editable_rows = [row for row in rows if row.get("status_classificacao") in {"pendente", "sugerida"}]
    if not editable_rows:
        st.caption("Nao ha movimentos pendentes ou sugeridos para confirmar neste filtro.")
        return

    selected_idx = st.selectbox(
        "Movimento para revisar",
        range(len(editable_rows)),
        format_func=lambda idx: _review_label(editable_rows[idx]),
        key="bank_statement_review_row",
    )
    selected = editable_rows[selected_idx]

    category_idx = st.selectbox(
        "Categoria real do App4",
        range(len(categories)),
        format_func=lambda idx: _category_label(categories[idx]),
        key="bank_statement_review_category",
    )
    suggested_account_idx = next(
        (idx for idx, account in enumerate(accounts) if str(account.get("id")) == str(selected.get("account_id"))),
        0,
    )
    account_idx = st.selectbox(
        "Conta",
        range(len(accounts)),
        index=suggested_account_idx,
        format_func=lambda idx: accounts[idx]["nome"],
        key="bank_statement_review_account",
    )
    col_rule, col_keyword = st.columns([1, 2], gap="small")
    with col_rule:
        save_rule = st.checkbox("Salvar regra", value=True, key="bank_statement_save_rule")
    with col_keyword:
        keyword = st.text_input(
            "Palavra-chave da regra",
            value=str(selected.get("descricao_original") or "").split(" R$")[0][:80],
            key="bank_statement_rule_keyword",
        )

    if st.button("Confirmar classificacao", type="primary", use_container_width=True, key="bank_statement_confirm_btn"):
        ok, msg = confirm_bank_statement_movement(
            selected["id"],
            categories[category_idx]["id"],
            account_id=accounts[account_idx]["id"],
            save_rule=save_rule,
            palavra_chave=keyword,
        )
        if ok:
            st.success("Classificacao confirmada e publicada no Controle Financeiro.")
            st.rerun()
        st.error(msg or "Falha ao confirmar classificacao.")


def render_upload_extrato_bancario() -> None:
    """Renderiza o fluxo exclusivo de upload/revisao de extratos bancarios."""
    _render_upload()
    st.divider()
    _render_review_queue()

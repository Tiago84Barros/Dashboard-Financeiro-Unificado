"""
UI de upload e revisão de extratos bancários em PDF.

O upload fica em Configurações; os movimentos classificados são publicados em
transactions e passam a aparecer no Controle Financeiro.
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from core.bank_statement_import import (
    SUPPORTED_BANKS,
    confirm_bank_statement_movement,
    get_bank_statement_categories,
    get_bank_statement_review_rows,
    import_bank_statement_rows,
    preview_bank_statement_pdf,
)
from core.config import settings
from core.utils import fmt_moeda
from design.componentes import card_metrica


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
                "Saídas",
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
        st.caption(f"Período identificado: {_fmt_date(start)} a {_fmt_date(end)}.")


def _preview_dataframe(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Data": _fmt_date(row.get("data_movimento")),
                "Banco": row.get("banco") or "-",
                "Tipo banco": row.get("tipo_original_banco") or "-",
                "Descrição": row.get("descricao_original") or "-",
                "Direção": row.get("direcao") or "-",
                "Categoria sugerida": row.get("categoria_nome") or row.get("categoria_sugerida_texto") or "Pendente",
                "Status": row.get("status_classificacao") or "pendente",
                "Confiança": row.get("confianca_classificacao") or 0.0,
                "Valor (R$)": row.get("valor") or 0.0,
            }
            for row in rows
        ]
    )


def _render_diagnostics(parsed: dict) -> None:
    """Diagnostico de depuracao quando nenhum movimento foi identificado."""
    extract = parsed.get("diagnostics") or {}
    parse = parsed.get("parse_diagnostics") or {}
    if not extract and not parse:
        return
    with st.expander("Diagnóstico da leitura do PDF", expanded=True):
        cols = st.columns(4, gap="small")
        with cols[0]:
            card_metrica("Páginas", extract.get("n_pages", "-"), accent="#4A9EFF")
        with cols[1]:
            card_metrica("Caracteres", parse.get("n_chars", extract.get("n_chars", 0)),
                         accent="#4A9EFF")
        with cols[2]:
            card_metrica("Linhas candidatas", parse.get("n_linhas_candidatas", 0),
                         accent="#F6C90E")
        with cols[3]:
            card_metrica("Movimentos válidos", parse.get("n_movimentos_validos", 0),
                         accent="#00C896")

        engine = extract.get("engine")
        if engine:
            st.caption(f"Motor de extração usado: {engine}.")
        if extract.get("scanned"):
            st.warning(
                "O PDF parece ser escaneado/imagem (texto pesquisavel quase nulo)."
            )
        motivos = parse.get("motivos_descarte") or {}
        if motivos:
            st.caption("Motivos de descarte das linhas:")
            st.dataframe(
                pd.DataFrame(
                    [{"Motivo": k, "Ocorrencias": v} for k, v in motivos.items()]
                ),
                hide_index=True,
                use_container_width=True,
            )


def _render_upload(*, show_header: bool = True) -> None:
    if show_header:
        st.subheader("Upload de Extrato Bancário")
        st.caption("Importe PDFs de movimentações bancárias. O padrão inicial suportado é C6 Bank.")

    if settings.MOCK_MODE:
        st.warning("Modo mock ativo: a prévia funciona, mas a gravação no Supabase fica desabilitada.")

    last_result = st.session_state.get("bank_statement_import_result")
    if last_result:
        if last_result.get("ok"):
            st.success(last_result.get("message", "Extrato importado."))
        else:
            st.error(last_result.get("message", "Falha ao importar extrato."))

    banco = st.selectbox("Banco", SUPPORTED_BANKS, key="bank_statement_bank")
    uploaded = st.file_uploader(
        "Arquivo PDF do extrato",
        type=["pdf"],
        key="bank_statement_pdf_upload",
        help="Extrato bancário em PDF. No momento, o parser foi calibrado para C6 Bank.",
    )

    if uploaded is None:
        st.caption("Selecione um PDF para visualizar a prévia antes de gravar.")
        return

    file_bytes = uploaded.getvalue()
    with st.spinner("Lendo PDF e classificando movimentos..."):
        parsed = preview_bank_statement_pdf(file_bytes, uploaded.name, banco=banco)

    for err in parsed.get("errors", [])[:5]:
        st.error(err)
    rows = parsed.get("rows", [])
    if not rows:
        _render_diagnostics(parsed)
        return

    _summary_cards(parsed.get("summary", {}), uploaded.name)

    categories = get_bank_statement_categories()
    cat_by_name = {c["nome"]: c for c in categories}
    category_options = ["Pendente"] + list(cat_by_name.keys())

    def _row_category(row: dict) -> str:
        name = row.get("categoria_nome") or row.get("categoria_sugerida_texto")
        return name if name in cat_by_name else "Pendente"

    edit_df = pd.DataFrame(
        [
            {
                "Data": _fmt_date(row.get("data_movimento")),
                "Tipo banco": row.get("tipo_original_banco") or "-",
                "Descrição": row.get("descricao_original") or "",
                "Direção": row.get("direcao") or "saida",
                "Categoria": _row_category(row),
                "Valor (R$)": float(row.get("valor") or 0.0),
            }
            for row in rows
        ]
    )

    st.caption("Revise antes de salvar — você pode editar Descrição, Direção, Valor e Categoria.")
    edited = st.data_editor(
        edit_df,
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "Data": st.column_config.TextColumn("Data", disabled=True),
            "Tipo banco": st.column_config.TextColumn("Tipo banco", disabled=True),
            "Descrição": st.column_config.TextColumn("Descrição", width="large"),
            "Direção": st.column_config.SelectboxColumn("Direção", options=["entrada", "saida"], required=True),
            "Categoria": st.column_config.SelectboxColumn("Categoria", options=category_options, required=True),
            "Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f", step=0.01),
        },
        key="bank_statement_editor",
    )

    if st.button(
        "Importar extrato",
        type="primary",
        use_container_width=True,
        disabled=(not rows or settings.MOCK_MODE),
        key="bank_statement_import_btn",
    ):
        final_rows: list[dict] = []
        for idx, base in enumerate(rows):
            e = edited.iloc[idx]
            row = {**base}
            row["descricao_original"] = str(e["Descrição"] or "")[:500]
            row["direcao"] = str(e["Direção"] or base.get("direcao") or "saida")
            row["valor"] = round(float(e["Valor (R$)"] or 0.0), 2)
            cat = cat_by_name.get(str(e["Categoria"] or "Pendente"))
            if cat:
                row["categoria_id"] = cat["id"]
                row["categoria_nome"] = cat["nome"]
                row["categoria_sugerida_texto"] = cat["nome"]
                row["status_classificacao"] = "confirmada"
                row["confianca_classificacao"] = 1.0
            else:
                row["categoria_id"] = None
                row["categoria_sugerida_texto"] = None
                row["status_classificacao"] = "pendente"
                row["confianca_classificacao"] = 0.0
            final_rows.append(row)

        result = import_bank_statement_rows(final_rows, uploaded.name, banco=banco)
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
    st.markdown("### Revisão de movimentações importadas")
    st.caption("Corrija categorias pendentes ou confirme sugestões usando apenas categorias já existentes no App4.")

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
            "Mês",
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
        st.caption("Nenhuma movimentação encontrada para os filtros atuais.")
        return

    df = pd.DataFrame(
        [
            {
                "Data": _fmt_date(row.get("data_movimento")),
                "Banco": row.get("banco"),
                "Descrição": row.get("descricao_original"),
                "Direção": row.get("direcao"),
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
    if not categories:
        st.warning("Categorias indisponíveis para confirmação.")
        return

    editable_rows = [row for row in rows if row.get("status_classificacao") in {"pendente", "sugerida"}]
    if not editable_rows:
        st.caption("Não há movimentos pendentes ou sugeridos para confirmar neste filtro.")
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
    col_rule, col_keyword = st.columns([1, 2], gap="small")
    with col_rule:
        save_rule = st.checkbox("Salvar regra", value=True, key="bank_statement_save_rule")
    with col_keyword:
        keyword = st.text_input(
            "Palavra-chave da regra",
            value=str(selected.get("descricao_original") or "").split(" R$")[0][:80],
            key="bank_statement_rule_keyword",
        )

    if st.button("Confirmar classificação", type="primary", use_container_width=True, key="bank_statement_confirm_btn"):
        ok, msg = confirm_bank_statement_movement(
            selected["id"],
            categories[category_idx]["id"],
            save_rule=save_rule,
            palavra_chave=keyword,
        )
        if ok:
            st.success("Classificação confirmada e publicada no Controle Financeiro.")
            st.rerun()
        st.error(msg or "Falha ao confirmar classificação.")


def render_upload_extrato_bancario(*, show_header: bool = True) -> None:
    """Renderiza o fluxo exclusivo de upload/revisao de extratos bancarios."""
    _render_upload(show_header=show_header)
    st.divider()
    _render_review_queue()

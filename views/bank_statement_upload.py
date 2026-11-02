"""
UI de upload de extratos bancários em PDF.

O upload fica em Configurações; os movimentos classificados são publicados em
transactions e a revisão acontece no Controle Financeiro.
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from core.bank_statement_import import (
    ROTULO_PENDENTE,
    SUPPORTED_BANKS,
    get_bank_statement_categories,
    import_bank_statement_rows,
    preview_bank_statement_pdf,
    rotulo_da_linha,
    rotulos_de_categoria,
)
from core.conciliacao_bancaria import (
    JANELA_DIAS,
    conferir_saldo,
    impacto_no_saldo,
    preparar_conciliacao,
    ultimas_conferencias,
)
from core.config import settings
from core.utils import fmt_moeda
from design.lacunas import aviso_lacuna, detalhe_tecnico
from design.tema_canvas import no_claro

_COR_RECEITA = "var(--app-primary)"
_COR_DESPESA = "var(--app-danger)"
_COR_INVEST = "var(--app-info)"
_COR_NEUTRO = "var(--app-muted)"


_PREVIA_COLS = [1.1, 1.2, 2.6, 1.2, 1.7, 1.2]


def _indice_opcao(opcoes: list, valor: object) -> int:
    texto = str(valor or "")
    return opcoes.index(texto) if texto in opcoes else 0


def _editor_previa_claro(edit_df: pd.DataFrame, category_options: list) -> pd.DataFrame:
    """Prévia do extrato desenhada com widgets nativos, para o tema claro.

    O ``st.data_editor`` pinta a grade num canvas cujas cores o Streamlit monta
    em JS a partir do tema do config (escuro), e CSS não alcança (memória:
    canvas-do-data-editor-ignora-css). Data e Tipo banco são só leitura aqui
    como são na grade escura — viram texto, não widget.

    A chave usa a posição da linha: a prévia não tem id, e o quadro é
    recalculado a cada upload.
    """
    category_options = category_options or ["Pendente"]
    direcoes = ["entrada", "saida"]

    cabecalho = st.columns(_PREVIA_COLS, gap="small")
    for coluna, titulo in zip(cabecalho, ("Data", "Tipo banco", "Descrição",
                                          "Direção", "Categoria", "Valor (R$)")):
        coluna.markdown(
            f'<div style="font-size:0.68rem;font-weight:700;letter-spacing:0.05em;'
            f'text-transform:uppercase;color:var(--app-muted);padding-bottom:4px;'
            f'border-bottom:1px solid var(--app-border);">{html.escape(titulo)}</div>',
            unsafe_allow_html=True,
        )

    edited = edit_df.copy()
    for i in range(len(edit_df)):
        r = edit_df.iloc[i]
        c_data, c_tipo, c_desc, c_dir, c_cat, c_valor = st.columns(
            _PREVIA_COLS, gap="small")
        for coluna, texto in ((c_data, r["Data"]), (c_tipo, r["Tipo banco"])):
            coluna.markdown(
                f'<div style="padding-top:6px;font-size:0.8rem;color:var(--app-text);">'
                f'{html.escape(str(texto))}</div>',
                unsafe_allow_html=True,
            )
        edited.at[i, "Descrição"] = c_desc.text_input(
            "Descrição", value=str(r["Descrição"]),
            key=f"previa_desc_{i}", label_visibility="collapsed")
        edited.at[i, "Direção"] = c_dir.selectbox(
            "Direção", direcoes, index=_indice_opcao(direcoes, r["Direção"]),
            key=f"previa_dir_{i}", label_visibility="collapsed")
        edited.at[i, "Categoria"] = c_cat.selectbox(
            "Categoria", category_options,
            index=_indice_opcao(category_options, r["Categoria"]),
            key=f"previa_cat_{i}", label_visibility="collapsed")
        edited.at[i, "Valor (R$)"] = c_valor.number_input(
            "Valor (R$)", value=float(r["Valor (R$)"]), step=0.01, format="%.2f",
            key=f"previa_valor_{i}", label_visibility="collapsed")
    return edited


def _safe(value: object) -> str:
    return html.escape(str(value or ""))


def _fmt_date(value: object) -> str:
    return value.strftime("%d/%m/%Y") if hasattr(value, "strftime") else "-"


def _kpi_card(title: str, value: str, subtitle: str, color: str) -> str:
    return f"""
    <div style="
        background:var(--app-surface);
        border:1px solid var(--app-border);
        border-radius:8px;
        padding:14px 16px;
        min-height:96px;">
        <div style="font-size:0.70rem;font-weight:800;letter-spacing:0.12em;
                    text-transform:uppercase;color:var(--app-muted);">{_safe(title)}</div>
        <div style="font-size:1.30rem;font-weight:900;color:{color};
                    margin-top:10px;line-height:1.1;">{_safe(value)}</div>
        <div style="font-size:0.76rem;color:var(--app-subtle);margin-top:8px;
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
                _COR_INVEST if int(summary.get("pendentes", 0)) == 0 else "var(--app-warning)",
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
    detalhe_tecnico(
        f"Leitura do PDF: {extract.get('n_pages', '-')} página(s), "
        f"{parse.get('n_chars', extract.get('n_chars', 0))} caractere(s), "
        f"{parse.get('n_linhas_candidatas', 0)} linha(s) candidata(s), "
        f"{parse.get('n_movimentos_validos', 0)} movimento(s) válido(s); "
        f"motor de extração: {extract.get('engine') or '-'}.",
        codigo="controle.extrato_leitura_pdf",
    )
    motivos = parse.get("motivos_descarte") or {}
    if motivos:
        detalhe_tecnico(
            "Motivos de descarte das linhas: "
            + "; ".join(f"{k}={v}" for k, v in motivos.items()),
            codigo="controle.extrato_descarte_linhas",
        )
    if extract.get("scanned"):
        st.warning("O PDF parece ser escaneado ou uma imagem; não foi possível ler o texto.")


# Mensagens que descrevem o arquivo do usuário (e que ele consegue corrigir).
_MENSAGENS_DO_USUARIO = {"Extrato sem linhas validas.", "Nada para importar."}


def _mostrar_falha(result: dict) -> None:
    """Falha de importação: problema do arquivo fica na tela; configuração,
    conta técnica e banco viram frase neutra e registro para o administrador."""
    msg = result.get("message") or "Falha ao importar extrato."
    if result.get("errors") or msg in _MENSAGENS_DO_USUARIO:
        st.error(msg)
        return
    st.error("Não foi possível importar o extrato agora.")
    aviso_lacuna(
        "Importação de extrato recusada por configuração, banco ou conta de movimentação.",
        codigo="tela.controle.extrato_importacao_recusada",
    )


def _linhas_finais(rows: list[dict], edited: pd.DataFrame, cat_por_rotulo: dict) -> list[dict]:
    """Linhas do extrato com o que o usuário editou na prévia."""
    final_rows: list[dict] = []
    for idx, base in enumerate(rows):
        e = edited.iloc[idx]
        row = {**base}
        row["descricao_original"] = str(e["Descrição"] or "")[:500]
        row["direcao"] = str(e["Direção"] or base.get("direcao") or "saida")
        row["valor"] = round(float(e["Valor (R$)"] or 0.0), 2)
        cat = cat_por_rotulo.get(str(e["Categoria"] or ROTULO_PENDENTE))
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
    return final_rows


_FORCA = {"forte": "par forte", "possivel": "par possível — confira"}


def _md(texto: str) -> str:
    """Texto com R$ dentro de widget que lê markdown: dois "$" na mesma linha
    viram fórmula LaTeX e o valor some da tela."""
    return str(texto).replace("$", "\\$")


def _render_conciliacao(final_rows: list[dict], parsed: dict) -> tuple[dict[int, str], dict | None]:
    """Conciliação na prévia: vínculo com lançamentos manuais e conferência
    de saldo. Devolve ``({índice: transaction_id}, saldo_extrato | None)``.

    Sem banco a prévia segue igual e a conciliação some com uma frase -- ela
    ajuda a importação, não a bloqueia.
    """
    st.markdown("#### 🔗 Conciliação")

    detectado = (parsed.get("saldos") or {}).get("final") or {}
    datas = [r["data_movimento"] for r in final_rows if r.get("data_movimento")]
    data_padrao = detectado.get("data") or (max(datas) if datas else None)
    c_data, c_valor = st.columns(2, gap="small")
    data_saldo = c_data.date_input(
        "Data do saldo final", value=data_padrao, format="DD/MM/YYYY",
        key="conciliacao_data_saldo")
    valor_saldo = c_valor.number_input(
        "Saldo final do extrato (R$)", value=detectado.get("valor"), step=0.01,
        format="%.2f", key="conciliacao_valor_saldo",
        help="Lido do PDF quando o extrato imprime o saldo. Confira com o PDF; "
             "se veio vazio, digite o saldo do fim do período.")
    if detectado.get("valor") is not None:
        st.caption("Saldo lido do próprio extrato. O OCR pode errar centavos: confira com o PDF.")
    else:
        st.caption("O extrato não trouxe saldo legível. Digite o saldo final do período "
                   "para conferir se o app fecha com o banco.")

    ctx = preparar_conciliacao(final_rows, data_saldo if valor_saldo is not None else None)
    if not ctx.get("ok"):
        st.caption("Conciliação indisponível agora (sem banco conectado). A importação segue normal.")
        return {}, None

    st.caption(f"Conta conciliada: **{ctx['conta']['nome']}** — só lançamentos manuais "
               "desta conta entram na busca por par.")

    ja = ctx["ja_importados"]
    if ja:
        st.info(f"{len(ja)} linha(s) deste extrato já foram importadas antes e serão ignoradas.")

    escolhidos: dict[int, str] = {}
    vinculos = ctx["vinculos"]
    if vinculos:
        st.markdown(
            f"**{len(vinculos)} movimento(s) já lançado(s) à mão.** Marcado: o movimento se "
            "vincula ao lançamento existente e não cria outro. Desmarcado: vira lançamento novo.")
        for idx in sorted(vinculos):
            v = vinculos[idx]
            r = final_rows[idx]
            rotulo = (
                f"{_fmt_date(r.get('data_movimento'))} · {r.get('descricao_original') or '-'} · "
                f"{fmt_moeda(r.get('valor'))}  ↔  manual {_fmt_date(v['data'])} · "
                f"{v['descricao'] or '(sem descrição)'} ({_FORCA[v['forca']]})"
            )
            if st.checkbox(_md(rotulo), value=True, key=f"conciliacao_vinc_{idx}_{v['transaction_id']}"):
                escolhidos[idx] = v["transaction_id"]
    else:
        st.caption("Nenhum movimento deste extrato tem par entre os lançamentos manuais "
                   f"da conta (mesmo valor, até {JANELA_DIAS} dias de diferença).")

    if valor_saldo is None or data_saldo is None or ctx.get("saldo_app") is None:
        return escolhidos, None

    marcadas = [
        {**r, "ja_importado": i in ja,
         "vincular_transaction_id": escolhidos.get(i)}
        for i, r in enumerate(final_rows)
    ]
    a_lancar, pendente = impacto_no_saldo(marcadas, data_saldo)
    conf = conferir_saldo(float(valor_saldo), ctx["saldo_app"], a_lancar, pendente)

    k1, k2, k3 = st.columns(3, gap="small")
    k1.markdown(_kpi_card("Saldo do banco", fmt_moeda(conf["saldo_extrato"]),
                          f"em {_fmt_date(data_saldo)}", _COR_NEUTRO), unsafe_allow_html=True)
    k2.markdown(_kpi_card("Saldo do app após importar", fmt_moeda(conf["saldo_projetado"]),
                          f"hoje {fmt_moeda(conf['saldo_app'])} + {fmt_moeda(conf['a_lancar'])} "
                          "desta importação", _COR_INVEST), unsafe_allow_html=True)
    k3.markdown(_kpi_card("Diferença", fmt_moeda(conf["diferenca"]),
                          "banco − app", _COR_RECEITA if conf["fecha"] else _COR_DESPESA),
                unsafe_allow_html=True)

    if conf["fecha"]:
        st.success("O saldo do app fecha com o do banco nesta data.")
    elif conf["explicada_por_pendentes"]:
        st.warning(_md(
            f"A diferença é exatamente o total dos movimentos sem categoria "
            f"({fmt_moeda(conf['pendente'])}): eles ficam fora do saldo até serem classificados."))
    else:
        st.warning(_md(
            f"Diferença de {fmt_moeda(conf['diferenca'])} até {_fmt_date(data_saldo)}: algum "
            "lançamento falta ou sobra no app. Causas comuns: lançamento manual em outra conta "
            "ou com valor diferente do banco, movimento sem categoria "
            f"({fmt_moeda(conf['pendente'])} nesta importação), valor lido errado pelo OCR, "
            "ou saldo inicial da conta desatualizado."))
    return escolhidos, {"data": data_saldo, "valor": float(valor_saldo)}


def _render_ultimas_conferencias() -> None:
    """Saldos de extratos já importados, recalculados contra o app de hoje."""
    conferencias = ultimas_conferencias()
    if not conferencias:
        return
    st.markdown("#### Últimas conferências de saldo")
    st.caption("A diferença é recalculada agora: classificar um pendente ou corrigir um "
               "lançamento manual muda o resultado.")
    for c in conferencias:
        icone = "✅" if c["fecha"] else "⚠️"
        texto = (f"{icone} {_fmt_date(c['data_saldo'])} · {c['conta']} · banco "
                 f"{fmt_moeda(c['saldo_extrato'])} · app {fmt_moeda(c['saldo_app'])}")
        if not c["fecha"]:
            texto += f" · diferença {fmt_moeda(c['diferenca'])}"
        st.markdown(_md(texto))


def _render_upload(*, show_header: bool = True) -> None:
    if show_header:
        st.subheader("Upload de Extrato Bancário")
        st.caption("Importe PDFs de movimentações bancárias. O padrão inicial suportado é C6 Bank.")

    if settings.MOCK_MODE:
        st.warning("A gravação está desabilitada neste ambiente; a prévia continua disponível.")
        detalhe_tecnico("Modo mock ativo: gravação do extrato desabilitada.",
                        codigo="controle.extrato_mock")

    last_result = st.session_state.get("bank_statement_import_result")
    if last_result:
        if last_result.get("ok"):
            st.success(last_result.get("message", "Extrato importado."))
        else:
            _mostrar_falha(last_result)

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
    # Rótulo único por categoria e resolução pelo id: o nome se repete entre
    # tipos (há três "Outros"), e resolver pelo nome gravava o tipo errado.
    cat_por_rotulo, rotulo_por_id = rotulos_de_categoria(categories)
    category_options = [ROTULO_PENDENTE] + list(cat_por_rotulo.keys())

    def _row_category(row: dict) -> str:
        return rotulo_da_linha(row, rotulo_por_id)

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
    edited = _editor_previa_claro(edit_df, category_options) if no_claro() else st.data_editor(
        edit_df,
        hide_index=True,
        width="stretch",
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

    final_rows = _linhas_finais(rows, edited, cat_por_rotulo)
    vinculos_escolhidos, saldo_extrato = _render_conciliacao(final_rows, parsed)

    if st.button(
        "Importar extrato",
        type="primary",
        width="stretch",
        disabled=(not rows or settings.MOCK_MODE),
        key="bank_statement_import_btn",
    ):
        for idx, tx_id in vinculos_escolhidos.items():
            final_rows[idx]["vincular_transaction_id"] = tx_id
        result = import_bank_statement_rows(
            final_rows, uploaded.name, banco=banco, saldo_extrato=saldo_extrato)
        st.session_state["bank_statement_import_result"] = result
        if result.get("ok"):
            st.rerun()
        _mostrar_falha(result)


def render_upload_extrato_bancario(*, show_header: bool = True) -> None:
    """Renderiza o upload de extratos bancarios. Revisao nao mora mais aqui.

    A fila de revisao vivia logo abaixo do upload, e era uma segunda copia da
    que ``views/controle_financeiro.py::_render_bank_statement_section`` ja
    renderiza -- com filtros melhores e no lugar onde o movimento importa.
    Aquela secao ate anunciava a divisao ("o upload fica em Configuracoes;
    aqui entram conferencia, filtros e confirmacao") enquanto esta a
    contradizia. Nenhuma capacidade saiu do app: confirmar classificacao e
    salvar regra continuam em Controle Financeiro.

    A conciliação (vínculo com lançamento manual e conferência de saldo) mora
    aqui porque decide o que a importação grava; o histórico das conferências
    fica logo abaixo do upload.
    """
    _render_upload(show_header=show_header)
    _render_ultimas_conferencias()

"""
views/configuracoes.py
Configurações do sistema — 3 abas focadas no que o usuário realmente usa.

  🔄 Atualização  — status das fontes de dados e execução de jobs
  🗄️ Banco        — conexão, schema e importação de dados históricos
  🔒 Segurança    — sessão e autenticação
"""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from core.auth import encerrar_sessao, esta_autenticado
from core.config import settings
from core.database import get_database_storage_status, get_db_status
from design.componentes import container_pagina


def render() -> None:
    container_pagina("Configurações", "Status do sistema e atualização de dados", "⚙️")
    # CSS dos cards é injetado uma vez por render — ambas as abas usam.
    st.markdown(_CARD_CSS, unsafe_allow_html=True)

    tab_dados, tab_banco, tab_seg = st.tabs([
        "🔄 Atualização de Dados",
        "🗄️ Banco & Importação",
        "🔒 Segurança",
    ])

    with tab_dados:
        _render_atualizacao()

    with tab_banco:
        _render_banco()

    with tab_seg:
        _render_seguranca()


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 1 — Atualização de Dados
# ═══════════════════════════════════════════════════════════════════════════════

_FRESHNESS_ICON = {
    "updated":       "✅",
    "attention":     "🟡",
    "outdated":      "🔴",
    "never_updated": "⚪",
    "error":         "❌",
    "skipped":       "⚪",
}
_FRESHNESS_LABEL = {
    "updated":       "Atualizado",
    "attention":     "Atenção",
    "outdated":      "Desatualizado",
    "never_updated": "Nunca atualizado",
    "error":         "Erro",
    "skipped":       "Ignorado",
}


_CARD_CSS = """
<style>
.upd-card-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 18px;
    margin: 4px 0 28px 0;
}
.upd-card {
    min-height: 142px;
    padding: 20px 22px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: linear-gradient(145deg, var(--bg), rgba(17,24,39,.72));
    box-shadow: 0 14px 30px rgba(0,0,0,.18);
}
.upd-card-label {
    color: #A8B3C7;
    font-size: .78rem;
    font-weight: 800;
    letter-spacing: .08em;
    text-transform: uppercase;
}
.upd-card-value {
    margin-top: 12px;
    color: var(--accent);
    font-size: 2.15rem;
    line-height: 1.05;
    font-weight: 900;
}
.upd-card-detail {
    margin-top: 12px;
    color: #CBD5E1;
    font-size: .9rem;
    line-height: 1.35;
}
@media (max-width: 900px) {
    .upd-card-grid { grid-template-columns: 1fr; }
}
</style>
"""


def _update_summary_card_html(label: str, value: str, detail: str = "", tone: str = "neutral") -> str:
    colors = {
        "ok": ("#00D09C", "rgba(0,208,156,.12)", "rgba(0,208,156,.35)"),
        "warn": ("#FFB020", "rgba(255,176,32,.12)", "rgba(255,176,32,.35)"),
        "info": ("#4DA3FF", "rgba(77,163,255,.12)", "rgba(77,163,255,.35)"),
        "neutral": ("#E5E7EB", "rgba(148,163,184,.10)", "rgba(148,163,184,.25)"),
    }
    accent, bg, border = colors.get(tone, colors["neutral"])
    detail_html = f'<div class="upd-card-detail">{detail}</div>' if detail else ""
    return (
        f'<div class="upd-card" style="--accent:{accent};--bg:{bg};--border:{border};">'
        f'<div class="upd-card-label">{label}</div>'
        f'<div class="upd-card-value">{value}</div>'
        f"{detail_html}"
        "</div>"
    )


def _render_atualizacao() -> None:
    if not settings.has_database:
        st.info(
            "Configure `SUPABASE_UNIFICADO_URL` em **.env** ou em "
            "**Streamlit Secrets** para habilitar o pipeline de dados.",
            icon="ℹ️",
        )
        return

    from data_pipeline.utils.db_utils import table_exists, ensure_pipeline_tables

    pipeline_ok = (
        table_exists("data_update_registry")
        and table_exists("data_update_logs")
        and table_exists("data_freshness_status")
    )

    if not pipeline_ok:
        st.warning(
            "As tabelas do pipeline ainda não foram criadas. "
            "Isso acontece uma única vez.",
            icon="⚠️",
        )
        if st.button("Inicializar pipeline", type="primary"):
            with st.spinner("Criando tabelas..."):
                res = ensure_pipeline_tables()
            if res.get("ok"):
                st.success("Pipeline inicializado.")
                st.rerun()
            else:
                for e in res.get("erros", []):
                    st.error(e)
        return

    from data_pipeline.orchestrator import get_last_global_update
    from data_pipeline.update_registry import seed_registry, get_registry
    from data_pipeline.utils.date_utils import fmt_datetime_br

    seed_registry()
    # get_registry JOIN já traz freshness + description + frequency em uma única query
    registry_list = get_registry(active_only=True)
    ultima = get_last_global_update()

    # ── Resumo compacto ────────────────────────────────────────────────────────
    ok_count  = sum(1 for s in registry_list if (s.get("freshness_status") or "never_updated") == "updated")
    bad_count = sum(1 for s in registry_list if (s.get("freshness_status") or "never_updated") in
                    ("outdated", "error", "never_updated", "attention"))
    total_count = len(registry_list)
    ultima_fmt = fmt_datetime_br(ultima) if ultima else "Nunca"
    detalhe_bad = "Tudo em dia" if bad_count == 0 else f"{bad_count} fonte(s) exigem atenção"

    st.markdown(
        '<div class="upd-card-grid">'
        + _update_summary_card_html("Fontes OK", str(ok_count), f"{total_count} fontes ativas monitoradas", "ok")
        + _update_summary_card_html("Precisam atualizar", str(bad_count), detalhe_bad, "warn" if bad_count else "ok")
        + _update_summary_card_html("Última atualização (BRT)", ultima_fmt, "Horário de Brasília", "info")
        + "</div>",
        unsafe_allow_html=True,
    )

    st.markdown("")

    # ── O que cada fonte atualiza ──────────────────────────────────────────────
    with st.expander("ℹ️ O que é atualizado ao clicar em 'Atualizar tudo'?"):
        st.markdown("""
| O que é atualizado | Frequência |
|---|---|
| **Cotações** — preços históricos de ações e FIIs | Diária |
| **Indicadores macroeconômicos** — SELIC, IPCA, câmbio, PIB, balança comercial, ICC e dívida pública | Diária |
| **Macro consolidado** — série histórica anual dos mesmos indicadores | Mensal |
| **Documentos corporativos** — fatos relevantes, resultados trimestrais e atas de empresas listadas | Semanal |

> Transações, operações e proventos **não** são atualizados automaticamente —
> eles vêm de importação manual (aba **Banco & Importação**) ou lançamento direto no app.
        """)

    # ── Botões de execução ─────────────────────────────────────────────────────
    col_run, col_force = st.columns([1, 1])
    with col_run:
        run_all = st.button("🔄 Atualizar tudo", type="primary", use_container_width=True)
    with col_force:
        force_all = st.button(
            "⚡ Forçar atualização",
            type="secondary",
            use_container_width=True,
            help="Ignora o controle de frequência e executa todos os jobs agora",
        )

    if run_all:
        _executar_pipeline("all", force=False)
    if force_all:
        _executar_pipeline("all", force=True)

    st.divider()

    # ── Tabela de fontes ───────────────────────────────────────────────────────
    _FREQ_LABEL = {
        "diario": "Diária", "semanal": "Semanal", "mensal": "Mensal",
        "trimestral": "Trimestral", "manual": "Manual",
    }

    if registry_list:
        dados = []
        for s in registry_list:
            fs   = s.get("freshness_status") or "never_updated"
            icon = _FRESHNESS_ICON.get(fs, "?")
            lbl  = _FRESHNESS_LABEL.get(fs, fs)
            err  = (s.get("last_error_message") or "")[:80]
            desc = s.get("description") or "—"
            freq = s.get("frequency") or "—"
            try:
                registros = int(s.get("last_records_inserted") or 0)
            except (TypeError, ValueError):
                registros = 0
            dados.append({
                "O que atualiza":   desc[:60] + "…" if len(desc) > 60 else desc,
                "Frequência":       _FREQ_LABEL.get(freq, freq),
                "Status":           f"{icon} {lbl}",
                "Última OK":        fmt_datetime_br(s.get("last_success_at")),
                "Registros":        registros,
                "Erro":             err,
            })
        st.dataframe(
            pd.DataFrame(dados),
            use_container_width=True,
            hide_index=True,
            column_config={
                "O que atualiza": st.column_config.TextColumn(width="large"),
                "Frequência":     st.column_config.TextColumn(width="small"),
                "Status":         st.column_config.TextColumn(width="medium"),
                "Última OK":      st.column_config.TextColumn(width="medium"),
                "Registros":      st.column_config.NumberColumn(width="small"),
                "Erro":           st.column_config.TextColumn(width="medium"),
            },
        )
    else:
        st.info(
            "Nenhuma fonte registrada ainda. Clique em **Atualizar tudo** para inicializar.",
            icon="ℹ️",
        )

    # ── Executar job individual ────────────────────────────────────────────────
    registry = get_registry(active_only=True)
    # Importações manuais (frequency='manual') não rodam pelo orquestrador.
    # Filtramos do expander de execução individual para não exibir botão inerte.
    runnable = [it for it in registry if (it.get("frequency") or "").lower() != "manual"]
    if runnable:
        with st.expander("▶ Executar fonte individualmente"):
            cols = st.columns(min(len(runnable), 3))
            for i, item in enumerate(runnable):
                with cols[i % 3]:
                    nome = item.get("source_name", item.get("job_name", "?"))
                    if st.button(nome, use_container_width=True, key=f"_run_{item['job_name']}"):
                        _executar_pipeline(item["job_name"], force=True)

    # ── Log de execuções ───────────────────────────────────────────────────────
    with st.expander("📋 Log de execuções recentes"):
        from data_pipeline.orchestrator import get_recent_update_logs
        logs = get_recent_update_logs(limit=20)
        if logs:
            _STATUS_ICON = {"success": "✅", "partial_success": "🟡",
                            "skipped": "⚪", "failed": "❌", "running": "🔵", "error": "❌"}
            log_dados = []
            for lg in logs:
                s = lg.get("status", "?")
                log_dados.append({
                    "Início":  fmt_datetime_br(lg.get("started_at")),
                    "Job":     lg.get("job_name", "—"),
                    "Status":  f"{_STATUS_ICON.get(s, '?')} {s}",
                    "Inserts": int(lg.get("records_inserted") or 0),
                    "Falhas":  int(lg.get("records_failed") or 0),
                    "Tempo":   f"{lg.get('execution_time_seconds', 0):.1f}s"
                               if lg.get("execution_time_seconds") else "—",
                    "Erro":    (lg.get("error_message") or "")[:60],
                })
            st.dataframe(pd.DataFrame(log_dados), use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhum log registrado.")


def _executar_pipeline(update_group: str, force: bool) -> None:
    from data_pipeline.orchestrator import run_updates

    label = "todos os jobs" if update_group == "all" else update_group
    with st.spinner(f"Executando {label}…"):
        resultado = run_updates(update_group=update_group, force=force)

    status = resultado.get("status", "?")
    ok     = resultado.get("success_count", 0)
    falhou = resultado.get("failed_count", 0)
    skip   = resultado.get("skipped_count", 0)
    total  = resultado.get("total_jobs", 0)

    if status == "success":
        st.success(f"✅ {ok}/{total} concluídos · {skip} pulados por frequência")
    elif status == "partial_success":
        st.warning(f"🟡 {ok} OK · {falhou} com falha · {skip} pulados")
    elif status == "error":
        st.error(resultado.get("error", "Erro desconhecido"))
        return
    else:
        st.error(f"❌ {falhou}/{total} falharam")

    results = resultado.get("results", [])
    if results:
        with st.expander("Detalhes por job"):
            _ICON = {"success": "✅", "partial_success": "🟡", "skipped": "⚪", "failed": "❌"}
            for r in results:
                s = r.get("status", "?")
                st.markdown(
                    f"{_ICON.get(s, '?')} **{r.get('source_name', r.get('job_name', '?'))}** — "
                    f"{r.get('records_inserted', 0)} inseridos · "
                    f"{r.get('records_failed', 0)} falhas · "
                    f"{r.get('execution_time_seconds', 0):.1f}s"
                )
                if r.get("error_message"):
                    st.caption(f"Erro: {r['error_message'][:120]}")

    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 2 — Banco & Importação
# ═══════════════════════════════════════════════════════════════════════════════

def _render_banco() -> None:
    status = get_db_status()

    # ── Status de conexão ──────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        if status["conectado"]:
            st.success("Banco conectado")
        elif status["configurado"]:
            st.warning("Configurado, mas sem conexão")
        else:
            st.error("Banco não configurado")
    with col2:
        if status["mock_mode"]:
            st.warning("Modo mock — dados simulados")
        else:
            st.success("Dados reais")
    with col3:
        if settings.has_owner:
            st.success("Usuário configurado")
        else:
            st.warning("OWNER_USER_ID ausente")

    if not status["configurado"]:
        st.info(
            "Adicione `SUPABASE_UNIFICADO_URL` no arquivo **.env** local "
            "ou em **Settings > Secrets** no Streamlit Cloud.",
            icon="ℹ️",
        )
        return

    st.divider()

    # ── Schema ─────────────────────────────────────────────────────────────────
    _render_storage_health()
    st.divider()

    from etl.schema_setup import TABELAS_ESPERADAS, verificar_schema

    with st.spinner("Verificando schema…"):
        presenca = verificar_schema()

    total      = len(TABELAS_ESPERADAS)
    existentes = sum(1 for v in presenca.values() if v)
    ausentes   = total - existentes

    col_p, col_b = st.columns([3, 1])
    with col_p:
        st.progress(existentes / total if total else 0)
        st.caption(f"{existentes}/{total} tabelas presentes")
    with col_b:
        if ausentes > 0:
            if st.button(f"Criar {ausentes} tabela(s)", type="primary", use_container_width=True):
                _executar_criar_schema()
        else:
            st.success("Schema completo ✓")

    if ausentes > 0:
        faltando = [t for t in TABELAS_ESPERADAS if not presenca[t]]
        st.caption(f"Ausentes: {', '.join(faltando)}")

    st.divider()

    # ── Importação (colapsável) ────────────────────────────────────────────────
    with st.expander("📄 Importar dados de arquivo (CSV / Excel)"):
        if settings.has_database:
            _render_import_csv()
        else:
            st.warning("Banco não conectado.")

    with st.expander("🔗 Importar de banco de origem (PostgreSQL / SQLite)"):
        if settings.has_database:
            _render_import_postgres()
        else:
            st.warning("Banco não conectado.")

    # ── Importações de Investimentos (separado das financeiras acima) ─────────
    st.divider()
    st.markdown("### 📈 Importar dados de investimentos")
    st.caption(
        "Importação manual a partir de arquivos exportados pelo próprio "
        "investidor. Não realiza scraping nem login automático em corretora."
    )
    if settings.has_database:
        _render_import_investimentos()
    else:
        st.warning("Banco não conectado.")


def _render_storage_health() -> None:
    storage = get_database_storage_status()

    st.subheader("Uso do Supabase")
    st.caption(
        "Monitoramento preventivo do tamanho do banco. O limite padrão é 500 MB, "
        "ajustável por `SUPABASE_DB_LIMIT_MB`."
    )

    if not storage.get("ok"):
        st.warning(
            "Não foi possível medir o uso atual do banco. "
            "Verifique a conexão e as permissões de leitura do PostgreSQL."
        )
        return

    status = storage.get("status", "unknown")
    used_mb = float(storage.get("used_mb", 0.0))
    limit_mb = float(storage.get("limit_mb", 0.0))
    remaining_mb = float(storage.get("remaining_mb", 0.0))
    pct_used = float(storage.get("pct_used", 0.0))

    if status == "danger":
        st.error(storage.get("message"))
    elif status in {"critical", "attention"}:
        st.warning(storage.get("message"))
    else:
        st.success(storage.get("message"))

    # Tons dos cards conforme o status de uso. "Uso atual" e "Espaço livre"
    # acompanham o nível de risco; "Limite monitorado" é sempre informacional.
    if status == "danger":
        tone_uso, tone_livre = "warn", "warn"
    elif status in {"critical", "attention"}:
        tone_uso, tone_livre = "warn", "warn"
    else:
        tone_uso, tone_livre = "ok", "ok"

    detalhe_uso = f"{pct_used:.1f}% da cota monitorada"
    detalhe_limite = (
        "Plano Free do Supabase — ajustável por `SUPABASE_DB_LIMIT_MB`"
    )
    if remaining_mb < 50:
        detalhe_livre = "Margem apertada — planejar limpeza ou upgrade"
    elif remaining_mb < 150:
        detalhe_livre = "Margem confortável, mas vale acompanhar"
    else:
        detalhe_livre = "Margem ampla para novos dados"

    st.markdown(
        '<div class="upd-card-grid">'
        + _update_summary_card_html("Uso atual", f"{used_mb:.1f} MB", detalhe_uso, tone_uso)
        + _update_summary_card_html("Limite monitorado", f"{limit_mb:.0f} MB", detalhe_limite, "info")
        + _update_summary_card_html("Espaço livre", f"{remaining_mb:.1f} MB", detalhe_livre, tone_livre)
        + "</div>",
        unsafe_allow_html=True,
    )

    st.progress(
        min(max(pct_used / 100, 0.0), 1.0),
        text=f"{pct_used:.1f}% da cota monitorada",
    )

    top_tables = storage.get("top_tables") or []
    if top_tables:
        with st.expander("Maiores tabelas do banco"):
            df = pd.DataFrame(top_tables).rename(
                columns={
                    "table_name": "Tabela",
                    "total_mb": "Tamanho (MB)",
                    "total_bytes": "Bytes",
                }
            )
            df["Tamanho (MB)"] = df["Tamanho (MB)"].map(lambda value: f"{float(value):.2f}")
            st.dataframe(
                df[["Tabela", "Tamanho (MB)", "Bytes"]],
                use_container_width=True,
                hide_index=True,
            )


def _executar_criar_schema() -> None:
    from etl.schema_setup import criar_schema
    with st.spinner("Criando tabelas…"):
        result = criar_schema()
    if result["ok"]:
        criadas = result.get("criadas", [])
        st.success(f"Criadas: {', '.join(criadas)}" if criadas else "Nenhuma tabela nova.")
    else:
        st.error("Erros ao criar schema:")
        for e in result["erros"]:
            st.code(e)
    st.rerun()


def _render_import_csv() -> None:
    st.caption("Exporte do app original em CSV e importe aqui. Use simulação antes de gravar.")

    tipo_dados = st.selectbox(
        "Tipo de dados",
        ["transacoes", "operacoes", "proventos"],
        format_func=lambda x: {
            "transacoes": "Transações financeiras (receitas/despesas)",
            "operacoes":  "Operações de investimento (compra/venda)",
            "proventos":  "Proventos (dividendos, JCP, rendimentos)",
        }[x],
        key="_csv_tipo",
    )

    arquivo = st.file_uploader(
        "Arquivo", type=["csv", "xlsx", "xls"], key="_csv_upload",
        help="CSV (vírgula ou ponto-vírgula) e Excel.",
    )

    if arquivo is None:
        _mostrar_template_csv(tipo_dados)
        return

    try:
        if arquivo.name.endswith(".csv"):
            conteudo = arquivo.read()
            df = None
            for sep in [",", ";"]:
                try:
                    _df = pd.read_csv(io.BytesIO(conteudo), sep=sep)
                    if len(_df.columns) > 1:
                        df = _df
                        break
                except Exception:
                    continue
            if df is None:
                df = pd.read_csv(io.BytesIO(conteudo))
        else:
            df = pd.read_excel(arquivo)
    except Exception as exc:
        st.error(f"Erro ao ler arquivo: {exc}")
        return

    st.success(f"{len(df)} linhas · {len(df.columns)} colunas")
    st.dataframe(df.head(5), use_container_width=True)

    usuario_id = settings.OWNER_USER_ID or st.text_input(
        "OWNER_USER_ID", placeholder="UUID do usuário", key="_csv_owner"
    )
    conta_id = ""
    if tipo_dados == "transacoes":
        conta_id = st.text_input("conta_id", placeholder="UUID da conta", key="_csv_conta")

    dry_run = st.toggle("Simulação (dry run)", value=True, key="_csv_dry")
    if dry_run:
        st.info("Dry run: dados validados mas não gravados.", icon="ℹ️")

    if st.button("Simular" if dry_run else "GRAVAR NO BANCO",
                 type="secondary" if dry_run else "primary", key="_csv_exec"):
        if not usuario_id:
            st.error("Informe o OWNER_USER_ID.")
            return
        if tipo_dados == "transacoes" and not conta_id:
            st.error("Informe o conta_id.")
            return
        from etl.importacao import ImportadorCSV
        imp = ImportadorCSV()
        with st.spinner("Processando…"):
            if tipo_dados == "transacoes":
                res = imp.importar_transacoes(df, usuario_id, conta_id, dry_run=dry_run)
            elif tipo_dados == "operacoes":
                res = imp.importar_operacoes(df, usuario_id, dry_run=dry_run)
            else:
                res = imp.importar_proventos(df, usuario_id, dry_run=dry_run)
        if res.ok:
            st.success(res.resumo())
        else:
            st.error(res.resumo())
            with st.expander("Ver erros"):
                for e in res.erros:
                    st.code(e)


def _mostrar_template_csv(tipo: str) -> None:
    templates = {
        "transacoes": pd.DataFrame({
            "descricao":        ["Supermercado", "Salário"],
            "valor":            [-250.00, 8500.00],
            "data_competencia": ["2026-05-01", "2026-05-05"],
            "tipo":             ["despesa", "receita"],
            "status":           ["liquidado", "liquidado"],
            "origem":           ["csv", "csv"],
        }),
        "operacoes": pd.DataFrame({
            "ticker":         ["IVVB11", "PETR4"],
            "tipo":           ["compra", "compra"],
            "quantidade":     [10, 100],
            "preco_unitario": [290.50, 38.20],
            "taxas":          [0.00, 0.50],
            "data_operacao":  ["2026-01-15", "2026-02-10"],
            "corretora":      ["Clear", "XP"],
        }),
        "proventos": pd.DataFrame({
            "ticker":         ["MXRF11", "ITUB4"],
            "tipo":           ["rendimento", "dividendo"],
            "valor_por_cota": [0.10, 0.50],
            "quantidade":     [200, 150],
            "valor_total":    [20.00, 75.00],
            "data_pagamento": ["2026-05-15", "2026-05-20"],
        }),
    }
    df_t = templates[tipo]
    st.caption("Colunas esperadas:")
    st.dataframe(df_t, use_container_width=True)
    st.download_button(
        f"Baixar template {tipo}.csv",
        df_t.to_csv(index=False).encode("utf-8"),
        file_name=f"template_{tipo}.csv",
        mime="text/csv",
        key=f"_tpl_{tipo}",
    )


def _render_import_postgres() -> None:
    st.caption("Conecte a um banco de origem para importar dados históricos.")

    fontes = {
        "App 1 — Dashboard B3":          settings.SOURCE_DB_APP1,
        "App 2 — Investimentos (SQLite)": settings.SOURCE_DB_APP2,
        "App 3 — Controle Financeiro":    settings.url_origem_controle,
        "Outra (informar URL)":           None,
    }

    fonte = st.selectbox("Fonte", list(fontes.keys()), key="_pg_fonte")
    url = fontes[fonte]

    if url is None:
        url = st.text_input(
            "Connection string",
            type="password",
            placeholder="postgresql://usuario:senha@host:5432/banco",
            key="_pg_url",
        )
    elif url:
        st.success(f"URL configurada para {fonte} ✓")
    else:
        st.warning(f"URL não configurada para {fonte}. Informe manualmente ou adicione ao .env.")
        url = st.text_input("Connection string", type="password", key="_pg_url_manual")

    if not url:
        return

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Testar conexão", key="_pg_test"):
            _testar_conexao_fonte(url)
    with col2:
        if st.button("Listar tabelas", key="_pg_list"):
            _listar_tabelas_fonte(url)

    st.markdown("---")
    _render_import_generica(url)


def _testar_conexao_fonte(url: str) -> None:
    from etl.importacao import ImportadorPostgres
    with st.spinner("Conectando…"):
        imp = ImportadorPostgres(url)
    if imp.conectado:
        st.success(f"Conexão OK — {len(imp.listar_tabelas())} tabela(s)")
    else:
        st.error("Falha na conexão")


def _listar_tabelas_fonte(url: str) -> None:
    from etl.importacao import ImportadorPostgres
    imp = ImportadorPostgres(url)
    if not imp.conectado:
        st.error("Sem conexão com a fonte")
        return
    tabelas = imp.listar_tabelas()
    if not tabelas:
        st.info("Nenhuma tabela encontrada.")
        return
    dados = [{"Tabela": t, "Colunas": ", ".join(imp.listar_colunas(t)[:8])}
             for t in sorted(tabelas)]
    st.dataframe(pd.DataFrame(dados), use_container_width=True, hide_index=True)


def _render_import_generica(url_fonte: str) -> None:
    st.caption("Mapeamento manual: escolha origem, destino e o mapeamento de colunas.")
    col1, col2 = st.columns(2)
    with col1:
        tabela_fonte = st.text_input("Tabela na fonte", placeholder="ex: lancamentos", key="_gi_src")
    with col2:
        from etl.schema_setup import TABELAS_ESPERADAS
        tabela_destino = st.selectbox("Tabela destino", TABELAS_ESPERADAS, key="_gi_dst")

    mapeamento_txt = st.text_area(
        "Mapeamento (destino=fonte, uma linha por campo)",
        height=100,
        placeholder="descricao=descricao\nvalor=valor\ndata_competencia=data",
        key="_gi_map",
    )
    dry_run = st.toggle("Simulação (dry run)", value=True, key="_gi_dry")

    if st.button("Executar", type="secondary" if dry_run else "primary", key="_gi_exec"):
        if not tabela_fonte or not mapeamento_txt:
            st.error("Informe a tabela e o mapeamento.")
            return
        mapeamento = {}
        for linha in mapeamento_txt.strip().splitlines():
            if "=" in linha:
                d, f = linha.split("=", 1)
                mapeamento[d.strip()] = f.strip()
        if not mapeamento:
            st.error("Mapeamento inválido.")
            return
        from etl.importacao import ImportadorPostgres
        with st.spinner("Importando…"):
            imp = ImportadorPostgres(url_fonte)
            if not imp.conectado:
                st.error("Sem conexão com a fonte.")
                return
            res = imp.importar_tabela_generica(
                tabela_fonte=tabela_fonte,
                tabela_destino=tabela_destino,
                mapeamento=mapeamento,
                filtro_sql="",
                dry_run=dry_run,
            )
        if res.ok:
            st.success(res.resumo())
        else:
            st.error(res.resumo())
            with st.expander("Erros"):
                for e in res.erros:
                    st.code(e)


# ═══════════════════════════════════════════════════════════════════════════════
# Importação de Investimentos (B3 Negociação, B3 Movimentação, XP, Nomad)
# ═══════════════════════════════════════════════════════════════════════════════

_INVESTIMENTO_UPLOADS: list[dict[str, str]] = [
    {
        "key":         "b3_neg",
        "label":       "B3 — Negociação (.xlsx)",
        "help":        "investidor.b3.com.br → Extratos e Informativos → Negociação",
        "file_types":  "xlsx",
        "parser_attr": "parse_b3_negociacao",
        "job_name":    "import_b3_negociacao",
        "table_name":  "investment_transactions",
        "source_name": "B3 — Negociação (manual)",
    },
    {
        "key":         "b3_mov",
        "label":       "B3 — Movimentação (.xlsx)",
        "help":        "investidor.b3.com.br → Extratos e Informativos → Movimentação",
        "file_types":  "xlsx",
        "parser_attr": "parse_b3_movimentacao",
        "job_name":    "import_b3_movimentacao",
        "table_name":  "dividends, investment_transactions",
        "source_name": "B3 — Movimentação (manual)",
    },
    {
        "key":         "xp_csl",
        "label":       "XP — Relatório Consolidado (.xlsx)",
        "help":        "Relatórios consolidados mensais ou anuais exportados pela "
                       "XP. Aceita vários arquivos de uma vez — cada um cria um "
                       "snapshot da carteira na data inferida do nome do arquivo.",
        "file_types":  "xlsx",
        "parser_attr": "parse_xp_consolidado",
        "job_name":    "import_xp_consolidado",
        "table_name":  "portfolio_position_snapshots",
        "source_name": "XP — Consolidado (manual)",
        "needs_filename": True,
        "multi_file":  True,
    },
    {
        "key":         "nomad",
        "label":       "Nomad — Notas (.pdf)",
        "help":        "PDFs de negociação exportados pela Nomad. Aceita "
                       "vários arquivos de uma vez — todos são importados no "
                       "mesmo lote, com resumo consolidado.",
        "file_types":  "pdf",
        "parser_attr": "parse_nomad_pdf",
        "job_name":    "import_nomad_pdf",
        "table_name":  "investment_transactions",
        "source_name": "Nomad — Notas PDF (manual)",
        "multi_file":  True,
    },
]


def _render_import_investimentos() -> None:
    """Seção de importação manual de investimentos (B3, XP, Nomad)."""
    st.info(
        "🔒 A importação usa apenas arquivos exportados. O app **não solicita "
        "senha** da B3, XP, Nomad ou banco.",
        icon="🔒",
    )

    for cfg in _INVESTIMENTO_UPLOADS:
        _render_import_block(cfg)
        st.markdown("")  # respiro entre blocos

    # Botão para recalcular posições manualmente — útil quando o usuário
    # adicionou transações por outro caminho (CSV, banco origem, edição direta)
    # e quer ver a Carteira atualizada.
    st.markdown("")
    with st.container(border=True):
        col_txt, col_btn = st.columns([3, 1])
        with col_txt:
            st.markdown("**📊 Recalcular carteira agora**")
            st.caption(
                "Recomputa `portfolio_positions` a partir de todas as "
                "`investment_transactions` (custo médio ponderado). "
                "É chamado automaticamente após cada importação, mas você "
                "pode forçar manualmente."
            )
        with col_btn:
            recompute_clicked = st.button(
                "Recalcular",
                type="secondary",
                use_container_width=True,
                key="_inv_recompute_btn",
                disabled=not settings.OWNER_USER_ID,
            )

        if recompute_clicked:
            from core.database import get_engine
            from data_pipeline.importers.investments.positions import (
                recompute_for_user,
            )
            engine = get_engine()
            if engine is None:
                st.error("Banco não configurado.")
            else:
                with st.spinner("Recalculando carteira…"):
                    rec = recompute_for_user(engine, settings.OWNER_USER_ID)
                st.session_state["_inv_recompute_result"] = rec

        rec = st.session_state.get("_inv_recompute_result")
        if rec:
            if rec.get("ok"):
                st.success(
                    f"✅ {rec.get('positions_upserted', 0)} posicoes "
                    f"recalculadas a partir de "
                    f"{rec.get('transactions_loaded', 0)} operacoes."
                )
                if rec.get("alerts"):
                    with st.expander("Alertas"):
                        for msg in rec["alerts"]:
                            st.caption(msg)
            else:
                st.error(f"Falha: {rec.get('error', 'erro desconhecido')}")


def _render_import_block(cfg: dict) -> None:
    """Bloco de upload + ação + resultado para uma única fonte."""
    multi = bool(cfg.get("multi_file"))

    with st.container(border=True):
        st.markdown(f"**{cfg['label']}**")
        st.caption(cfg["help"])

        col_up, col_btn = st.columns([3, 1])
        with col_up:
            uploaded = st.file_uploader(
                "Arquivo(s)" if multi else "Arquivo",
                type=[cfg["file_types"]],
                key=f"_inv_upl_{cfg['key']}",
                label_visibility="collapsed",
                accept_multiple_files=multi,
            )
        # Quando multi_file, `uploaded` é list (pode ser vazia) ou None
        if multi:
            has_files = bool(uploaded)
            n_files = len(uploaded) if uploaded else 0
        else:
            has_files = uploaded is not None
            n_files = 1 if uploaded is not None else 0

        with col_btn:
            btn_label = "Importar" if n_files <= 1 else f"Importar {n_files} arquivos"
            run = st.button(
                btn_label,
                type="primary",
                use_container_width=True,
                key=f"_inv_btn_{cfg['key']}",
                disabled=not has_files,
            )

        result_key = f"_inv_result_{cfg['key']}"
        if run and has_files:
            if multi:
                payload = [(f.name, f.getvalue()) for f in uploaded]
                spinner_msg = (
                    f"Importando {n_files} arquivos — {cfg['label']}…"
                    if n_files > 1 else f"Importando {cfg['label']}…"
                )
            elif cfg.get("needs_filename"):
                # Parser usa o nome do arquivo (XP: infere report_date)
                payload = (uploaded.name, uploaded.getvalue())
                spinner_msg = f"Importando {cfg['label']}…"
            else:
                payload = uploaded.getvalue()
                spinner_msg = f"Importando {cfg['label']}…"

            with st.spinner(spinner_msg):
                st.session_state[result_key] = _executar_importacao_investimento(
                    cfg, payload
                )

        if st.session_state.get(result_key):
            _render_import_result(st.session_state[result_key])


def _executar_importacao_investimento(cfg: dict, payload) -> dict:
    """
    Roda o parser correspondente e registra o resultado no painel de
    atualização (data_update_logs + data_freshness_status).

    `payload` é bytes (upload único) ou list[(filename, bytes)] (Nomad
    multi-arquivo). O parser correspondente sabe normalizar.
    """
    from datetime import datetime, timezone

    from core.database import get_engine
    from data_pipeline.importers.investments import (
        parse_b3_negociacao, parse_b3_movimentacao,
        parse_xp_consolidado, parse_nomad_pdf,
    )
    from data_pipeline.utils.logging_utils import (
        log_finish, log_start, update_freshness,
    )

    parsers = {
        "parse_b3_negociacao":   parse_b3_negociacao,
        "parse_b3_movimentacao": parse_b3_movimentacao,
        "parse_xp_consolidado":  parse_xp_consolidado,
        "parse_nomad_pdf":       parse_nomad_pdf,
    }
    parser = parsers[cfg["parser_attr"]]

    engine = get_engine()
    if engine is None:
        return {
            "status": "failed",
            "source": cfg["job_name"],
            "records_imported": 0,
            "transactions_imported": 0,
            "incomes_imported": 0,
            "positions_imported": 0,
            "duplicates_skipped": 0,
            "rows_skipped": 0,
            "errors": ["Banco não configurado."],
        }

    started = datetime.now(tz=timezone.utc)
    log_id = log_start(cfg["table_name"], cfg["source_name"], cfg["job_name"])

    try:
        summary = parser(payload, engine)
    except Exception as exc:  # noqa: BLE001
        summary = {
            "status": "failed",
            "source": cfg["job_name"],
            "records_imported": 0,
            "transactions_imported": 0,
            "incomes_imported": 0,
            "positions_imported": 0,
            "duplicates_skipped": 0,
            "rows_skipped": 0,
            "errors": [f"Erro na importação: {type(exc).__name__}"],
        }

    log_finish(
        log_id,
        status=summary.get("status", "failed"),
        records_inserted=int(summary.get("records_imported", 0)),
        records_updated=0,
        records_failed=int(summary.get("rows_skipped", 0)),
        error_message=("; ".join(summary.get("errors", [])[:3]) or None),
        started_at=started,
    )
    update_freshness(
        cfg["table_name"], cfg["source_name"], cfg["job_name"],
        status=summary.get("status", "failed"),
        records_inserted=int(summary.get("records_imported", 0)),
        records_updated=0,
        records_failed=int(summary.get("rows_skipped", 0)),
        error_message=("; ".join(summary.get("errors", [])[:3]) or None),
        frequency="manual",
    )

    # Recalcula portfolio_positions automaticamente se gravamos novas
    # operacoes — assim a Carteira reflete imediatamente o que foi importado.
    if (
        summary.get("status") in ("success", "partial_success")
        and int(summary.get("transactions_imported", 0)) > 0
        and settings.OWNER_USER_ID
    ):
        from data_pipeline.importers.investments.positions import recompute_for_user
        recompute_summary = recompute_for_user(engine, settings.OWNER_USER_ID)
        summary["_positions_recompute"] = recompute_summary

    summary["_executed_at_local"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    return summary


def _render_import_result(summary: dict) -> None:
    """Renderiza o resumo de uma importação concluída."""
    st.markdown("")
    status = summary.get("status", "failed")
    src = summary.get("source", "?")
    if status == "success":
        st.success(f"✅ Importação concluída — {summary.get('records_imported', 0)} registros novos.")
    elif status == "partial_success":
        st.warning(f"🟡 Importação parcial — alguns registros falharam.")
    elif status == "skipped":
        st.info("⚪ Importação não executada nesta rodada.")
    else:
        st.error(f"❌ Falha na importação de {src}.")

    # XP traz posições (snapshots); B3/Nomad trazem operações.
    positions = int(summary.get("positions_imported", 0))
    if positions > 0:
        cols = st.columns(4)
        cols[0].metric("Posições", positions)
        cols[1].metric("Proventos", int(summary.get("incomes_imported", 0)))
        cols[2].metric("Duplicados", int(summary.get("duplicates_skipped", 0)))
        cols[3].metric("Ignorados", int(summary.get("rows_skipped", 0)))
    else:
        cols = st.columns(4)
        cols[0].metric("Operações", int(summary.get("transactions_imported", 0)))
        cols[1].metric("Proventos", int(summary.get("incomes_imported", 0)))
        cols[2].metric("Duplicados", int(summary.get("duplicates_skipped", 0)))
        cols[3].metric("Ignorados", int(summary.get("rows_skipped", 0)))

    extras = []
    if summary.get("_report_date"):
        extras.append(f"Data do snapshot: {summary['_report_date']}")
    if summary.get("_institution"):
        extras.append(f"Instituição: {summary['_institution']}")
    extras.append(f"Executado em {summary.get('_executed_at_local', '—')}")
    st.caption(" · ".join(extras))

    files_skipped = int(summary.get("files_skipped", 0))
    if files_skipped:
        notes = summary.get("files_skipped_notes") or []
        st.info(
            f"ℹ️ {files_skipped} arquivo(s) ignorado(s) intencionalmente — "
            "extratos mensais consolidam o que as notas individuais já trazem."
        )
        if notes:
            with st.expander(f"Arquivos ignorados ({files_skipped})"):
                for note in notes[:50]:
                    st.caption(note)
                if len(notes) > 50:
                    st.caption(f"… e mais {len(notes) - 50} arquivos.")

    rec = summary.get("_positions_recompute")
    if rec:
        if rec.get("ok"):
            st.caption(
                f"📊 Carteira atualizada: {rec.get('positions_upserted', 0)} "
                f"posicoes recalculadas a partir de "
                f"{rec.get('transactions_loaded', 0)} operacoes."
            )
            if rec.get("alerts"):
                with st.expander("Alertas no calculo da carteira"):
                    for msg in rec["alerts"]:
                        st.caption(msg)
        else:
            st.warning(
                f"📊 Recalculo da carteira falhou: {rec.get('error', 'erro desconhecido')}"
            )

    errors = summary.get("errors") or []
    if errors:
        with st.expander(f"Detalhes técnicos ({len(errors)})"):
            for err in errors[:50]:
                st.code(err)
            if len(errors) > 50:
                st.caption(f"… e mais {len(errors) - 50} mensagens.")


# ═══════════════════════════════════════════════════════════════════════════════
# Tab 3 — Segurança
# ═══════════════════════════════════════════════════════════════════════════════

def _render_seguranca() -> None:
    col1, col2 = st.columns(2)
    with col1:
        if settings.APP_PASSWORD:
            st.success("Senha de acesso configurada")
        else:
            st.warning("Sem senha (modo dev)")
    with col2:
        if esta_autenticado():
            st.success("Sessão autenticada")
            if st.button("Encerrar sessão", type="secondary"):
                encerrar_sessao()
        else:
            st.info("Sem sessão ativa")

    st.divider()

    with st.expander("🔑 Gerar hash de senha (APP_PASSWORD)"):
        st.caption(
            "Cole o hash gerado no campo `APP_PASSWORD` do `.env` ou Streamlit Secrets. "
            "Nunca use a senha em texto puro em produção."
        )
        senha = st.text_input("Senha", type="password", key="_hash_input",
                              placeholder="Digite para gerar o hash SHA-256")
        if senha:
            import hashlib
            h = hashlib.sha256(senha.encode()).hexdigest()
            st.code(h)

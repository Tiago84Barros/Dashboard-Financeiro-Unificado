"""
views/admin_data_health.py
Página administrativa: Saúde dos Dados.

Mostra a saúde do banco, % auditado/validado, score médio de confiabilidade,
empresas pendentes, histórico das últimas auditorias, última sincronização,
empresas com mais inconsistências e campos críticos pendentes.

Tudo defensivo: se as tabelas de qualidade ainda não existirem (job nunca rodou),
a página explica o que falta em vez de quebrar. Nunca expõe segredos/URLs.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st


def _scalar(sql: str, params: dict | None = None):
    try:
        from sqlalchemy import text
        from core.database import get_engine
        engine = get_engine()
        if engine is None:
            return None
        with engine.connect() as conn:
            return conn.execute(text(sql), params or {}).scalar()
    except Exception:
        return None


def _df(sql: str, params: dict | None = None) -> pd.DataFrame:
    try:
        from sqlalchemy import text
        from core.database import get_engine
        engine = get_engine()
        if engine is None:
            return pd.DataFrame()
        with engine.connect() as conn:
            return pd.read_sql_query(text(sql), conn, params=params or {})
    except Exception:
        return pd.DataFrame()


def _table_exists(name: str) -> bool:
    return bool(_scalar(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t)", {"t": name}
    ))


def render(show_header: bool = True) -> None:
    if show_header:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">'
            '<span style="font-size:2rem">🩺</span>'
            '<h1 style="font-size:1.9rem;font-weight:800;color:#E2E8F0;margin:0;">'
            'Saúde dos Dados</h1></div>',
            unsafe_allow_html=True,
        )
    st.caption("Monitoramento contínuo da qualidade do banco B3 — auditoria, "
               "saneamento cruzado (Fundamentus/Status Invest) e score de confiabilidade.")

    # ── Métricas-chave ────────────────────────────────────────────────────────
    universo = 0
    try:
        import core.b3_db as _db
        df_set = _db.load_setores()
        universo = int(df_set["ticker"].nunique()) if not df_set.empty else 0
    except Exception:
        pass

    auditadas = 0
    score_medio = None
    if _table_exists("data_quality_scores"):
        auditadas = int(_scalar("SELECT COUNT(DISTINCT ticker) FROM data_quality_scores") or 0)
        try:
            from data_pipeline.quality.score import bank_average_score
            score_medio = bank_average_score()
        except Exception:
            score_medio = None

    pct_aud = (auditadas / universo * 100.0) if universo else 0.0
    pendentes = max(0, universo - auditadas)

    ultima_sync = None
    try:
        from data_pipeline.orchestrator import get_last_global_update
        ultima_sync = get_last_global_update()
    except Exception:
        pass

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Empresas no universo", f"{universo:,}".replace(",", "."))
    c2.metric("Auditadas", f"{auditadas:,}".replace(",", "."), f"{pct_aud:.0f}% do universo")
    c3.metric("Pendentes", f"{pendentes:,}".replace(",", "."))
    c4.metric("Score médio do banco", f"{score_medio:.1f}%" if score_medio is not None else "—",
              help="Média do score de confiabilidade por campo (data_quality_scores).")

    st.progress(min(1.0, pct_aud / 100.0), text=f"Cobertura de auditoria: {pct_aud:.0f}%")
    if ultima_sync:
        st.caption(f"Última sincronização global: {ultima_sync}")

    if not _table_exists("data_quality_scores") and not _table_exists("data_quality_reports"):
        st.info(
            "O ciclo de auditoria & saneamento ainda não rodou (tabelas de qualidade "
            "não encontradas). Ele roda automaticamente no GitHub Actions (job "
            "`audit_and_heal`) ou sob demanda nas abas Análise Avançada / Criação de "
            "Portfólio (botão 🩺 Sanear dados).",
            icon="ℹ️",
        )

    st.divider()

    # ── Frescor das fontes (pipeline) ─────────────────────────────────────────
    st.subheader("📡 Frescor das fontes de dados")
    try:
        from data_pipeline.orchestrator import get_update_status
        status = get_update_status()
        if status:
            cols = ["source_name", "freshness_status", "last_success_at", "next_expected_update"]
            dfx = pd.DataFrame(status)
            st.dataframe(dfx[[c for c in cols if c in dfx.columns]],
                         use_container_width=True, hide_index=True)
        else:
            st.caption("Sem registro de fontes ainda.")
    except Exception:
        st.caption("Status de fontes indisponível.")

    # ── Últimas auditorias ────────────────────────────────────────────────────
    st.subheader("🧾 Histórico das últimas auditorias")
    if _table_exists("data_quality_reports"):
        rep = _df("""
            SELECT run_ts, empresas_verificadas, empresas_corrigidas,
                   campos_atualizados, divergencias, score_medio_banco
            FROM data_quality_reports ORDER BY id DESC LIMIT 20
        """)
        if not rep.empty:
            st.dataframe(rep, use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhum relatório de auditoria registrado ainda.")
    else:
        st.caption("Tabela de relatórios ainda não criada (o job ainda não rodou).")

    # ── Empresas com mais inconsistências (correções aplicadas) ───────────────
    st.subheader("🏷️ Empresas com mais correções")
    if _table_exists("data_healing_audit"):
        worst = _df("""
            SELECT ticker AS "Ticker", COUNT(*) AS "Correções"
            FROM data_healing_audit GROUP BY ticker
            ORDER BY COUNT(*) DESC LIMIT 15
        """)
        if not worst.empty:
            st.dataframe(worst, use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhuma correção registrada ainda.")
    else:
        st.caption("Sem histórico de correções (data_healing_audit) ainda.")

    # ── Campos críticos pendentes (menores scores) ────────────────────────────
    st.subheader("⚠️ Campos com menor confiabilidade")
    if _table_exists("data_quality_scores"):
        low = _df("""
            SELECT ticker AS "Ticker", indicador AS "Indicador",
                   ROUND(score::numeric, 1) AS "Score", n_fontes AS "Fontes"
            FROM data_quality_scores
            ORDER BY score ASC NULLS FIRST LIMIT 20
        """)
        if not low.empty:
            st.dataframe(low, use_container_width=True, hide_index=True)
        else:
            st.caption("Sem scores calculados ainda.")
    else:
        st.caption("Scores de confiabilidade ainda não calculados.")

    st.divider()
    st.caption(
        "Saneamento grava sempre com backup (multiplos_healing_backup) e auditoria "
        "(data_healing_audit). Correções exigem ≥2 fontes concordantes; em divergência, "
        "prioriza Fundamentus/Status Invest."
    )

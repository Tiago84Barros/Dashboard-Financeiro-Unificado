"""
views/admin_data_health.py
Página administrativa: Saúde dos Dados.

Layout em cards CSS (tema escuro do app) com KPIs, anel de score, distribuição
de confiabilidade, cobertura de auditoria e seções de detalhe. Tudo defensivo:
se as tabelas de qualidade ainda não existirem, explica o que falta sem quebrar.
Nunca expõe segredos/URLs.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from data_pipeline.utils.date_utils import fmt_datetime_br  # UTC → Brasília

# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

_CSS = """
<style>
.dh-hero{display:flex;align-items:center;gap:14px;margin:2px 0 2px;}
.dh-hero-icon{font-size:2.1rem;}
.dh-hero h1{font-size:1.85rem;font-weight:800;color:#E2E8F0;margin:0;letter-spacing:-.02em;}
.dh-sub{font-size:.82rem;color:#9CA3AF;margin:2px 0 18px;}

.dh-grid{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px;}
.dh-card{flex:1;min-width:170px;background:linear-gradient(160deg,#141925 0%,#10141d 100%);
         border:1px solid #1E2533;border-radius:14px;padding:16px 18px;
         box-shadow:0 1px 0 rgba(255,255,255,.02) inset;}
.dh-card-label{font-size:.64rem;letter-spacing:.13em;text-transform:uppercase;
               color:#6B7689;font-weight:800;margin-bottom:8px;display:flex;
               align-items:center;gap:6px;}
.dh-card-val{font-size:1.9rem;font-weight:900;color:#E2E8F0;line-height:1;}
.dh-card-sub{font-size:.72rem;color:#5A6678;margin-top:6px;}
.dh-pos{color:#34D399;} .dh-warn{color:#FBBF24;} .dh-neg{color:#F87171;} .dh-mut{color:#9CA3AF;}
.dh-card-accent-pos{border-color:rgba(52,211,153,.32);background:linear-gradient(160deg,rgba(16,185,129,.10),#10141d);}
.dh-card-accent-warn{border-color:rgba(251,191,36,.30);background:linear-gradient(160deg,rgba(245,158,11,.10),#10141d);}
.dh-card-accent-neg{border-color:rgba(248,113,113,.30);background:linear-gradient(160deg,rgba(239,68,68,.10),#10141d);}

/* Anel de score */
.dh-ring-wrap{display:flex;align-items:center;gap:16px;}
.dh-ring{width:104px;height:104px;border-radius:50%;display:flex;align-items:center;
         justify-content:center;flex-shrink:0;}
.dh-ring-inner{width:82px;height:82px;border-radius:50%;background:#10141d;
               display:flex;flex-direction:column;align-items:center;justify-content:center;}
.dh-ring-val{font-size:1.5rem;font-weight:900;color:#E2E8F0;line-height:1;}
.dh-ring-cap{font-size:.55rem;letter-spacing:.12em;text-transform:uppercase;color:#6B7689;font-weight:800;margin-top:3px;}

/* Barra de cobertura / distribuição */
.dh-bar{height:12px;border-radius:8px;background:#1A2030;overflow:hidden;display:flex;}
.dh-bar-seg{height:100%;}
.dh-legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:.72rem;color:#9CA3AF;}
.dh-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle;}

.dh-section{font-size:1.02rem;font-weight:800;color:#E2E8F0;margin:24px 0 10px;
            display:flex;align-items:center;gap:8px;}
.dh-panel{background:#0E1119;border:1px solid #1E2533;border-radius:12px;padding:6px 8px;}
.dh-note{font-size:.74rem;color:#5A6678;margin-top:10px;line-height:1.5;}
</style>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Acesso a dados (defensivo)
# ─────────────────────────────────────────────────────────────────────────────

def _scalar(sql: str, params: dict | None = None):
    try:
        from sqlalchemy import text
        from core.database import get_engine
        eng = get_engine()
        if eng is None:
            return None
        with eng.connect() as conn:
            return conn.execute(text(sql), params or {}).scalar()
    except Exception:
        return None


def _df(sql: str, params: dict | None = None) -> pd.DataFrame:
    try:
        from sqlalchemy import text
        from core.database import get_engine
        eng = get_engine()
        if eng is None:
            return pd.DataFrame()
        with eng.connect() as conn:
            return pd.read_sql_query(text(sql), conn, params=params or {})
    except Exception:
        return pd.DataFrame()


def _table_exists(name: str) -> bool:
    return bool(_scalar(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t)", {"t": name}
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Componentes
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_int(n) -> str:
    try:
        return f"{int(n):,}".replace(",", ".")
    except Exception:
        return "—"


def _score_class(v: float | None) -> str:
    if v is None:
        return "dh-mut"
    return "dh-pos" if v >= 85 else ("dh-warn" if v >= 70 else "dh-neg")


def _score_color(v: float | None) -> str:
    if v is None:
        return "#3A4356"
    return "#34D399" if v >= 85 else ("#FBBF24" if v >= 70 else "#F87171")


def _kpi(label: str, value: str, sub: str = "", accent: str = "", val_cls: str = "") -> str:
    acc = f" dh-card-accent-{accent}" if accent else ""
    vc = f" {val_cls}" if val_cls else ""
    return (
        f'<div class="dh-card{acc}">'
        f'<div class="dh-card-label">{label}</div>'
        f'<div class="dh-card-val{vc}">{value}</div>'
        f'<div class="dh-card-sub">{sub}</div>'
        f'</div>'
    )


def _ring(score: float | None) -> str:
    color = _score_color(score)
    pct = max(0.0, min(100.0, score if score is not None else 0.0))
    val = f"{score:.0f}<span style='font-size:.9rem'>%</span>" if score is not None else "—"
    return (
        f'<div class="dh-ring" style="background:conic-gradient({color} {pct*3.6:.0f}deg,#1A2030 0);">'
        f'<div class="dh-ring-inner"><div class="dh-ring-val" style="color:{color}">{val}</div>'
        f'<div class="dh-ring-cap">score</div></div></div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Render
# ─────────────────────────────────────────────────────────────────────────────

def render(show_header: bool = True) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    if show_header:
        st.markdown(
            '<div class="dh-hero"><span class="dh-hero-icon">🩺</span>'
            '<h1>Saúde dos Dados</h1></div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<div class="dh-sub">Monitoramento contínuo da qualidade do banco B3 — '
        'auditoria incremental, saneamento cruzado (Fundamentus / Status Invest) '
        'e score de confiabilidade por campo.</div>',
        unsafe_allow_html=True,
    )

    # ── Coleta de métricas ────────────────────────────────────────────────────
    universo = 0
    try:
        import core.b3_db as _db
        df_set = _db.load_setores()
        universo = int(df_set["ticker"].nunique()) if not df_set.empty else 0
    except Exception:
        pass

    has_scores = _table_exists("data_quality_scores")
    has_reports = _table_exists("data_quality_reports")
    has_audit = _table_exists("data_healing_audit")

    auditadas = int(_scalar("SELECT COUNT(DISTINCT ticker) FROM data_quality_scores") or 0) if has_scores else 0
    score_medio = None
    bands = {"alto": 0, "medio": 0, "baixo": 0}
    if has_scores:
        sc = _scalar("SELECT AVG(score) FROM data_quality_scores")
        score_medio = round(float(sc), 1) if sc is not None else None
        dist = _df("""
            SELECT CASE WHEN score >= 90 THEN 'alto'
                        WHEN score >= 70 THEN 'medio' ELSE 'baixo' END AS faixa,
                   COUNT(*) AS n
            FROM data_quality_scores WHERE score IS NOT NULL GROUP BY 1
        """)
        for _, r in dist.iterrows():
            bands[str(r["faixa"])] = int(r["n"])

    pct_aud = (auditadas / universo * 100.0) if universo else 0.0
    pendentes = max(0, universo - auditadas)

    correcoes_total = int(_scalar("SELECT COUNT(*) FROM data_healing_audit") or 0) if has_audit else 0
    correcoes_7d = int(_scalar(
        "SELECT COUNT(*) FROM data_healing_audit WHERE run_ts >= NOW() - INTERVAL '7 days'"
    ) or 0) if has_audit else 0

    ultima_auditoria = _scalar("SELECT MAX(run_ts) FROM data_quality_reports") if has_reports else None
    ultima_sync = None
    try:
        from data_pipeline.orchestrator import get_last_global_update
        ultima_sync = get_last_global_update()
    except Exception:
        pass

    # ── Linha 1: anel de score + KPIs principais ──────────────────────────────
    conf_lbl = ("Excelente" if (score_medio or 0) >= 85 else
                "Atenção" if (score_medio or 0) >= 70 else
                "Crítico" if score_medio is not None else "Sem dados")
    left, right = st.columns([1, 2.4])
    with left:
        st.markdown(
            f'<div class="dh-card" style="height:100%"><div class="dh-card-label">🛡️ Confiabilidade geral</div>'
            f'<div class="dh-ring-wrap" style="margin-top:6px">{_ring(score_medio)}'
            f'<div><div class="dh-card-val {_score_class(score_medio)}" style="font-size:1.25rem">{conf_lbl}</div>'
            f'<div class="dh-card-sub">média do score de todos os campos auditados</div></div></div></div>',
            unsafe_allow_html=True,
        )
    with right:
        cards = "".join([
            _kpi("🏢 Universo B3", _fmt_int(universo), "empresas na base de setores"),
            _kpi("✅ Auditadas", _fmt_int(auditadas), f"{pct_aud:.0f}% do universo",
                 accent=("pos" if pct_aud >= 80 else "warn" if pct_aud >= 40 else "neg")),
            _kpi("⏳ Pendentes", _fmt_int(pendentes), "aguardando 1ª auditoria"),
        ])
        st.markdown(f'<div class="dh-grid" style="height:100%">{cards}</div>', unsafe_allow_html=True)

    # ── Linha 2: correções + última atividade ─────────────────────────────────
    cards2 = "".join([
        _kpi("🔧 Correções (total)", _fmt_int(correcoes_total), "valores saneados no banco"),
        _kpi("📈 Correções (7 dias)", _fmt_int(correcoes_7d), "saneamentos recentes"),
        _kpi("🕒 Última auditoria", fmt_datetime_br(ultima_auditoria) if ultima_auditoria else "—",
             "horário de Brasília · ciclo audit_and_heal"),
        _kpi("🔄 Última sincronização", fmt_datetime_br(ultima_sync) if ultima_sync else "—",
             "horário de Brasília · pipeline"),
    ])
    st.markdown(f'<div class="dh-grid">{cards2}</div>', unsafe_allow_html=True)

    # ── Cobertura de auditoria ────────────────────────────────────────────────
    st.markdown('<div class="dh-section">📊 Cobertura de auditoria do universo</div>', unsafe_allow_html=True)
    cov = min(100.0, pct_aud)
    st.markdown(
        f'<div class="dh-bar"><div class="dh-bar-seg" style="width:{cov:.1f}%;background:linear-gradient(90deg,#00C896,#34D399);"></div></div>'
        f'<div class="dh-legend"><span>{_fmt_int(auditadas)} auditadas</span>'
        f'<span>{_fmt_int(pendentes)} pendentes</span>'
        f'<span>~{(pendentes // 50) + 1 if pendentes else 0} dia(s) p/ completar o ciclo (50/dia)</span></div>',
        unsafe_allow_html=True,
    )

    # ── Distribuição de confiabilidade ────────────────────────────────────────
    if has_scores and sum(bands.values()) > 0:
        total_b = sum(bands.values())
        a, m, b = bands["alto"], bands["medio"], bands["baixo"]
        wa, wm, wb = (a / total_b * 100, m / total_b * 100, b / total_b * 100)
        st.markdown('<div class="dh-section">🎯 Distribuição de confiabilidade dos campos</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="dh-bar">'
            f'<div class="dh-bar-seg" style="width:{wa:.1f}%;background:#34D399;"></div>'
            f'<div class="dh-bar-seg" style="width:{wm:.1f}%;background:#FBBF24;"></div>'
            f'<div class="dh-bar-seg" style="width:{wb:.1f}%;background:#F87171;"></div></div>'
            f'<div class="dh-legend">'
            f'<span><span class="dh-dot" style="background:#34D399"></span>Alto (≥90%): {_fmt_int(a)}</span>'
            f'<span><span class="dh-dot" style="background:#FBBF24"></span>Médio (70–89%): {_fmt_int(m)}</span>'
            f'<span><span class="dh-dot" style="background:#F87171"></span>Baixo (&lt;70%): {_fmt_int(b)}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Aviso quando o ciclo nunca rodou ──────────────────────────────────────
    if not has_scores and not has_reports:
        st.info(
            "O ciclo de auditoria & saneamento ainda não rodou (tabelas de qualidade "
            "não encontradas). Ele roda automaticamente no GitHub Actions (job "
            "`audit_and_heal`) ou sob demanda nas abas Análise Avançada / Criação de "
            "Portfólio (botão 🩺 Sanear dados).",
            icon="ℹ️",
        )

    # ── Detalhes ──────────────────────────────────────────────────────────────
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown('<div class="dh-section">🧾 Últimas auditorias</div>', unsafe_allow_html=True)
        if has_reports:
            rep = _df("""
                SELECT run_ts AS "Execução", empresas_verificadas AS "Verif.",
                       empresas_corrigidas AS "Corrig.", campos_atualizados AS "Campos",
                       divergencias AS "Diverg.", ROUND(score_medio_banco::numeric,1) AS "Score"
                FROM data_quality_reports ORDER BY id DESC LIMIT 12
            """)
            if not rep.empty:
                if "Execução" in rep.columns:
                    rep["Execução"] = rep["Execução"].map(fmt_datetime_br)
                st.dataframe(rep, use_container_width=True, hide_index=True)
            else:
                st.caption("Nenhum relatório registrado ainda.")
        else:
            st.caption("Tabela de relatórios ainda não criada (o job ainda não rodou).")

    with col_b:
        st.markdown('<div class="dh-section">🏷️ Empresas com mais correções</div>', unsafe_allow_html=True)
        if has_audit:
            worst = _df("""
                SELECT ticker AS "Ticker", COUNT(*) AS "Correções"
                FROM data_healing_audit GROUP BY ticker
                ORDER BY COUNT(*) DESC LIMIT 12
            """)
            if not worst.empty:
                st.dataframe(worst, use_container_width=True, hide_index=True)
            else:
                st.caption("Nenhuma correção registrada ainda.")
        else:
            st.caption("Sem histórico de correções ainda.")

    # ── Campos de menor confiança ─────────────────────────────────────────────
    st.markdown('<div class="dh-section">⚠️ Campos com menor confiabilidade</div>', unsafe_allow_html=True)
    if has_scores:
        low = _df("""
            SELECT ticker AS "Ticker", indicador AS "Indicador",
                   ROUND(score::numeric,1) AS "Score", n_fontes AS "Fontes",
                   n_divergencias AS "Diverg."
            FROM data_quality_scores
            ORDER BY score ASC NULLS FIRST LIMIT 20
        """)
        if not low.empty:
            st.dataframe(low, use_container_width=True, hide_index=True)
        else:
            st.caption("Sem scores calculados ainda.")
    else:
        st.caption("Scores de confiabilidade ainda não calculados.")

    # ── Frescor das fontes ────────────────────────────────────────────────────
    st.markdown('<div class="dh-section">📡 Frescor das fontes do pipeline</div>', unsafe_allow_html=True)
    try:
        from data_pipeline.orchestrator import get_update_status
        status = get_update_status()
        if status:
            dfx = pd.DataFrame(status)
            ren = {"source_name": "Fonte", "freshness_status": "Status",
                   "last_success_at": "Última OK", "next_expected_update": "Próxima"}
            cols = [c for c in ren if c in dfx.columns]
            tbl = dfx[cols].rename(columns=ren)
            for c in ("Última OK", "Próxima"):
                if c in tbl.columns:
                    tbl[c] = tbl[c].map(lambda v: fmt_datetime_br(v) if pd.notna(v) else "—")
            st.dataframe(tbl, use_container_width=True, hide_index=True)
        else:
            st.caption("Sem registro de fontes ainda.")
    except Exception:
        st.caption("Status de fontes indisponível.")

    st.markdown(
        '<div class="dh-note">🔒 Saneamento grava sempre com backup '
        '(<code>multiplos_healing_backup</code>) e auditoria (<code>data_healing_audit</code>). '
        'Correções exigem ≥2 fontes concordantes; em divergência, prioriza '
        'Fundamentus/Status Invest. Zero nunca é tratado como dado ausente.</div>',
        unsafe_allow_html=True,
    )

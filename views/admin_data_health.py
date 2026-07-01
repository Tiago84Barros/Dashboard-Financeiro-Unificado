"""
views/admin_data_health.py
Página administrativa: Saúde dos Dados.

Reflete as fontes ATIVAS do contexto atual:
  1. Fundamentos — market.* (BRAPI Pro), a fonte única de indicadores/DRE.
  2. Documentos CVM/IPE (RAG) — cobertura e extração de texto completo.
  3. Frescor das fontes do pipeline.

A antiga auditoria por "healing" cruzado (Fundamentus / Status Invest sobre a
tabela legada `multiplos`) foi descontinuada como fonte de leitura e não é mais
exibida aqui. Tudo defensivo: se uma tabela não existir, explica sem quebrar.
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

/* Barra de cobertura / distribuição */
.dh-bar{height:12px;border-radius:8px;background:#1A2030;overflow:hidden;display:flex;}
.dh-bar-seg{height:100%;}
.dh-legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:.72rem;color:#9CA3AF;}
.dh-dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:middle;}

.dh-section{font-size:1.02rem;font-weight:800;color:#E2E8F0;margin:24px 0 10px;
            display:flex;align-items:center;gap:8px;}
.dh-badge{font-size:.62rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;
          color:#34D399;background:rgba(16,185,129,.12);border:1px solid rgba(52,211,153,.3);
          border-radius:999px;padding:2px 10px;}
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


# ─────────────────────────────────────────────────────────────────────────────
# Seção 1 — Fundamentos (market.* / BRAPI Pro), fonte ativa
# ─────────────────────────────────────────────────────────────────────────────

def _render_market(mh: dict, src: str) -> None:
    u = mh["universo"]
    st.markdown(
        '<div class="dh-section">🧬 Fundamentos — market.* (BRAPI Pro) '
        f'<span class="dh-badge">fonte ativa: {src}</span></div>',
        unsafe_allow_html=True,
    )
    cards = "".join([
        _kpi("🏢 Empresas (CVM)", _fmt_int(u["companies"]), "cadastro market.companies"),
        _kpi("📈 Ativos", _fmt_int(u["assets"]), "tickers em market.assets"),
        _kpi("📸 Snapshot ttm", _fmt_int(u["ttm_tickers"]), "empresas com indicadores atuais"),
        _kpi("📚 Histórico anual", _fmt_int(u["annual_tickers"]), "empresas com DRE por ano"),
    ])
    st.markdown(f'<div class="dh-grid">{cards}</div>', unsafe_allow_html=True)

    # Completude das métricas-chave (impacto direto no ranking/score)
    st.markdown('<div class="dh-section" style="font-size:.92rem">📋 Completude das '
                'métricas-chave (snapshot ttm)</div>', unsafe_allow_html=True)
    comp_html = ""
    for r in mh["completude"]:
        pct = r["pct"]
        col = "#34D399" if pct >= 90 else "#FBBF24" if pct >= 60 else "#F87171"
        comp_html += (
            f'<div style="display:flex;align-items:center;gap:10px;margin:4px 0;'
            f'font-size:.74rem;color:#9CA3AF">'
            f'<span style="width:150px">{r["campo"]}</span>'
            f'<div class="dh-bar" style="flex:1"><div class="dh-bar-seg" '
            f'style="width:{pct:.0f}%;background:{col}"></div></div>'
            f'<span style="width:110px;text-align:right">'
            f'{_fmt_int(r["preenchidos"])}/{_fmt_int(r["total"])} ({pct:.0f}%)</span></div>')
    st.markdown(comp_html, unsafe_allow_html=True)

    q = mh["qualidade"]
    sev_txt = " · ".join(f"{s}: {_fmt_int(n)}" for s, n in q.get("por_severidade", [])) or "—"
    fr = mh["frescor"]
    cards_q = "".join([
        _kpi("🚨 Anomalias logadas", _fmt_int(q["total"]),
             f"market.data_quality_logs · {sev_txt}",
             accent=("warn" if q["total"] else "pos")),
        _kpi("🧱 Bootstrap concluído", _fmt_int(fr["bootstrap_ok"]),
             "tickers processados (market.bootstrap_state)"),
        _kpi("🕒 Último cálculo", fmt_datetime_br(fr["ultimo_calc"]) if fr.get("ultimo_calc") else "—",
             "horário de Brasília · reprocess"),
    ])
    st.markdown(f'<div class="dh-grid">{cards_q}</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="dh-note">Métricas com completude &lt;100% são lacunas reais da fonte '
        '(ex.: bancos sem lucro líquido padronizado, empresas sem LPA) — monitoradas, não '
        '"curadas" por scraping. A validação usa faixas coerentes (core.data_quality) e '
        'registra o que destoa em market.data_quality_logs.</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 2 — Documentos CVM/IPE (RAG) — extração de texto
# ─────────────────────────────────────────────────────────────────────────────

# Versões que já têm o TEXTO COMPLETO do documento (não só metadados).
_FULLTEXT_VERSIONS = ("fulltext_v1", "ipe_pdf_text_v1")


def _render_cvm(drip: int) -> None:
    st.markdown('<div class="dh-section">📄 Documentos CVM/IPE (RAG) — extração de texto</div>',
                unsafe_allow_html=True)
    if not _table_exists("docs_corporativos"):
        st.caption("Tabela docs_corporativos indisponível.")
        return

    ext = _df("""
        SELECT
            COUNT(*) FILTER (WHERE extraction_version IN ('fulltext_v1','ipe_pdf_text_v1')) AS com_texto,
            COUNT(*) FILTER (WHERE extraction_version = 'ipe_meta_v1')                      AS pendentes,
            COUNT(*) FILTER (WHERE extraction_version = 'ipe_meta_v1_nofulltext')           AS sem_texto,
            COUNT(*)                                                                        AS total
        FROM public.docs_corporativos
    """)
    if ext.empty:
        st.caption("Sem dados de extração.")
        return

    done = int(ext.iloc[0]["com_texto"] or 0)
    pend = int(ext.iloc[0]["pendentes"] or 0)
    noft = int(ext.iloc[0]["sem_texto"] or 0)
    total = int(ext.iloc[0]["total"] or 0)
    dias = (pend + drip - 1) // drip if pend else 0

    # Cobertura da carteira (por raiz do emissor: PETR4 enxerga docs de PETR3)
    cart = _df("""
        WITH pf AS (SELECT DISTINCT LEFT(UPPER(ticker), 4) AS raiz
                    FROM public.b3_portfolio_model_items)
        SELECT
            (SELECT COUNT(*) FROM pf) AS tot,
            (SELECT COUNT(*) FROM pf WHERE raiz IN (
                SELECT LEFT(UPPER(ticker), 4) FROM public.docs_corporativos
                WHERE extraction_version IN ('fulltext_v1','ipe_pdf_text_v1'))) AS com
    """)
    cart_tot = int(cart.iloc[0]["tot"]) if not cart.empty else 0
    cart_com = int(cart.iloc[0]["com"]) if not cart.empty else 0

    ult_extracao = _scalar(
        "SELECT MAX(created_at) FROM docs_corporativos_chunks "
        "WHERE chunking_version = 'fulltext_v1'"
    )

    cards = "".join([
        _kpi("📚 Docs CVM (total)", _fmt_int(total), "no banco unificado"),
        _kpi("✅ Com texto completo", _fmt_int(done), "PDF/ENET extraído",
             accent="pos" if done else ""),
        _kpi("⏳ Pendentes (gotejamento)", _fmt_int(pend),
             f"~{dias} dia(s) p/ drenar ({drip}/dia)",
             accent="warn" if pend else "pos"),
        _kpi("🎯 Carteira coberta", f"{cart_com}/{cart_tot}" if cart_tot else "—",
             "empresas com ≥1 doc de texto",
             accent="pos" if cart_tot and cart_com >= cart_tot else "warn"),
        _kpi("🕒 Última extração",
             fmt_datetime_br(ult_extracao) if ult_extracao else "—",
             "horário de Brasília · full-text"),
    ])
    st.markdown(f'<div class="dh-grid">{cards}</div>', unsafe_allow_html=True)

    base = done + pend + noft
    if base:
        prog = done / base * 100
        st.markdown(
            f'<div class="dh-bar"><div class="dh-bar-seg" '
            f'style="width:{prog:.1f}%;background:linear-gradient(90deg,#5B8DEF,#34D399);"></div></div>'
            f'<div class="dh-legend"><span>{_fmt_int(done)} com texto</span>'
            f'<span>{_fmt_int(pend)} pendentes</span>'
            f'<span>{_fmt_int(noft)} sem texto útil</span></div>',
            unsafe_allow_html=True,
        )
    st.markdown(
        '<div class="dh-note">O coletor descobre os documentos por metadados e um '
        'gotejamento diário (throttled, anti-bloqueio) baixa o texto completo do ENET, '
        'priorizando a carteira e Fato Relevante/Resultados. Documentos já com texto '
        'alimentam o RAG com números datados; pendentes entram com o resumo de metadados '
        'até serem extraídos.</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 3 — Frescor das fontes do pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _render_frescor() -> None:
    st.markdown('<div class="dh-section">📡 Frescor das fontes do pipeline</div>',
                unsafe_allow_html=True)
    try:
        from data_pipeline.orchestrator import get_update_status
        status = get_update_status()
    except Exception:
        status = None
    if not status:
        st.caption("Status de fontes indisponível.")
        return
    dfx = pd.DataFrame(status)
    ren = {"source_name": "Fonte", "freshness_status": "Status",
           "last_success_at": "Última OK", "next_expected_update": "Próxima"}
    cols = [c for c in ren if c in dfx.columns]
    tbl = dfx[cols].rename(columns=ren)
    for c in ("Última OK", "Próxima"):
        if c in tbl.columns:
            tbl[c] = tbl[c].map(lambda v: fmt_datetime_br(v) if pd.notna(v) else "—")
    st.dataframe(tbl, use_container_width=True, hide_index=True)


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
        '<div class="dh-sub">Monitoramento das fontes ativas: fundamentos '
        '<b>market.*</b> (BRAPI Pro) e o corpus de documentos <b>CVM/IPE</b> que '
        'alimenta a análise por IA (RAG).</div>',
        unsafe_allow_html=True,
    )

    # Origem ativa de leitura (market vs legado) — informativo.
    try:
        import core.b3_data as _facade
        src = _facade.read_source()
    except Exception:
        src = "market"

    # ── 1) Fundamentos — market.* ─────────────────────────────────────────────
    try:
        import core.market_health as _mh
        mh = _mh.market_health_summary()
    except Exception:
        mh = {"schema_ok": False}
    if mh.get("schema_ok"):
        _render_market(mh, src)
    else:
        st.info(
            "Schema `market.*` (BRAPI Pro) indisponível neste banco. Verifique a "
            "conexão / o pipeline de mercado. Os fundamentos vêm dessa fonte.",
            icon="ℹ️",
        )

    # ── 2) Documentos CVM/IPE (RAG) ───────────────────────────────────────────
    try:
        import os as _os
        drip = max(1, int(_os.getenv("CVM_FULLTEXT_MAX", "12")))
    except Exception:
        drip = 12
    _render_cvm(drip)

    # ── 3) Frescor das fontes ─────────────────────────────────────────────────
    _render_frescor()

    st.markdown(
        '<div class="dh-note">🔒 Fundamentos servidos por market.* (BRAPI Pro) com '
        'validação de faixa (core.data_quality) — sem correção por scraping cruzado. '
        'O corpus CVM/IPE é append-only e versionado por extração; nada é sobrescrito '
        'silenciosamente.</div>',
        unsafe_allow_html=True,
    )

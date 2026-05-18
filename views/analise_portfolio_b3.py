"""
views/analise_portfolio_b3.py
Análise Qualitativa de Portfólio B3 via LLM.

Carrega o portfólio modelo salvo em b3_portfolio_models, integra dados
quantitativos (múltiplos + DRE do Supabase) e contexto macro, e gera
análise institucional via OpenAI + redistribuição de pesos quanti-quali.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

import core.b3_db as _db
from core.b3_portfolio_model import load_active_b3_portfolio_model
from core.llm_b3 import (
    analisar_empresa,
    analisar_portfolio,
    llm_disponivel,
    redistribuir_pesos,
)
from views.empresas_b3 import _logo_url, _sec_hdr

# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────

_CSS = """
<style>
.apb3-kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px;}
.apb3-kpi{flex:1;min-width:140px;background:#12151E;border:1px solid #1E2533;
           border-radius:12px;padding:16px 18px;}
.apb3-kpi-label{font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;
                color:#718096;font-weight:700;margin-bottom:6px;}
.apb3-kpi-val{font-size:1.55rem;font-weight:900;color:#E2E8F0;line-height:1.1;}
.apb3-kpi-sub{font-size:.72rem;color:#4A5568;margin-top:4px;}
.apb3-kpi-pos{border-color:rgba(34,197,94,.3);background:rgba(16,185,129,.08);}
.apb3-kpi-neg{border-color:rgba(248,113,113,.3);background:rgba(239,68,68,.07);}
.apb3-kpi-neu{border-color:rgba(148,163,184,.2);}

.apb3-macro-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px;}
.apb3-macro{flex:1;min-width:120px;background:#0E1119;border:1px solid #1E2533;
             border-radius:10px;padding:14px 16px;text-align:center;}
.apb3-macro-lbl{font-size:.65rem;color:#9CA3AF;text-transform:uppercase;
                letter-spacing:.1em;margin-bottom:6px;font-weight:700;}
.apb3-macro-val{font-size:1.30rem;font-weight:900;color:#E2E8F0;}
.apb3-macro-delta{font-size:.72rem;margin-top:4px;}
.apb3-macro-up{color:#34D399;}
.apb3-macro-dn{color:#F87171;}
.apb3-macro-fl{color:#9CA3AF;}

.apb3-logo-grid{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px;}
.apb3-logo-item{display:flex;flex-direction:column;align-items:center;
                background:#12151E;border:1px solid #1E2533;border-radius:10px;
                padding:10px 14px;min-width:88px;text-align:center;}
.apb3-logo-ticker{font-size:.80rem;font-weight:800;color:#E2E8F0;margin-top:6px;}
.apb3-logo-weight{font-size:.68rem;color:#4A5568;margin-top:2px;}
.apb3-logo-badge{font-size:.60rem;font-weight:700;border-radius:999px;
                 padding:2px 8px;margin-top:4px;display:inline-block;}
.apb3-badge-forte{background:rgba(34,197,94,.18);color:#34D399;border:1px solid rgba(34,197,94,.3);}
.apb3-badge-moderada{background:rgba(245,158,11,.15);color:#FBBF24;border:1px solid rgba(245,158,11,.3);}
.apb3-badge-fraca{background:rgba(239,68,68,.13);color:#F87171;border:1px solid rgba(248,113,113,.3);}
.apb3-badge-default{background:rgba(148,163,184,.1);color:#9CA3AF;border:1px solid rgba(148,163,184,.2);}

.apb3-alloc-grid{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px;}
.apb3-alloc-card{flex:1;min-width:150px;max-width:200px;background:#12151E;
                  border:1px solid #1E2533;border-radius:12px;padding:14px 16px;
                  text-align:center;}
.apb3-alloc-ticker{font-size:.95rem;font-weight:800;color:#E2E8F0;margin:6px 0 2px;}
.apb3-alloc-nome{font-size:.62rem;color:#718096;margin-bottom:8px;
                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:160px;}
.apb3-alloc-pct{font-size:1.45rem;font-weight:900;color:#00C896;}
.apb3-alloc-delta{font-size:.70rem;margin-top:3px;}
.apb3-alloc-acao{font-size:.62rem;font-weight:700;border-radius:999px;padding:2px 8px;
                  margin-top:6px;display:inline-block;}
.apb3-acao-manter{background:rgba(148,163,184,.1);color:#9CA3AF;}
.apb3-acao-aumentar{background:rgba(34,197,94,.15);color:#34D399;}
.apb3-acao-reduzir{background:rgba(239,68,68,.13);color:#F87171;}
.apb3-acao-revisar{background:rgba(245,158,11,.13);color:#FBBF24;}

.apb3-section-title{font-size:1.15rem;font-weight:800;color:#E2E8F0;
                     margin:28px 0 6px;display:flex;align-items:center;gap:8px;}
.apb3-divider{border:none;border-top:1px solid #1E2533;margin:20px 0;}

.apb3-report-qual{background:#0A0D15;border:1px solid #1E2533;border-radius:10px;
                   padding:16px 18px;margin-bottom:12px;font-size:.82rem;
                   color:#C4CBD5;line-height:1.6;}
.apb3-report-label{font-size:.65rem;letter-spacing:.1em;text-transform:uppercase;
                    color:#4A5568;font-weight:700;margin-bottom:4px;}
.apb3-tag-pill{border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.04);
               border-radius:999px;padding:4px 12px;color:#D6DCE6;font-size:.72rem;
               font-weight:700;display:inline-block;margin:3px;}
</style>
"""

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _persp_badge(persp: str) -> str:
    cls = {
        "forte":    "apb3-badge-forte",
        "moderada": "apb3-badge-moderada",
        "fraca":    "apb3-badge-fraca",
    }.get(persp, "apb3-badge-default")
    label = {"forte": "FORTE", "moderada": "MODERADA", "fraca": "FRACA"}.get(persp, persp.upper())
    return f'<span class="apb3-logo-badge {cls}">{label}</span>'


def _acao_badge(acao: str) -> str:
    cls = {
        "manter":   "apb3-acao-manter",
        "aumentar": "apb3-acao-aumentar",
        "reduzir":  "apb3-acao-reduzir",
        "revisar":  "apb3-acao-revisar",
    }.get(acao, "apb3-acao-manter")
    return f'<span class="apb3-alloc-acao {cls}">{acao.upper()}</span>'


def _delta_str(new_w: float, old_w: float) -> str:
    d = new_w - old_w
    if abs(d) < 0.001:
        return '<span class="apb3-macro-fl">= sem mudança</span>'
    sign = "▲" if d > 0 else "▼"
    color = "apb3-macro-up" if d > 0 else "apb3-macro-dn"
    return f'<span class="{color}">{sign} {abs(d)*100:.1f}pp</span>'


def _macro_card(label: str, value: str, delta: str, delta_up: bool | None = None) -> str:
    if delta_up is None:
        delta_cls = "apb3-macro-fl"
    else:
        delta_cls = "apb3-macro-up" if delta_up else "apb3-macro-dn"
    return (
        f'<div class="apb3-macro">'
        f'<div class="apb3-macro-lbl">{label}</div>'
        f'<div class="apb3-macro-val">{value}</div>'
        f'<div class="apb3-macro-delta {delta_cls}">{delta}</div>'
        f'</div>'
    )


def _kpi_card(label: str, value: str, sub: str, modifier: str = "neu") -> str:
    return (
        f'<div class="apb3-kpi apb3-kpi-{modifier}">'
        f'<div class="apb3-kpi-label">{label}</div>'
        f'<div class="apb3-kpi-val">{value}</div>'
        f'<div class="apb3-kpi-sub">{sub}</div>'
        f'</div>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 1 — Portfólio Salvo
# ─────────────────────────────────────────────────────────────────────────────

def _render_portfolio_salvo(model: dict, pesos_novos: dict[str, float] | None) -> None:
    items    = model.get("items", [])
    metrics  = model.get("metrics_json") or {}
    if isinstance(metrics, str):
        import json
        try: metrics = json.loads(metrics)
        except Exception: metrics = {}

    n        = len(items)
    alpha_m  = metrics.get("alpha_selic_medio") or (
        float(np.mean([it["alpha_selic"] for it in items])) if items else 0.0
    )
    score_m  = metrics.get("score_medio") or (
        float(np.mean([it["score"] for it in items])) if items else 0.0
    )
    ano      = model.get("ano_compra") or "—"
    name     = model.get("name") or "Portfólio B3 Modelo"

    st.markdown(
        f'<div class="apb3-section-title">📂 {name}</div>',
        unsafe_allow_html=True,
    )

    mod_alpha = "pos" if alpha_m > 0 else ("neg" if alpha_m < 0 else "neu")
    cards_html = "".join([
        _kpi_card("Empresas", str(n), "na carteira"),
        _kpi_card("Ano-base", str(ano), "ciclo de referência"),
        _kpi_card(
            "Alpha vs Selic",
            f"{alpha_m:+.1f}%",
            "média histórica do portfólio",
            mod_alpha,
        ),
        _kpi_card("Score médio", f"{score_m:.1f}", "scoring quantitativo"),
    ])
    st.markdown(f'<div class="apb3-kpi-row">{cards_html}</div>', unsafe_allow_html=True)

    # Grade de logos + pesos
    logos_html = '<div class="apb3-logo-grid">'
    for it in sorted(items, key=lambda x: -float(x.get("weight") or 0)):
        tk      = it["ticker"]
        nome    = (it.get("nome") or tk)[:22]
        w_orig  = float(it.get("weight") or 0)
        w_exib  = (pesos_novos.get(tk, w_orig) if pesos_novos else w_orig)
        logo    = _logo_url(tk)
        logos_html += (
            f'<div class="apb3-logo-item">'
            f'<img src="{logo}" width="38" height="38" '
            f'style="border-radius:8px;object-fit:contain;" '
            f'onerror="this.style.display=\'none\'">'
            f'<div class="apb3-logo-ticker">{tk}</div>'
            f'<div class="apb3-logo-weight">{w_exib*100:.1f}%</div>'
            f'</div>'
        )
    logos_html += "</div>"
    st.markdown(logos_html, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Seção 2 — Cenário Macro Atual
# ─────────────────────────────────────────────────────────────────────────────

def _render_macro(macro_hist: dict) -> None:
    if not macro_hist:
        st.caption("Cenário macro indisponível — configure a tabela `macro` no Supabase.")
        return

    anos = sorted(macro_hist.keys())
    ano_cur = anos[-1]
    ano_ant = anos[-2] if len(anos) >= 2 else None
    cur = macro_hist[ano_cur]
    ant = macro_hist.get(ano_ant, {}) if ano_ant else {}

    def _fmt_rate(val: float) -> str:
        """Formata taxa: se decimal (≤1) multiplica ×100, se já % usa direto."""
        if abs(val) <= 1.0:
            return f"{val * 100:.2f}%"
        return f"{val:.2f}%"

    def _rate_diff(key: str) -> tuple[str, bool | None]:
        """Delta para taxas: pp = diff×100 se decimal, diff direto se já em %."""
        vc = cur.get(key)
        va = ant.get(key)
        try:
            vc, va = float(vc), float(va)
        except Exception:
            return "—", None
        if not (np.isfinite(vc) and np.isfinite(va)):
            return "—", None
        d = vc - va
        mult = 100.0 if abs(vc) <= 1.0 else 1.0
        d_pp = d * mult
        sign = "▲" if d > 0 else ("▼" if d < 0 else "=")
        return f"{sign} {abs(d_pp):.2f}pp vs {ano_ant}", d > 0

    def _cambio_diff() -> tuple[str, bool | None]:
        vc = cur.get("cambio")
        va = ant.get("cambio")
        try:
            vc, va = float(vc), float(va)
        except Exception:
            return "—", None
        if not (np.isfinite(vc) and np.isfinite(va)):
            return "—", None
        d = vc - va
        sign = "▲" if d > 0 else ("▼" if d < 0 else "=")
        return f"{sign} R$ {abs(d):.2f} vs {ano_ant}", d > 0

    def _pib_fmt_and_diff() -> tuple[str, str, bool | None]:
        """PIB pode ser taxa de crescimento ou valor absoluto do PIB."""
        vc = cur.get("pib")
        va = ant.get("pib")
        try:
            vc_f = float(vc)
        except Exception:
            return "—", "—", None
        if not np.isfinite(vc_f):
            return "—", "—", None
        # Se valor absoluto > 100 → provavelmente PIB em unidade monetária, não taxa
        if abs(vc_f) > 100:
            return "N/D", "dados absolutos", None
        display = _fmt_rate(vc_f)
        try:
            va_f = float(va)
            if np.isfinite(va_f) and abs(va_f) <= 100:
                d = vc_f - va_f
                mult = 100.0 if abs(vc_f) <= 1.0 else 1.0
                d_pp = d * mult
                sign = "▲" if d > 0 else ("▼" if d < 0 else "=")
                return display, f"{sign} {abs(d_pp):.2f}pp vs {ano_ant}", d > 0
        except Exception:
            pass
        return display, "—", None

    selic_v  = cur.get("selic")
    ipca_v   = cur.get("ipca")
    selic_val  = _fmt_rate(float(selic_v))  if selic_v  is not None else "—"
    ipca_val   = _fmt_rate(float(ipca_v))   if ipca_v   is not None else "—"
    cambio_val = f"R$ {cur['cambio']:.2f}"  if cur.get("cambio") is not None else "—"
    pib_val, pib_delta, pib_up = _pib_fmt_and_diff()

    selic_delta, selic_up   = _rate_diff("selic")
    ipca_delta,  ipca_up    = _rate_diff("ipca")
    cambio_delta, cambio_up = _cambio_diff()

    st.markdown(
        f'<div class="apb3-section-title">🌐 Cenário Macroeconômico — {ano_cur}</div>',
        unsafe_allow_html=True,
    )

    macro_html = "".join([
        _macro_card("Selic a.a.", selic_val,  selic_delta,  not selic_up  if selic_up  is not None else None),
        _macro_card("IPCA",       ipca_val,   ipca_delta,   not ipca_up   if ipca_up   is not None else None),
        _macro_card("USD / BRL",  cambio_val, cambio_delta, not cambio_up if cambio_up is not None else None),
        _macro_card("PIB",        pib_val,    pib_delta,    pib_up),
    ])
    st.markdown(f'<div class="apb3-macro-row">{macro_html}</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Seção 3 — Relatório Consolidado
# ─────────────────────────────────────────────────────────────────────────────

def _render_relatorio_consolidado(port_analise: dict) -> None:
    if not port_analise:
        return

    qual  = port_analise.get("qualidade_carteira", "—")
    persp = port_analise.get("perspectiva_12m", "—")
    conf  = port_analise.get("confianca_media", 0)
    score = port_analise.get("score_medio", 0)
    cob   = port_analise.get("cobertura", "—")

    qual_mod  = {"alta": "pos", "media": "neu", "baixa": "neg"}.get(qual, "neu")
    persp_mod = {"construtiva": "pos", "equilibrada": "neu", "cautelosa": "neg"}.get(persp, "neu")

    st.markdown('<div class="apb3-section-title">📊 Relatório Consolidado do Portfólio</div>',
                unsafe_allow_html=True)

    cards_html = "".join([
        _kpi_card("Qualidade",      qual.upper(),    "visão LLM da carteira",      qual_mod),
        _kpi_card("Perspectiva 12m", persp.upper(),  "horizonte de médio prazo",    persp_mod),
        _kpi_card("Confiança",      f"{conf}",       "índice 0–100 da análise"),
        _kpi_card("Score LLM",      f"{score}",      "nota qualitativa média",
                  "pos" if score >= 60 else ("neg" if score < 40 else "neu")),
    ])
    st.markdown(f'<div class="apb3-kpi-row">{cards_html}</div>', unsafe_allow_html=True)

    with st.expander("📝 Resumo Executivo + Papel dos Ativos", expanded=True):
        resumo = port_analise.get("resumo_executivo", "")
        papel  = port_analise.get("papel_dos_ativos", "")
        if resumo:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Resumo Executivo</div>{resumo}</div>',
                unsafe_allow_html=True,
            )
        if papel:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Papel dos Ativos na Carteira</div>{papel}</div>',
                unsafe_allow_html=True,
            )

    with st.expander("💪 Pontos Fortes / Fracos", expanded=False):
        fortes = port_analise.get("pontos_fortes", [])
        fracos = port_analise.get("pontos_fracos", [])
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Forças**")
            for f in fortes:
                st.markdown(f"✅ {f}")
        with c2:
            st.markdown("**Pontos de atenção**")
            for f in fracos:
                st.markdown(f"⚠️ {f}")

    with st.expander("🔭 Relatório Estratégico Completo", expanded=False):
        relat = port_analise.get("relatorio_estrategico", "")
        if relat:
            st.markdown(
                f'<div class="apb3-report-qual">{relat}</div>',
                unsafe_allow_html=True,
            )
        sint = port_analise.get("sintese_alocacao", "")
        if sint:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Síntese da Realocação</div>{sint}</div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 4 — Alocação Sugerida
# ─────────────────────────────────────────────────────────────────────────────

def _render_alocacao(items_analisados: list[dict], pesos_novos: dict[str, float]) -> None:
    if not items_analisados or not pesos_novos:
        return

    st.markdown('<div class="apb3-section-title">🎯 Alocação Sugerida (Quanti + Quali)</div>',
                unsafe_allow_html=True)

    cards_html = '<div class="apb3-alloc-grid">'
    for it in sorted(items_analisados, key=lambda x: -pesos_novos.get(x.get("ticker",""), 0)):
        tk    = it.get("ticker", "")
        nome  = (it.get("nome") or tk)[:24]
        an    = it.get("analise", {})
        persp = an.get("perspectiva", "moderada")
        acao  = an.get("acao_sugerida", "manter")
        w_new = pesos_novos.get(tk, 0.0)
        w_old = float(it.get("peso_pct", 0.0)) / 100.0
        logo  = _logo_url(tk)

        cards_html += (
            f'<div class="apb3-alloc-card">'
            f'<img src="{logo}" width="36" height="36" '
            f'style="border-radius:8px;object-fit:contain;" '
            f'onerror="this.style.display=\'none\'">'
            f'<div class="apb3-alloc-ticker">{tk}</div>'
            f'<div class="apb3-alloc-nome" title="{nome}">{nome}</div>'
            f'<div class="apb3-alloc-pct">{w_new*100:.1f}%</div>'
            f'<div class="apb3-alloc-delta">{_delta_str(w_new, w_old)}</div>'
            + _persp_badge(persp) + _acao_badge(acao) +
            f'</div>'
        )
    cards_html += "</div>"
    st.markdown(cards_html, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Seção 5 — Relatórios por Empresa
# ─────────────────────────────────────────────────────────────────────────────

def _render_empresa_expander(it: dict, pesos_novos: dict[str, float]) -> None:
    tk   = it.get("ticker", "")
    an   = it.get("analise", {})
    persp = an.get("perspectiva", "moderada")
    w_new = pesos_novos.get(tk, float(it.get("peso_pct", 0.0)) / 100.0)

    badge = _persp_badge(persp)
    with st.expander(f"{tk}  —  {an.get('acao_sugerida','?').upper()}  •  {w_new*100:.1f}%  {badge}", expanded=False):
        # Resumo
        resumo = an.get("resumo", "")
        if resumo:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Tese de Investimento</div>{resumo}</div>',
                unsafe_allow_html=True,
            )

        c1, c2 = st.columns(2)
        # Riscos
        with c1:
            riscos = an.get("riscos", [])
            if riscos:
                st.markdown("**Riscos**")
                for r in riscos:
                    st.markdown(f"⚠️ {r}")
            macro_s = an.get("sensibilidade_macro", [])
            if macro_s:
                st.markdown("**Sensibilidade macro**")
                pills = "".join(f'<span class="apb3-tag-pill">{m}</span>' for m in macro_s)
                st.markdown(pills, unsafe_allow_html=True)

        # Catalisadores
        with c2:
            cats = an.get("catalisadores", [])
            if cats:
                st.markdown("**Catalisadores**")
                for c in cats:
                    st.markdown(f"🚀 {c}")
            prox = an.get("proxima_acao", "")
            if prox:
                st.markdown(f"**Próxima ação:** {prox}")

        # Alerta + tese final
        alerta = an.get("alerta_principal", "")
        if alerta:
            st.warning(f"⚡ {alerta}")

        tese = an.get("tese_final", "")
        if tese:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Conclusão</div>{tese}</div>',
                unsafe_allow_html=True,
            )

        # Métricas numéricas
        cols_m = st.columns(3)
        cols_m[0].metric("Score qualitativo", f"{an.get('score_qualitativo', '—')}/100")
        cols_m[1].metric("Confiança", f"{an.get('confianca', '—')}/100")
        alloc_sug = an.get("alocacao_sugerida_pct")
        cols_m[2].metric("Alocação sugerida", f"{alloc_sug:.1f}%" if alloc_sug is not None else "—")

        just = an.get("justificativa_alocacao", "")
        if just:
            st.caption(just)


# ─────────────────────────────────────────────────────────────────────────────
# Seção 6 — Conclusão Estratégica
# ─────────────────────────────────────────────────────────────────────────────

def _render_conclusao(port_analise: dict) -> None:
    conclusao = port_analise.get("conclusao_estrategica", "")
    if not conclusao:
        return
    st.markdown('<div class="apb3-section-title">🏁 Conclusão Estratégica</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="apb3-report-qual" style="border-color:rgba(0,200,150,.25);">{conclusao}</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runner de análise LLM
# ─────────────────────────────────────────────────────────────────────────────

def _executar_analise(
    items: list[dict],
    macro_hist: dict,
    mode: str,
    mult_batch: dict,
    dre_batch: dict,
) -> dict:
    """Roda análise individual de cada empresa + análise consolidada + redistribuição."""

    portfolio_ctx = (
        f"Portfólio com {len(items)} empresas. "
        f"Alpha médio vs Selic: {float(np.mean([it.get('alpha_selic',0) for it in items]))*100:.1f}%. "
        f"Score quantitativo médio: {float(np.mean([it.get('score',50) for it in items])):.1f}. "
        f"Modo de redistribuição: {mode}."
    )

    items_analisados: list[dict] = []
    prog = st.progress(0, text="Analisando empresas via LLM…")

    for idx, it in enumerate(items):
        tk       = it["ticker"]
        nome_     = it.get("nome") or tk
        setor_    = it.get("setor") or "N/D"
        seg_      = it.get("segmento") or "N/D"
        peso_pct_ = float(it.get("weight") or 0) * 100.0
        score_    = float(it.get("score") or 50)
        alpha_    = float(it.get("alpha_selic") or 0) * 100.0

        df_mult = mult_batch.get(tk, pd.DataFrame())
        df_fin  = dre_batch.get(tk, pd.DataFrame())

        try:
            analise = analisar_empresa(
                ticker=tk, nome=nome_, setor=setor_, segmento=seg_,
                peso_pct=peso_pct_, score=score_, alpha_selic=alpha_,
                df_mult=df_mult, df_fin=df_fin,
                macro_hist=macro_hist,
                portfolio_ctx=portfolio_ctx,
            )
        except Exception as exc:
            st.warning(f"{tk}: erro LLM — {exc}")
            from core.llm_b3 import _fallback_empresa
            analise = _fallback_empresa(tk, peso_pct_)

        items_analisados.append({
            "ticker":    tk,
            "nome":      nome_,
            "peso_pct":  peso_pct_,
            "score":     score_,
            "alpha_selic": alpha_,
            "analise":   analise,
        })
        prog.progress((idx + 1) / len(items), text=f"Analisado: {tk}")

    prog.empty()

    # Análise consolidada do portfólio
    with st.spinner("Gerando relatório consolidado do portfólio…"):
        try:
            port_analise = analisar_portfolio(items_analisados, macro_hist)
        except Exception as exc:
            st.warning(f"Análise de portfólio falhou: {exc}")
            from core.llm_b3 import _fallback_portfolio
            port_analise = _fallback_portfolio()

    # Redistribuição de pesos
    pesos_novos = redistribuir_pesos(items_analisados, mode=mode)

    return {
        "items_analisados": items_analisados,
        "port_analise":     port_analise,
        "pesos_novos":      pesos_novos,
        "mode":             mode,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def render(show_header: bool = True) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    if show_header:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">'
            '<span style="font-size:2rem">🧠</span>'
            '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
            'Análise de Portfólio B3</h1>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        'Análise qualitativa institucional do portfólio modelo salvo, combinando '
        'dados quantitativos, cenário macro e interpretação LLM baseada em '
        'repertório analítico de casas especializadas (XP, BTG, Itaú BBA).'
        '</p>',
        unsafe_allow_html=True,
    )

    # ── Carrega portfólio salvo ───────────────────────────────────────────────
    with st.spinner("Carregando portfólio modelo…"):
        model = load_active_b3_portfolio_model()

    if not model or not model.get("items"):
        st.info(
            "Nenhum portfólio modelo salvo. "
            "Crie e salve uma carteira na aba **🚀 Criação de Portfólio** primeiro.",
            icon="ℹ️",
        )
        return

    items = model["items"]

    # ── Carrega macro ─────────────────────────────────────────────────────────
    with st.spinner("Carregando dados macroeconômicos…"):
        macro_hist = _db.load_macro_history()

    # ── Portfólio salvo + macro sempre visíveis ───────────────────────────────
    state = st.session_state.get("apb3_state", {})
    pesos_exib = state.get("pesos_novos") if state else None

    _render_portfolio_salvo(model, pesos_exib)
    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    _render_macro(macro_hist)
    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)

    # ── Painel de análise LLM ─────────────────────────────────────────────────
    st.markdown('<div class="apb3-section-title">🤖 Análise Qualitativa via LLM</div>',
                unsafe_allow_html=True)

    if not llm_disponivel():
        st.warning(
            "OpenAI API Key não configurada. "
            "Adicione `OPENAI_API_KEY` no `.env` ou nos Streamlit Secrets para ativar a análise LLM.",
            icon="⚠️",
        )
        return

    col_mode, col_btn, col_reset = st.columns([2, 2, 1])
    with col_mode:
        mode = st.radio(
            "Modo de redistribuição",
            ["Rígida", "Flexível"],
            horizontal=True,
            key="apb3_mode",
            help=(
                "**Rígida**: exclui empresas com perspectiva 'fraca'; peso mínimo 3%.\n"
                "**Flexível**: mantém todas com peso mínimo 2%."
            ),
        )
    with col_btn:
        rodar = st.button(
            "🚀 Executar Análise LLM",
            type="primary",
            use_container_width=True,
            key="apb3_rodar",
        )
    with col_reset:
        if st.button("🗑️ Limpar", use_container_width=True, key="apb3_reset"):
            st.session_state.pop("apb3_state", None)
            st.rerun()

    # ── Executa análise ───────────────────────────────────────────────────────
    if rodar:
        tickers = tuple(sorted(it["ticker"] for it in items))
        with st.spinner("Carregando múltiplos e DRE do banco…"):
            mult_batch = _db.load_multiplos_historico_batch(tickers)
            dre_batch  = _db.load_demonstracoes_batch(tickers)

        result = _executar_analise(items, macro_hist, mode, mult_batch, dre_batch)
        st.session_state["apb3_state"] = result
        st.rerun()

    # ── Exibe resultados ──────────────────────────────────────────────────────
    if not state:
        st.info("Configure o modo acima e clique **🚀 Executar Análise LLM** para iniciar.", icon="ℹ️")
        return

    items_an    = state["items_analisados"]
    port_an     = state["port_analise"]
    pesos_novos = state["pesos_novos"]

    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    _render_relatorio_consolidado(port_an)

    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    _render_alocacao(items_an, pesos_novos)

    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    st.markdown('<div class="apb3-section-title">🏢 Relatórios por Empresa</div>',
                unsafe_allow_html=True)
    for it in sorted(items_an, key=lambda x: -pesos_novos.get(x.get("ticker",""), 0)):
        _render_empresa_expander(it, pesos_novos)

    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    _render_conclusao(port_an)

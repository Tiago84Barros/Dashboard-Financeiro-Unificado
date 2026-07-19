"""
views/analise_portfolio_b3.py
Análise Qualitativa de Portfólio B3 via LLM.

Carrega o portfólio modelo salvo em b3_portfolio_models, integra dados
quantitativos (múltiplos + DRE do Supabase) e contexto macro, e gera
análise institucional via OpenAI + redistribuição de pesos quanti-quali.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import streamlit as st

import core.b3_data as _db  # facade c/ feature flag MARKET_READ_SOURCE (default: legacy)
import core.data_reconciliacao as _recon
from core.b3_portfolio_model import load_active_b3_portfolio_model
from core.llm_b3 import (
    chat_com_portfolio,
    llm_disponivel,
    parse_chart_directives,
    provedores_disponiveis,
    redistribuir_pesos,
)
from core.llm_context_b3 import build_llm_context_for_portfolio_chat
from core.portfolio_report_b3 import (
    analyze_portfolio_report,
    generate_company_portfolio_report,
)
from core.portfolio_chat_charts import infer_chart_directives, render_charts_from_directives
from core.rag_b3 import (
    format_rag_context,
    get_cobertura_docs,
    retrieve_chunks,
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


def _score_mod(v, hi: float, lo: float) -> str:
    """Modificador de cor do card por faixa: >=hi pos, <lo neg, senão neu."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "neu"
    return "pos" if v >= hi else ("neg" if v < lo else "neu")


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
        """
        PIB — BCB série 4380 armazena em R$ milhões correntes (acumulado trimestral).
        Multiplicamos por 1e6 para obter o valor real antes de formatar.
        Se o valor corrente for < 60% do anterior, assume ano incompleto e omite delta.
        """
        vc = cur.get("pib")
        va = ant.get("pib")
        try:
            vc_f = float(vc)
        except Exception:
            return "—", "—", None
        if not np.isfinite(vc_f):
            return "—", "—", None

        if abs(vc_f) <= 100:
            # Taxa de crescimento decimal/percentual (fallback para dados legados)
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

        # Converte de R$ milhões → R$ (valor real)
        actual = vc_f * 1_000_000
        if actual >= 1e12:
            display = f"R$ {actual / 1e12:.1f}T"
        elif actual >= 1e9:
            display = f"R$ {actual / 1e9:.0f}B"
        else:
            display = f"R$ {actual:,.0f}"

        try:
            va_f = float(va)
            if np.isfinite(va_f) and va_f > 0:
                # Ano incompleto: valor corrente < 60% do ano anterior → omite delta
                if vc_f < va_f * 0.60:
                    return display, "ano em curso", None
                pct = (vc_f - va_f) / va_f * 100.0
                sign = "▲" if pct > 0 else ("▼" if pct < 0 else "=")
                return display, f"{sign} {abs(pct):.1f}% vs {ano_ant}", pct > 0
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
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Leitura da Alocação do Modelo</div>{sint}</div>',
                unsafe_allow_html=True,
            )
        causal = port_analise.get("diagnostico_causal", "")
        if causal:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Diagnóstico causal</div>{causal}</div>',
                unsafe_allow_html=True,
            )

        risks = port_analise.get("riscos_transmissao") or []
        catalysts = port_analise.get("catalisadores_portfolio") or []
        if risks or catalysts:
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Riscos e transmissão**")
                for risk in risks:
                    if isinstance(risk, dict):
                        exposed = ", ".join(risk.get("ativos_expostos") or [])
                        st.markdown(
                            f"⚠️ **{risk.get('risco', 'Risco')}** — {risk.get('mecanismo', '')}"
                            + (f" ({exposed})" if exposed else "")
                        )
                        if risk.get("monitoramento"):
                            st.caption(f"Monitorar: {risk['monitoramento']}")
            with c2:
                st.markdown("**Catalisadores do conjunto**")
                for catalyst in catalysts:
                    if isinstance(catalyst, dict):
                        exposed = ", ".join(catalyst.get("ativos_expostos") or [])
                        st.markdown(
                            f"🚀 **{catalyst.get('catalisador', 'Catalisador')}** — "
                            f"{catalyst.get('mecanismo', '')}"
                            + (f" ({exposed})" if exposed else "")
                        )

        fit = port_analise.get("adequacao_carteira") or {}
        if fit:
            fit_text = " · ".join(
                str(fit.get(k) or "") for k in ("perfil", "horizonte", "volatilidade", "condicoes")
                if fit.get(k)
            )
            if fit_text:
                st.markdown(
                    '<div class="apb3-report-qual"><div class="apb3-report-label">'
                    f'Adequação da carteira</div>{fit_text}</div>',
                    unsafe_allow_html=True,
                )


# ─────────────────────────────────────────────────────────────────────────────
# Seção 4 — Alocação Sugerida
# ─────────────────────────────────────────────────────────────────────────────

def _render_alocacao(items_analisados: list[dict], pesos_novos: dict[str, float]) -> None:
    if not items_analisados or not pesos_novos:
        return

    st.markdown('<div class="apb3-section-title">🎯 Alocação do Modelo (Quanti + Quali)</div>',
                unsafe_allow_html=True)

    cards_html = '<div class="apb3-alloc-grid">'
    for it in sorted(items_analisados, key=lambda x: -pesos_novos.get(x.get("ticker",""), 0)):
        tk    = it.get("ticker", "")
        nome  = (it.get("nome") or tk)[:24]
        an    = it.get("analise", {})
        persp = an.get("perspectiva", "moderada")
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
            + _persp_badge(persp) +
            f'</div>'
        )
    cards_html += "</div>"
    st.markdown(cards_html, unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Seção 5 — Relatórios por Empresa
# ─────────────────────────────────────────────────────────────────────────────

def _render_empresa_expander(it: dict, pesos_novos: dict[str, float]) -> None:
    tk    = it.get("ticker", "")
    an    = it.get("analise", {})
    persp = an.get("perspectiva", "moderada")
    w_new = pesos_novos.get(tk, float(it.get("peso_pct", 0.0)) / 100.0)
    conclusion = an.get("conclusao") or {}
    value_band = str(conclusion.get("faixa_valor") or "indeterminada").upper()

    persp_icon = {"forte": "🟢", "moderada": "🟡", "fraca": "🔴"}.get(persp, "⚪")
    persp_txt  = persp.upper()
    with st.expander(f"{persp_icon} {tk}  —  {value_band}  •  {w_new*100:.1f}%  [{persp_txt}]", expanded=False):
        # Classificação do parecer para seleção (mesmo critério do gate da
        # Criação de Portfólio) — veto/ressalva aparecem antes de tudo.
        quali_cls = an.get("classificacao_selecao")
        if quali_cls == "vetar":
            st.error(f"🚫 Parecer recomenda VETO: {an.get('motivo_selecao', '')}")
        elif quali_cls == "aprovar_com_ressalvas" and an.get("motivo_selecao"):
            st.caption(f"⚠️ Ressalva do parecer: {an['motivo_selecao']}")

        # Síntese do parecer
        resumo = an.get("resumo", "")
        if resumo:
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Síntese do Parecer</div>{resumo}</div>',
                unsafe_allow_html=True,
            )

        # KPIs determinísticos do dossiê (calculados em código, não pela LLM)
        d   = it.get("dossie") or {}
        val = d.get("valuation") or {}
        dv  = d.get("dividendos") or {}
        sj  = d.get("sensibilidade_juros") or {}
        if val or dv:
            _preco = val.get("preco")
            _ret   = val.get("ret_12m_pct")
            _dy    = dv.get("dy_12m_pct")
            _plc, _pvp = val.get("pl_calc"), val.get("pvp_calc")
            _nd    = sj.get("div_liq_mi")
            _pos   = {"caixa_liquido": "caixa líquido", "endividada": "endividada",
                      "moderada": "alavancagem moderada"}.get(sj.get("posicao"), "—")
            _preco_sub = str(val.get("data_preco") or "")
            if _ret is not None:
                _preco_sub += f" · 12m {_ret:+.1f}%"
            cards_d = "".join([
                _kpi_card("Preço",
                          f"R$ {_preco:.2f}" if _preco is not None else "—",
                          _preco_sub, "neu"),
                _kpi_card("DY 12m recomputado",
                          f"{_dy:.1f}%" if _dy is not None else "—",
                          "proventos dedup ÷ preço", "pos" if (_dy or 0) >= 6 else "neu"),
                _kpi_card("P/L · P/VP (calc)",
                          f"{_plc if _plc is not None else '—'} · {_pvp if _pvp is not None else '—'}",
                          f"ano-base {val.get('ano_base_valuation') or '—'}", "neu"),
                _kpi_card("Dívida líquida",
                          f"R$ {_nd:,.0f} mi" if _nd is not None else "—",
                          _pos, "pos" if (_nd is not None and _nd < 0) else "neu"),
            ])
            st.markdown(f'<div class="apb3-kpi-row">{cards_d}</div>',
                        unsafe_allow_html=True)

        # Relatório completo por seções. As chaves antigas permanecem na lista
        # para que análises já salvas na sessão continuem legíveis.
        rel = an.get("relatorio") or {}
        for k_sec, lbl in (
            ("empresa_hoje", "O que a empresa é hoje"),
            ("analise_pares", "Análise por pares do mesmo setor"),
            ("valuation_interpretado", "Valuation interpretado"),
            ("tendencias", "Tendências operacionais e financeiras"),
            ("qualidade_resultados", "Qualidade dos Resultados"),
            ("resultados", "Resultados"),
            ("dividendos", "Dividendos — recorrente vs extraordinário"),
            ("valuation", "Valuation"),
            ("governanca_controlador", "Governança e controlador"),
            ("eventos_relevantes", "Eventos relevantes e percepção de mercado"),
            ("qualidade_dados", "Qualidade dos dados"),
        ):
            txt = rel.get(k_sec)
            if txt:
                st.markdown(
                    f'<div class="apb3-report-qual"><div class="apb3-report-label">{lbl}</div>{txt}</div>',
                    unsafe_allow_html=True,
                )

        # Red flags determinísticas do dossiê (verificadas em código)
        flags = d.get("red_flags") or []
        if flags:
            st.markdown("**Red flags determinísticas (verificadas em código)**")
            for f_ in flags:
                st.markdown(f"🚩 {f_}")

        c1, c2 = st.columns(2)
        # Riscos
        with c1:
            riscos = an.get("riscos", [])
            if riscos:
                st.markdown("**Riscos**")
                for r in riscos:
                    if isinstance(r, dict):
                        title = r.get("risco") or "Risco"
                        mechanism = r.get("mecanismo") or ""
                        monitor = r.get("indicador_monitorado") or ""
                        st.markdown(f"⚠️ **{title}** — {mechanism}")
                        if monitor:
                            st.caption(f"Monitorar: {monitor}")
                    else:
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
                    if isinstance(c, dict):
                        title = c.get("catalisador") or "Catalisador"
                        mechanism = c.get("mecanismo") or ""
                        trigger = c.get("janela_ou_gatilho") or ""
                        st.markdown(f"🚀 **{title}** — {mechanism}")
                        if trigger:
                            st.caption(f"Janela/gatilho: {trigger}")
                    else:
                        st.markdown(f"🚀 {c}")
            prox = an.get("proxima_acao", "")
            if prox:
                st.markdown(f"**Monitoramento:** {prox}")

        scenarios = an.get("cenarios") or []
        if scenarios:
            st.markdown("**Análise probabilística**")
            scenario_rows = []
            for scenario in scenarios:
                if not isinstance(scenario, dict):
                    continue
                scenario_rows.append({
                    "Cenário": scenario.get("cenario", "—"),
                    "Probabilidade": f"{float(scenario.get('probabilidade_pct') or 0):.1f}%",
                    "Impacto esperado": scenario.get("impacto_esperado", ""),
                    "Fundamentação": scenario.get("fundamentacao", ""),
                })
            if scenario_rows:
                st.dataframe(pd.DataFrame(scenario_rows), use_container_width=True, hide_index=True)

        score_detail = an.get("score_qualitativo_detalhado") or {}
        if score_detail:
            st.markdown("**Score Qualitativo — composição ponderada**")
            score_rows = []
            for item_score in score_detail.values():
                if not isinstance(item_score, dict):
                    continue
                score_rows.append({
                    "Critério": item_score.get("label", "—"),
                    "Nota (0–10)": item_score.get("nota", "—"),
                    "Peso": f"{item_score.get('peso_pct', 0)}%",
                    "Justificativa": item_score.get("justificativa", ""),
                    "Evidência ou lacuna": item_score.get("evidencia_ou_lacuna", ""),
                })
            if score_rows:
                st.dataframe(pd.DataFrame(score_rows), use_container_width=True, hide_index=True)

        fit = an.get("adequacao_investidor") or {}
        if fit:
            fit_text = " · ".join(
                str(fit.get(k) or "") for k in
                ("perfil", "horizonte", "tolerancia_volatilidade", "condicoes")
                if fit.get(k)
            )
            if fit_text:
                st.markdown(
                    '<div class="apb3-report-qual"><div class="apb3-report-label">'
                    f'Adequação ao perfil do investidor</div>{fit_text}</div>',
                    unsafe_allow_html=True,
                )

        # Alerta + tese final
        alerta = an.get("alerta_principal", "")
        if alerta:
            st.warning(f"⚡ {alerta}")

        conclusion = an.get("conclusao") or {}
        conclusion_lines = []
        for label, key in (
            ("Faixa de valor", "faixa_valor"),
            ("O desconto é justificável?", "desconto_justificavel"),
            ("Percepção implícita", "percepcao_mercado"),
            ("Risco-retorno", "risco_retorno"),
            ("Principal positivo", "principal_positivo"),
            ("Principal risco", "principal_risco"),
        ):
            if conclusion.get(key):
                conclusion_lines.append(f"**{label}:** {conclusion[key]}")
        tese = conclusion.get("resumo_executivo") or an.get("tese_final", "")
        if conclusion_lines or tese:
            body = "<br>".join(conclusion_lines + ([f"<br>{tese}"] if tese else []))
            st.markdown(
                f'<div class="apb3-report-qual"><div class="apb3-report-label">Conclusão objetiva</div>{body}</div>',
                unsafe_allow_html=True,
            )

        # Métricas numéricas — cards estilizados (mesmo visual dos KPIs do relatório)
        sc = an.get("score_qualitativo")
        cf = an.get("confianca")
        weighted = an.get("score_qualitativo_ponderado")
        n_docs = it.get("n_docs", 0)

        sc_val = f"{sc}/100" if sc is not None else "—"
        cf_val = f"{cf}/100" if cf is not None else "—"
        value_band = str(conclusion.get("faixa_valor") or "indeterminada")

        cards_m = "".join([
            _kpi_card("Score qualitativo", sc_val,
                      f"ponderado {weighted}/10" if weighted is not None else "nota ponderada",
                      _score_mod(sc, 60, 40)),
            _kpi_card("Confiança", cf_val, "convicção da análise", _score_mod(cf, 70, 50)),
            _kpi_card("Valuation", value_band.upper(),
                      str(conclusion.get("percepcao_mercado") or "leitura não disponível")),
            _kpi_card("Docs CVM", f"{n_docs:,}" if n_docs else "—",
                      "chunks IPE/ENET no RAG" if n_docs else "sem documentos",
                      "pos" if n_docs else "neu"),
        ])
        st.markdown(f'<div class="apb3-kpi-row">{cards_m}</div>', unsafe_allow_html=True)

        # RAG stats
        rag_stats = it.get("rag_stats", {})
        if rag_stats and rag_stats.get("total_hits", 0) > 0:
            mode_lbl = {"semantic": "semântico", "temporal_fallback": "temporal"}.get(
                rag_stats.get("mode", ""), rag_stats.get("mode", ""))
            st.caption(
                f"📄 RAG {mode_lbl}: {rag_stats['total_hits']} chunks recuperados "
                f"(últimos {rag_stats.get('months_back', 36)} meses)"
            )


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
    cobertura_docs: dict[str, int] | None = None,
) -> dict:
    """Roda análise individual de cada empresa + análise consolidada + redistribuição."""

    # Fix auditoria 2026-07: alpha_selic já é gravado em PONTOS PERCENTUAIS
    # pela Criação de Portfólio (_margem_pct multiplica por 100); o ×100
    # extra aqui mandava "1.250%" para a LLM e distorcia a análise.
    portfolio_ctx = (
        f"Portfólio com {len(items)} empresas. "
        f"Alpha médio vs Selic: {float(np.mean([it.get('alpha_selic',0) for it in items])):.1f}%. "
        f"Score quantitativo médio: {float(np.mean([it.get('score',50) for it in items])):.1f}. "
        f"Modo de redistribuição: {mode}."
    )
    n_com_docs = sum(1 for it in items if (cobertura_docs or {}).get(it["ticker"], 0) > 0)

    items_analisados: list[dict] = []
    erros: list[str] = []          # erros de LLM capturados — exibidos após o rerun
    prog = st.progress(0, text="Analisando empresas via LLM…")

    for idx, it in enumerate(items):
        tk        = it["ticker"]
        nome_     = it.get("nome") or tk
        setor_    = it.get("setor") or "N/D"
        seg_      = it.get("segmento") or "N/D"
        peso_pct_ = float(it.get("weight") or 0) * 100.0
        score_    = float(it.get("score") or 50)
        # alpha_selic já vem em pontos percentuais (fix auditoria 2026-07)
        alpha_    = float(it.get("alpha_selic") or 0)
        n_docs    = (cobertura_docs or {}).get(tk, 0)

        # Fundamentos por empresa agora vêm do dossiê determinístico
        # O dossiê, os múltiplos e as demonstrações alimentam a leitura histórica,
        # a qualidade dos resultados e a síntese consolidada do portfólio.

        # ── RAG: recupera chunks CVM para este ticker ─────────────────────────
        rag_ctx = ""
        rag_stats: dict = {}
        if n_docs > 0:
            prog.progress((idx + 1) / len(items),
                          text=f"Buscando docs CVM para {tk}…")
            try:
                # Janela maior (48m) e mais candidatos: a priorização por sinal em
                # format_rag_context garante Fato Relevante/Resultados no contexto.
                chunks, rag_stats = retrieve_chunks(tk, top_k_total=90, months_back=48)
                rag_ctx = format_rag_context(chunks, max_chars=12000)
            except Exception as exc_rag:
                rag_ctx = ""
                rag_stats = {"mode": "error", "error": str(exc_rag)}

        prog.progress((idx + 1) / len(items), text=f"LLM: {tk}…")
        # Relatório institucional EXCLUSIVO desta aba. Reusa o dossiê, RAG,
        # loaders e provedores existentes sem tocar nos prompts do gate, Score,
        # análise individual B3 ou Empresas Americanas.
        dossie: dict = {}
        try:
            _ctx_emp = (
                f"{portfolio_ctx} Empresa avaliada: {nome_} | setor {setor_} / "
                f"segmento {seg_}. Peso atual na carteira: {peso_pct_:.1f}% | "
                f"score quantitativo {score_:.1f} | alpha vs Selic {alpha_:+.1f}%."
            )
            analise, dossie = generate_company_portfolio_report(
                tk,
                df_fin=(dre_batch or {}).get(tk),
                df_mult=(mult_batch or {}).get(tk),
                macro_hist=macro_hist,
                portfolio_tickers=[item.get("ticker", "") for item in items],
                rag_context=rag_ctx,
                portfolio_context=_ctx_emp,
            )
        except Exception as exc:
            st.warning(f"{tk}: erro LLM — {exc}")
            erros.append(f"{tk}: {exc}")
            from core.llm_b3 import _fallback_empresa
            analise = _fallback_empresa(tk, peso_pct_)
        else:
            if int(analise.get("confianca") or 0) == 0:
                erros.append(f"{tk}: relatório institucional não gerado — exibindo fallback neutro.")

        items_analisados.append({
            "ticker":     tk,
            "nome":       nome_,
            "peso_pct":   peso_pct_,
            "score":      score_,
            "alpha_selic": alpha_,
            "analise":    analise,
            "dossie":     dossie,
            "n_docs":     n_docs,
            "rag_stats":  rag_stats,
        })
        prog.progress((idx + 1) / len(items), text=f"Analisado: {tk}")

    prog.empty()

    # Análise consolidada do portfólio
    with st.spinner("Gerando relatório consolidado do portfólio…"):
        try:
            port_analise = analyze_portfolio_report(items_analisados, macro_hist)
            if int(port_analise.get("confianca_media") or 0) == 0:
                # _parse_json caiu no fallback (resposta da LLM não era JSON válido).
                erros.append("Relatório consolidado: resposta da LLM não pôde ser "
                             "interpretada (JSON inválido).")
        except Exception as exc:
            st.warning(f"Análise de portfólio falhou: {exc}")
            erros.append(f"Relatório consolidado: {exc}")
            from core.llm_b3 import _fallback_portfolio
            port_analise = _fallback_portfolio()

    # Redistribuição de pesos
    pesos_novos = redistribuir_pesos(items_analisados, mode=mode)

    return {
        "items_analisados": items_analisados,
        "port_analise":     port_analise,
        "pesos_novos":      pesos_novos,
        "mode":             mode,
        "erros":            erros,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Chat contextual sobre o portfólio
# ─────────────────────────────────────────────────────────────────────────────

# Classificação de formatação dos indicadores
_IND_PCT   = ("DY", "ROE", "ROA", "ROIC", "Margem_Liquida", "Margem_Operacional",
              "Payout", "cresc_receita", "cresc_lucro")
_IND_RATIO = ("P/L", "P/VP", "EV_EBIT", "Liquidez_Corrente", "Endividamento_Total", "P_FCO")
_IND_LABEL = {
    "DY": "Dividend Yield", "P/L": "P/L", "P/VP": "P/VP", "ROE": "ROE", "ROA": "ROA",
    "ROIC": "ROIC", "Margem_Liquida": "Margem líquida", "Margem_Operacional": "Margem op.",
    "Endividamento_Total": "Dív/Patrim.", "Liquidez_Corrente": "Liquidez corrente",
    "EV_EBIT": "EV/EBIT", "Payout": "Payout", "P_FCO": "P/FCO",
    "cresc_receita": "Cresc. receita (a.a.)", "cresc_lucro": "Cresc. lucro (a.a.)",
}


def _fmt_ind(key: str, v) -> str:
    """Formata um indicador conforme seu tipo (% ou múltiplo)."""
    if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v):
        return "N/D"
    v = float(v)
    if key in _IND_PCT:
        return f"{v*100:.1f}%" if abs(v) <= 2.0 else f"{v:.1f}%"
    return f"{v:.2f}x"


def _dre_series(dfd: pd.DataFrame, col: str) -> pd.Series:
    if dfd is None or dfd.empty or col not in dfd.columns:
        return pd.Series(dtype=float)
    d = dfd.copy()
    if "Data" in d.columns:
        d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
        d = d.dropna(subset=["Data"]).sort_values("Data")
    return pd.to_numeric(d[col], errors="coerce")


def _growth_log(series: pd.Series) -> float | None:
    vals = pd.to_numeric(series, errors="coerce").dropna()
    vals = vals[vals > 0].tail(6)
    if len(vals) < 3:
        return None
    x = np.arange(len(vals), dtype=float)
    y = np.log(vals.astype(float).to_numpy())
    slope = float(np.polyfit(x, y, 1)[0])
    return float(np.exp(slope) - 1.0)


@st.cache_data(ttl=1800, show_spinner=False)
def _portfolio_fundamentals(tickers_tuple: tuple[str, ...]) -> dict[str, dict]:
    """Fundamentos por ticker: múltiplos atuais (banco) + crescimento (DRE)."""
    fund: dict[str, dict] = {}
    mult_cols = ("DY", "P/L", "P/VP", "ROE", "ROA", "ROIC", "Margem_Liquida",
                 "Margem_Operacional", "Endividamento_Total", "Liquidez_Corrente",
                 "EV_EBIT", "Payout", "P_FCO")
    try:
        df_all = _db.load_multiplos_todos()
        df_recon, _, _ = _recon.batch_multiplos_reconciliados(
            tickers_tuple,
            df_base=df_all,
            include_status=False,
        )
        if not df_recon.empty and "Ticker" in df_recon.columns:
            df_recon = df_recon.copy()
            df_recon["_TK"] = df_recon["Ticker"].astype(str).str.upper().str.replace(".SA", "", regex=False)
            idx = df_recon.set_index("_TK")
            for tk in tickers_tuple:
                if tk in idx.index:
                    row = idx.loc[tk]
                    if isinstance(row, pd.DataFrame):
                        row = row.iloc[0]
                    fund[tk] = {
                        c: (float(row[c]) if c in idx.columns and pd.notna(row.get(c)) else None)
                        for c in mult_cols
                    }
    except Exception:
        pass
    # Crescimento de receita/lucro a partir da DRE
    try:
        dre = _db.load_demonstracoes_batch(tickers_tuple)
        for tk, dfd in (dre or {}).items():
            tku = str(tk).upper().replace(".SA", "")
            fund.setdefault(tku, {})
            fund[tku]["cresc_receita"] = _growth_log(_dre_series(dfd, "Receita_Liquida"))
            fund[tku]["cresc_lucro"]   = _growth_log(_dre_series(dfd, "Lucro_Liquido"))
    except Exception:
        pass
    return fund


def _weights_from_model(model: dict) -> dict[str, float]:
    raw = {
        str(it.get("ticker", "")).upper().replace(".SA", ""): float(it.get("weight") or 0)
        for it in model.get("items", [])
    }
    tot = sum(raw.values()) or 1.0
    return {k: v / tot for k, v in raw.items()}


def _consolidated_metrics(fund: dict[str, dict], weights: dict[str, float]) -> dict[str, dict]:
    """Médias ponderada (por peso) e simples de cada indicador da carteira."""
    keys = ("DY", "P/L", "P/VP", "ROE", "ROIC", "Margem_Liquida",
            "Endividamento_Total", "Payout", "Liquidez_Corrente",
            "cresc_receita", "cresc_lucro")
    consol: dict[str, dict] = {}
    for k in keys:
        acc = wsum = 0.0
        vals: list[float] = []
        for tk, f in fund.items():
            v = f.get(k)
            if v is None or (isinstance(v, float) and not np.isfinite(v)):
                continue
            vals.append(v)
            w = weights.get(tk, 0.0)
            if w > 0:
                acc += v * w
                wsum += w
        if vals:
            consol[k] = {
                "ponderada": (acc / wsum) if wsum > 0 else None,
                "simples": sum(vals) / len(vals),
                "n": len(vals),
            }
    return consol


def _parse_motivos(it: dict) -> list[str]:
    raw = it.get("motivos_json") or it.get("motivos")
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
        return [str(x) for x in parsed] if isinstance(parsed, list) else [str(parsed)]
    except Exception:
        return [str(raw)]


def _rag_for_question(user_input: str, model: dict, cobertura_docs: dict) -> str:
    """Recupera trechos de documentos CVM/IPE relevantes para a pergunta."""
    items = model.get("items", [])
    port_tks = [str(it.get("ticker", "")).upper().replace(".SA", "") for it in items]
    n_docs = sum(1 for v in cobertura_docs.values() if v > 0)
    cov = (f"Documentos CVM/IPE indexados: {n_docs}/{len(port_tks)} empresas, "
           f"{sum(cobertura_docs.values()):,} trechos no total.")
    if n_docs == 0:
        return cov + " (Nenhum documento corporativo indexado para esta carteira.)"

    up = (user_input or "").upper()
    mencionados = [tk for tk in port_tks if tk and tk in up]
    com_docs = [tk for tk in port_tks if cobertura_docs.get(tk, 0) > 0]
    alvo = [tk for tk in (mencionados or com_docs) if cobertura_docs.get(tk, 0) > 0][:3]

    cache = st.session_state.setdefault("apb3_rag_cache", {})
    chunks: list[dict] = []
    for tk in alvo:
        if tk not in cache:
            try:
                c, _ = retrieve_chunks(tk, top_k_total=12, per_topic_k=3)
                cache[tk] = c or []
            except Exception:
                cache[tk] = []
        chunks.extend(cache[tk])

    if chunks:
        return cov + "\n" + format_rag_context(chunks, max_chars=4000)
    return cov + " (Documentos disponíveis, mas sem trechos recuperados para as empresas consultadas.)"


def _build_chat_context(model: dict, state: dict, macro_hist: dict,
                        fund: dict, consol: dict, weights: dict,
                        cobertura_docs: dict, rag_ctx: str) -> str:
    """Compila contexto REAL e completo do portfólio para o chat."""
    items = model.get("items", [])
    by_w = sorted(items, key=lambda it: -float(it.get("weight") or 0))
    lines: list[str] = []

    # 1 — Identificação da carteira
    lines.append("PORTFÓLIO ATUALMENTE AVALIADO:")
    nome_mod = model.get("name") or "Carteira modelo B3"
    ano_compra = model.get("ano_compra")
    lines.append(f"  Nome: {nome_mod}" + (f" | Ano de compra: {ano_compra}" if ano_compra else ""))
    lines.append(f"  Nº de empresas: {len(items)}")
    # Composição setorial
    metrics = model.get("metrics_json") or {}
    params = model.get("params_json") or {}
    for obj_name, obj in (("metrics", metrics), ("params", params)):
        if isinstance(obj, str):
            try:
                parsed = json.loads(obj)
                if obj_name == "metrics":
                    metrics = parsed
                else:
                    params = parsed
            except Exception:
                pass
    if metrics:
        met_parts = []
        for k in ("alpha_selic_medio", "alpha_ew_medio", "score_medio", "score_entrada_medio"):
            if k in metrics and metrics.get(k) is not None:
                met_parts.append(f"{k}={metrics.get(k)}")
        if met_parts:
            lines.append("  Metricas salvas da Criacao de Portfolio: " + " | ".join(met_parts))
    if params:
        par_parts = []
        for k in ("segmentos_analisados", "segmentos_aprovados", "min_anos_dre", "status_invest_reconciliacao"):
            if k in params and params.get(k) is not None:
                par_parts.append(f"{k}={params.get(k)}")
        if par_parts:
            lines.append("  Parametros/resultados da Criacao de Portfolio: " + " | ".join(par_parts))
    segs: dict[str, float] = {}
    for it in items:
        seg = str(it.get("segmento") or it.get("setor") or "—")
        segs[seg] = segs.get(seg, 0.0) + weights.get(str(it.get("ticker","")).upper().replace(".SA",""), 0.0)
    comp = ", ".join(f"{s}={w*100:.0f}%" for s, w in sorted(segs.items(), key=lambda x: -x[1]))
    lines.append(f"  Composição por segmento: {comp}")

    # 2 — Indicadores consolidados
    lines.append("\nINDICADORES CONSOLIDADOS DA CARTEIRA (ponderado por peso | média simples | nº empresas com dado):")
    for k, agg in consol.items():
        pond = _fmt_ind(k, agg.get("ponderada"))
        simp = _fmt_ind(k, agg.get("simples"))
        lines.append(f"  {_IND_LABEL.get(k,k)}: ponderada={pond} | simples={simp} | n={agg.get('n')}")
    if not consol:
        lines.append("  (Sem múltiplos suficientes no banco para consolidar.)")
    lines.append("  Obs.: valor de mercado não consta na base (apenas múltiplos e DRE).")

    # 3 — Dados por empresa (banco + carteira)
    lines.append("\nEMPRESAS DA CARTEIRA (dados do banco + scores da Criação de Portfólio):")
    for it in by_w:
        tk = str(it.get("ticker", "")).upper().replace(".SA", "")
        w  = weights.get(tk, 0.0) * 100
        f  = fund.get(tk, {})
        meta = [f"peso={w:.1f}%"]
        if it.get("segmento"): meta.append(f"segmento={it['segmento']}")
        if it.get("score") is not None: meta.append(f"score={float(it['score']):.1f}")
        if it.get("alpha_selic") is not None: meta.append(f"alphaSelic={float(it['alpha_selic']):+.1f}%")
        if it.get("ano_lider"): meta.append(f"líder_em={it['ano_lider']}")
        lines.append(f"  {tk} ({it.get('nome','')}) | " + " | ".join(meta))
        inds = " | ".join(
            f"{_IND_LABEL.get(c,c)}={_fmt_ind(c, f.get(c))}"
            for c in ("DY", "P/L", "P/VP", "ROE", "ROIC", "Margem_Liquida",
                      "Endividamento_Total", "Payout", "Liquidez_Corrente",
                      "cresc_receita", "cresc_lucro")
            if f.get(c) is not None
        )
        if inds:
            lines.append(f"    Fundamentos: {inds}")
        motivos = _parse_motivos(it)
        if motivos:
            lines.append(f"    Justificativa de aprovação: {'; '.join(motivos[:4])}")

    # 4 — Documentos / relatórios
    lines.append("\nDOCUMENTOS E RELATÓRIOS (CVM/IPE):")
    lines.append("  " + (rag_ctx or "Sem documentos.").replace("\n", "\n  "))

    # 5 — Análise qualitativa (se já executada)
    if state:
        port_an = state.get("port_analise", {})
        items_an = state.get("items_analisados", [])
        pesos_novos = state.get("pesos_novos", {})
        lines.append("\nANÁLISE QUALITATIVA (LLM) JÁ EXECUTADA:")
        lines.append(f"  Qualidade: {port_an.get('qualidade_carteira','N/D')} | "
                     f"Perspectiva 12m: {port_an.get('perspectiva_12m','N/D')} | "
                     f"Score médio: {port_an.get('score_medio','N/D')}")
        if port_an.get("resumo_executivo"):
            lines.append(f"  Resumo: {port_an['resumo_executivo'][:400]}")
        for it in sorted(items_an, key=lambda x: -pesos_novos.get(x.get("ticker", ""), 0))[:30]:
            an = it.get("analise", {})
            tk = it.get("ticker", "")
            extra = []
            if an.get("perspectiva"): extra.append(f"perspectiva={an['perspectiva']}")
            if an.get("acao_sugerida"): extra.append(f"ação={an['acao_sugerida']}")
            if an.get("resumo"): extra.append(f"tese={an['resumo'][:160]}")
            if extra:
                lines.append(f"  {tk}: " + " | ".join(extra))
    else:
        lines.append("\nANÁLISE QUALITATIVA (LLM): ainda não executada nesta sessão "
                     "(o usuário pode rodá-la no botão 'Executar Análise LLM'). "
                     "Os dados quantitativos acima já permitem responder a maioria das perguntas.")

    # 6 — Macro
    anos = sorted(macro_hist.keys(), reverse=True)[:2] if macro_hist else []
    if anos:
        lines.append("\nMACROECONOMIA:")
        for ano in sorted(anos):
            d = macro_hist[ano]
            parts = []
            if "selic" in d:  parts.append(f"Selic={d['selic']*100:.2f}%")
            if "ipca" in d:   parts.append(f"IPCA={d['ipca']*100:.2f}%")
            if "cambio" in d: parts.append(f"USD/BRL={d['cambio']:.2f}")
            if parts:
                lines.append(f"  {ano}: {', '.join(parts)}")

    return "\n".join(lines)


def _render_chat(model: dict, state: dict, macro_hist: dict,
                 tickers_tuple: tuple[str, ...], cobertura_docs: dict) -> None:
    """Chat contextual — responde com dados reais da carteira (sempre disponível)."""
    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    st.markdown('<div class="apb3-section-title">💬 Tire Dúvidas sobre o Portfólio</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<p style="font-size:0.78rem;color:#9CA3AF;margin-bottom:16px;">'
        'Pergunte sobre indicadores (DY, P/L, ROE…), médias da carteira, comparações '
        'com empresas <strong>fora</strong> da carteira, setores, ranking de múltiplos, '
        'a lógica da seleção, documentos CVM ou estratégia. A IA consulta a carteira, o '
        'universo B3 inteiro, os setores e os documentos — e gera gráficos quando ajudam.</p>',
        unsafe_allow_html=True,
    )

    col_chat_hdr, col_chat_clr = st.columns([5, 1])
    with col_chat_clr:
        if st.button("🗑️ Limpar chat", key="apb3_chat_clear", use_container_width=True):
            st.session_state.pop("apb3_chat_history", None)
            st.rerun()

    history: list[dict] = st.session_state.get("apb3_chat_history", [])
    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    user_input = st.chat_input("Pergunte sobre o portfólio…", key="apb3_chat_input")
    if user_input:
        history.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.chat_message("assistant"):
            chart_directives: list[dict] = []
            chart_meta: dict = {}
            with st.spinner("Consultando carteira, banco, setores e documentos…"):
                try:
                    weights = _weights_from_model(model)
                    fund    = _portfolio_fundamentals(tickers_tuple)
                    consol  = _consolidated_metrics(fund, weights)
                    rag_ctx = _rag_for_question(user_input, model, cobertura_docs)
                    base_context = _build_chat_context(
                        model, state, macro_hist, fund, consol, weights,
                        cobertura_docs, rag_ctx,
                    )
                    # Camada ampla: enriquece com universo B3, setores, fundamentos
                    # externos e Criação de Portfólio conforme a intenção da pergunta.
                    context, chart_meta = build_llm_context_for_portfolio_chat(
                        user_question=user_input,
                        base_context=base_context,
                        model=model,
                        weights=weights,
                        macro_hist=macro_hist,
                        portfolio_tickers=[it.get("ticker", "") for it in model.get("items", [])],
                        cobertura_docs=cobertura_docs,
                    )
                    resposta_raw = chat_com_portfolio(context, history[:-1], user_input)
                    resposta, chart_directives = parse_chart_directives(resposta_raw)
                    # Fallback: a LLM às vezes descreve o gráfico sem emitir a
                    # diretiva. Se pediram um gráfico e nenhuma veio, inferimos.
                    if not chart_directives:
                        chart_directives = infer_chart_directives(
                            user_input,
                            chart_meta.get("mentioned_tickers"),
                            chart_meta.get("peers"),
                        )
                except Exception as exc:
                    resposta = f"Erro ao consultar LLM: {exc}"
            st.markdown(resposta)
            if chart_directives:
                try:
                    render_charts_from_directives(chart_directives, chart_meta)
                except Exception as exc:
                    st.caption(f"⚠️ Não foi possível gerar os gráficos solicitados: {exc}")

        history.append({"role": "assistant", "content": resposta})
        st.session_state["apb3_chat_history"] = history


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
            'Avaliação de Portfólio B3</h1>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        '<strong style="color:#CBD5E1;">Etapa 3 de 3 · Avaliação do conjunto.</strong> '
        'Julga a carteira criada na aba <strong>Criação de Portfólio</strong> como um '
        'todo — diversificação, concentração por ativo e por setor, exposição a risco, '
        'dividendos e crescimento consolidados, qualidade média e adequação ao objetivo. '
        'Combina dados quantitativos, cenário macro, documentos CVM/IPE e interpretação '
        'LLM baseada em repertório de casas especializadas (XP, BTG, Itaú BBA). '
        'Responde: <em>esse conjunto forma uma carteira coerente, diversificada e defensável?</em>'
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
    if model.get("is_stale"):
        st.error(
            "O portfólio salvo usa uma versão antiga da metodologia. Recalcule "
            "e salve uma nova carteira na aba Criação de Portfólio antes da análise."
        )
        return

    items = model["items"]
    tickers_tuple = tuple(sorted(it["ticker"] for it in items))

    # ── Carrega macro + cobertura CVM ─────────────────────────────────────────
    with st.spinner("Carregando dados…"):
        macro_hist     = _db.load_macro_history()
        cobertura_docs = get_cobertura_docs(tickers_tuple)

    n_com_docs = sum(1 for v in cobertura_docs.values() if v > 0)
    total_chunks = sum(cobertura_docs.values())

    # ── Portfólio salvo + macro sempre visíveis ───────────────────────────────
    state = st.session_state.get("apb3_state", {})
    pesos_exib = state.get("pesos_novos") if state else None

    _render_portfolio_salvo(model, pesos_exib)

    # Cobertura RAG
    if n_com_docs > 0:
        st.markdown(
            f'<div style="font-size:.76rem;color:#34D399;margin:-8px 0 12px;">'
            f'📄 {n_com_docs}/{len(items)} empresas com documentos CVM '
            f'({total_chunks:,} chunks) — RAG ativo</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="font-size:.76rem;color:#718096;margin:-8px 0 12px;">'
            '📄 Sem documentos CVM — análise somente com dados quantitativos</div>',
            unsafe_allow_html=True,
        )

    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)
    _render_macro(macro_hist)
    st.markdown('<hr class="apb3-divider">', unsafe_allow_html=True)

    # ── Painel de análise LLM ─────────────────────────────────────────────────
    st.markdown('<div class="apb3-section-title">🤖 Análise Qualitativa via LLM</div>',
                unsafe_allow_html=True)

    if not llm_disponivel():
        st.warning(
            "Nenhum provedor LLM configurado. Adicione `OPENAI_API_KEY` e/ou "
            "`GEMINI_API_KEY` no `.env` ou nos Streamlit Secrets para ativar a análise LLM.",
            icon="⚠️",
        )
        return

    _provs = provedores_disponiveis()
    if _provs:
        _lbl = {"openai": "OpenAI", "gemini": "Gemini"}
        _txt = " → ".join(_lbl.get(p, p) for p in _provs)
        st.caption(f"🤖 Provedores LLM ativos (com fallback automático): **{_txt}**")

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
        with st.spinner("Carregando múltiplos e DRE do banco…"):
            mult_batch = _db.load_multiplos_historico_batch(tickers_tuple)
            dre_batch  = _db.load_demonstracoes_batch(tickers_tuple)

        result = _executar_analise(
            items, macro_hist, mode, mult_batch, dre_batch,
            cobertura_docs=cobertura_docs,
        )
        st.session_state["apb3_state"] = result
        st.rerun()

    # ── Exibe resultados (se a análise qualitativa já foi executada) ──────────
    if state:
        items_an    = state["items_analisados"]
        port_an     = state["port_analise"]
        pesos_novos = state["pesos_novos"]
        erros       = state.get("erros", [])

        # Erros de LLM persistem no state e reaparecem após o rerun — sem isso o
        # relatório saía em branco ("Relatório indisponível.") sem explicar a causa.
        if erros:
            with st.expander(f"⚠️ {len(erros)} falha(s) na análise LLM — relatório pode estar incompleto",
                             expanded=True):
                for e in erros:
                    st.markdown(f"- {e}")
                st.caption(
                    "Causas comuns: cota/limite atingido em **todos** os provedores "
                    "(OpenAI e Gemini), chave sem acesso ao modelo, ou timeout. "
                    "Configure `GEMINI_API_KEY` como fallback, verifique chave/cota e "
                    "clique novamente em **Executar Análise LLM**."
                )

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
    else:
        st.info(
            "Configure o modo acima e clique **🚀 Executar Análise LLM** para o "
            "relatório qualitativo completo. O chat abaixo já responde com os dados "
            "quantitativos reais da carteira (indicadores, fundamentos e documentos).",
            icon="ℹ️",
        )

    # Chat sempre disponível — usa dados reais da carteira mesmo sem análise qualitativa
    _render_chat(model, state, macro_hist, tickers_tuple, cobertura_docs)

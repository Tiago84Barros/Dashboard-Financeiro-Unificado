"""
views/fiis.py — Seleção de FIIs (fundos imobiliários)

Metodologia em abas (como Empresas B3, com as particularidades de FII):
  1. Ranking        — score DY 12m · P/VP · liquidez (BRAPI + CVM).
  2. Busca de ativo — detalhe por FII: histórico (P/VP·VPA), vacância, composição
                      e carteira de imóveis com região (tijolo/logística).
  3. Carteira-modelo — seleção diversificada por tipo (tijolo/papel/fof/híbrido).
  4. Backtest        — retorno total (preço + proventos reinvestidos) da carteira.

Fonte nativa do market.* (BRAPI Pro + CVM Informe Mensal). FII não tem DRE/ROE.
"""
from __future__ import annotations

import math
from html import escape

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

import core.market_read as _mr
from core.fii_integrated_model import (INTEGRATED_MODEL_VERSION,
                                       IntegratedEligibilityPolicy,
                                       apply_integrated_eligibility)
from core.fii_methodology import (FORMULA_VERSION, METHODOLOGY_VERSION, MacroScenario,
                                 classify_macro_regime, evaluate_publication_gate,
                                 score_fiis_by_type)
from core.fii_portfolio_monitor import build_fii_portfolio_monitor
from core.fii_portfolio_v4 import (LIVE_PORTFOLIO_STRATEGY_ID, PortfolioPolicy,
                                   optimize_diligence_portfolio)
from core.fii_validation import validation_supports_strategy
from core.fii_selection_explanations import build_selection_reports
from core.llm_b3 import llm_disponivel, provedores_disponiveis
from core.llm_context_fii import build_fii_chat_context
from core.llm_fii import chat_com_fiis
from data_pipeline.market import fii as _fz
from data_pipeline.utils.date_utils import fmt_datetime_br

# Metadados por tipo de FII: emoji, rótulo e cor de destaque do card.
_TIPO_META = {
    "tijolo":  ("🧱", "Tijolo",     "#00C896"),
    "papel":   ("📄", "Papel/CRI",  "#4A9EFF"),
    "fof":     ("🏢", "FoF",        "#B084F6"),
    "hibrido": ("🔀", "Híbrido",    "#F6C90E"),
}
_TIPO_ORDER = ["tijolo", "papel", "fof", "hibrido"]
_TIPO_OUTROS = ("🏦", "Outros", "#9CA3AF")

_CSS = """
<style>
.fii-hdr { display:flex;align-items:baseline;gap:10px;font-size:1.25rem;font-weight:800;
           text-transform:uppercase;letter-spacing:.04em;color:#E2E8F0;
           border-bottom:2px solid #1E2533;padding-bottom:8px;margin:24px 0 14px; }
.fii-hdr .cnt { font-size:0.74rem;color:#4A5568;font-weight:700;letter-spacing:.06em; }
/* KPIs do topo (cards CSS) */
.fii-kpi { background:#12151E;border:1px solid #1E2533;border-radius:10px;
           padding:12px 15px;margin-bottom:6px;border-left:3px solid #00C896; }
.fii-kpi .lbl { font-size:0.62rem;font-weight:700;text-transform:uppercase;
                letter-spacing:.08em;color:#4A5568;margin-bottom:5px; }
.fii-kpi .val { font-size:1.6rem;font-weight:800;line-height:1.05;color:#E2E8F0; }
.fii-kpi .sub { font-size:0.68rem;font-weight:700;margin-top:4px; }
.fii-card { background:#12151E;border:1px solid #1E2533;border-radius:12px;
            padding:12px 14px 10px;height:100%;transition:border-color .2s; }
.fii-card:hover { border-color:rgba(0,200,150,.35); }
.fii-top { display:flex;justify-content:space-between;align-items:center;margin-bottom:4px; }
.fii-tk  { font-size:0.95rem;font-weight:800;color:#E2E8F0;letter-spacing:.02em; }
.fii-score { font-size:0.66rem;font-weight:800;padding:2px 8px;border-radius:10px; }
.fii-nome { font-size:0.68rem;color:#718096;margin-bottom:2px;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.fii-seg  { font-size:0.62rem;color:#4A5568;margin-bottom:8px;
            overflow:hidden;text-overflow:ellipsis;white-space:nowrap; }
.fii-mini { display:flex;gap:12px;border-top:1px solid #1A2130;padding-top:7px; }
.fii-mini .lbl { display:block;font-size:0.54rem;font-weight:700;text-transform:uppercase;
                 letter-spacing:.06em;color:#4A5568; }
.fii-mini .val { display:block;font-size:0.86rem;font-weight:800;color:#E2E8F0;line-height:1.2; }
.fii-sc-high { background:rgba(0,200,150,.15);color:#00C896; }
.fii-sc-mid  { background:rgba(246,201,14,.15);color:#F6C90E; }
.fii-sc-low  { background:rgba(252,92,125,.15);color:#FC5C7D; }
.fii-info-card { background:linear-gradient(145deg,#12151E,#10131B);border:1px solid #1E2533;
                 border-left:3px solid #4A9EFF;border-radius:12px;padding:13px 15px;
                 margin:8px 0 12px;color:#A0AEC0;font-size:.78rem;line-height:1.5; }
.fii-info-card .title { color:#E2E8F0;font-size:.66rem;font-weight:800;
                        text-transform:uppercase;letter-spacing:.08em;margin-bottom:4px; }
.fii-scenario-grid { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;
                     margin:5px 0 12px; }
.fii-scenario { background:#12151E;border:1px solid #1E2533;border-radius:10px;
                padding:10px 12px; }
.fii-scenario .name { color:#718096;font-size:.61rem;font-weight:750;text-transform:uppercase;
                      letter-spacing:.06em;white-space:nowrap; }
.fii-scenario .impact { color:#E2E8F0;font-size:1.02rem;font-weight:850;margin-top:3px; }
.fii-scenario.pos { border-top:2px solid #00C896; }
.fii-scenario.pos .impact { color:#00C896; }
.fii-scenario.neg { border-top:2px solid #FC5C7D; }
.fii-scenario.neg .impact { color:#FC5C7D; }
.fii-selection-card { background:linear-gradient(145deg,#12151E,#10131B);border:1px solid #1E2533;
                      border-top:3px solid #00C896;border-radius:12px;margin:7px 0 10px;
                      overflow:hidden; }
.fii-selection-card summary { cursor:pointer;list-style:none;padding:13px 14px; }
.fii-selection-card summary::-webkit-details-marker { display:none; }
.fii-selection-card summary:hover { background:rgba(255,255,255,.018); }
.fii-selection-head { display:flex;align-items:center;justify-content:space-between;gap:8px; }
.fii-selection-ticker { color:#E2E8F0;font-size:1rem;font-weight:850; }
.fii-selection-rank { color:#00C896;background:rgba(0,200,150,.12);border-radius:12px;
                      padding:3px 8px;font-size:.64rem;font-weight:800;white-space:nowrap; }
.fii-selection-meta { color:#718096;font-size:.68rem;margin-top:3px; }
.fii-selection-body { border-top:1px solid #1E2533;padding:11px 14px 13px;color:#A0AEC0;
                      font-size:.74rem;line-height:1.45; }
.fii-selection-body .section { color:#CBD5E0;font-size:.62rem;font-weight:800;
                               text-transform:uppercase;letter-spacing:.07em;margin:8px 0 3px; }
.fii-selection-body ul { margin:3px 0 6px;padding-left:18px; }
.fii-selection-body li { margin-bottom:3px; }
.fii-selection-facts { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;
                       margin:5px 0 9px; }
.fii-selection-fact { background:#0E1118;border:1px solid #1A2130;border-radius:7px;
                      padding:6px 8px;color:#CBD5E0;font-size:.68rem; }
.fii-selection-evidence { color:#718096;border-top:1px dashed #253047;margin-top:9px;
                          padding-top:7px;font-size:.64rem; }
.fii-selection-caveat { background:rgba(246,201,14,.07);border:1px solid rgba(246,201,14,.18);
                        border-radius:8px;padding:7px 9px;margin-top:7px;color:#D6C56E; }
@media (max-width: 800px) { .fii-scenario-grid,.fii-selection-facts {
                            grid-template-columns:repeat(1,minmax(0,1fr)); } }
</style>
"""

_TABS = ["📊 Diligência", "🔎 Busca de ativo", "🧺 Carteira-modelo",
         "📈 Retrospectiva", "🧾 Revisão de dados"]

_SNAPSHOT_REQUIRED_FIELDS = (
    "ticker", "tipo", "dy_12m", "pvp", "liquidez_diaria", "history_months",
    "max_drawdown", "vacancia_fisica", "property_count", "region_count",
)


def _methodology_inputs_to_vitrine(inputs: pd.DataFrame) -> pd.DataFrame:
    """Projeta o snapshot metodológico no formato visual sem uma segunda consulta."""
    if inputs.empty:
        return inputs.copy()
    mapping = {
        "ticker": "Ticker", "name": "Nome", "sector": "Segmento", "tipo": "Tipo",
        "price": "Preço", "pvp": "P/VP", "dy_12m": "DY_12m",
        "liquidez_diaria": "Liquidez_Diaria", "patrimonio_liquido": "Patrimonio",
        "vpa": "VPA", "num_cotistas": "Cotistas", "tipo_gestao": "Gestao",
        "pct_imoveis": "Pct_Imoveis", "pct_papel": "Pct_Papel",
        "pct_caixa": "Pct_Caixa", "pct_fundos": "Pct_Fundos",
    }
    frame = inputs.rename(columns=mapping).copy()
    for column in (*mapping.values(), "updated_at", "cvm_ref_date", "vacancia_ref_date"):
        if column not in frame:
            frame[column] = pd.NA
    return frame[[*mapping.values(), "updated_at", "cvm_ref_date", "vacancia_ref_date"]]


def _snapshot_as_of(inputs: pd.DataFrame):
    dates = []
    for row in inputs.to_dict("records"):
        metadata = row.get("snapshot_metadata") or {}
        value = metadata.get("as_of_date") if isinstance(metadata, dict) else None
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.notna(parsed):
            dates.append(parsed.date())
    return min(dates) if dates else None


def _selection_status_copy(*, validation_applicable: bool, can_publish: bool) -> dict[str, str]:
    approved = validation_applicable and can_publish
    return {
        "title": (
            "## Seleção de FIIs — Universo Validado"
            if approved else "## Seleção de FIIs — Lista de Diligência"
        ),
        "tab": "📊 Seleção validada" if approved else "📊 Diligência",
        "footer": (
            "O universo bruto atende ao gate vigente; a Carteira-modelo ainda "
            "aplica elegibilidade e controles próprios."
            if approved else
            "O universo bruto permanece em diligência; a Carteira-modelo usa "
            "somente o subconjunto elegível e possui gate de publicação próprio."
        ),
    }


def render(show_header: bool = True) -> None:
    validation = _mr.load_fii_validation_status(METHODOLOGY_VERSION)
    validation_applicable = validation_supports_strategy(
        validation, LIVE_PORTFOLIO_STRATEGY_ID
    )
    st.markdown(_CSS, unsafe_allow_html=True)

    with st.spinner("Carregando snapshot auditável de FIIs…"):
        inputs = _mr.load_fii_methodology_inputs()
    df = _methodology_inputs_to_vitrine(inputs)
    if df.empty:
        if show_header:
            st.markdown("## Seleção de FIIs — Lista de Diligência")
            st.caption(
                f"Metodologia Integrada v{METHODOLOGY_VERSION.split('.')[0]}, "
                "específica por tipo · dados ainda indisponíveis"
            )
        if inputs.attrs.get("load_error"):
            st.error(
                "Não foi possível consultar o universo de FIIs agora. "
                "A conexão foi preservada em modo somente leitura; tente novamente."
            )
        else:
            st.info("Ainda não há FIIs no banco. Rode `python run_market_ingest.py fiis` "
                    "(+ `fiis-cvm`, `fiis-series`) para popular.")
        return
    df = df.copy()
    # P/VP efetivo (fix auditoria FII 2026-07): preço ÷ VPA CVM quando
    # disponível — a MESMA fonte da aba Busca; senão o priceToBook da brapi.
    # Score e exibição passam a usar o mesmo número.
    if "VPA" in df.columns:
        df["P/VP"] = [
            _fz.pvp_efetivo(p, v, b)
            for p, v, b in zip(df["Preço"], df["VPA"], df["P/VP"])
        ]
    scored_v4 = score_fiis_by_type(
        inputs.to_dict("records") if not inputs.empty else [],
        validation_status="passed" if validation_applicable else "unvalidated",
    )
    score_map = {r["ticker"]: r for r in scored_v4}
    df["Score"] = df["Ticker"].map(lambda ticker: (score_map.get(ticker) or {}).get("type_score"))
    df["Confiança"] = df["Ticker"].map(lambda ticker: (score_map.get(ticker) or {}).get("confidence"))
    df["Cobertura"] = df["Ticker"].map(lambda ticker: (score_map.get(ticker) or {}).get("coverage"))
    df["Status_Publicação"] = df["Ticker"].map(
        lambda ticker: (score_map.get(ticker) or {}).get("publication_status", "diligence_only"))
    gate = evaluate_publication_gate(
        scored_v4, expected_universe=len(df),
        validation_status="passed" if validation_applicable else "unvalidated",
        snapshot_as_of=_snapshot_as_of(inputs),
    )
    st.session_state["fii_raw_publication_gate"] = gate
    status_copy = _selection_status_copy(
        validation_applicable=validation_applicable,
        can_publish=gate.can_publish_recommendation,
    )
    if show_header:
        st.markdown(status_copy["title"])
        st.caption(
            f"Metodologia Integrada v{METHODOLOGY_VERSION.split('.')[0]}, específica por tipo · "
            + ("validação point-in-time aplicável ao motor atual"
               if validation_applicable
               else "validação do motor atual pendente")
        )
    ranked = df[df["Score"].notna()].sort_values(
        "Score", ascending=False
    ).reset_index(drop=True)
    health_metrics = _fii_data_health_metrics(df, ranked, inputs, scored_v4, gate)
    # O aviso do gate continua sempre visível e colorido — é um freio, não um
    # detalhe. O que desceu para o expander foi o diagnóstico numérico que o
    # acompanhava, e que ocupava quatro linhas em todas as abas.
    if gate.can_publish_recommendation:
        st.success("Cobertura, confiança e validação atendidas — "
                   "apta à publicação como Carteira Modelo.")
    else:
        st.warning(
            "Universo bruto em diligência: a lista completa ainda não atende ao "
            "gate de publicação. Isso não bloqueia automaticamente a Carteira-modelo, "
            "que usa somente o subconjunto elegível e aplica gates próprios. "
            "Os números do universo estão em “Qualidade dos dados”, logo abaixo."
        )
    _render_data_health_summary(health_metrics, gate)

    # Abas por botão (permitem trocar de aba programaticamente — ex.: card → Busca).
    active = st.session_state.get("fii_active_tab", 0)
    tab_labels = [status_copy["tab"], *_TABS[1:]]
    cols = st.columns(len(tab_labels))
    for i, (c, lab) in enumerate(zip(cols, tab_labels)):
        with c:
            if st.button(lab, width="stretch", key=f"fii_tab{i}",
                         type="primary" if active == i else "secondary"):
                st.session_state["fii_active_tab"] = i
                st.rerun()
    st.markdown("<hr style='margin:4px 0 16px;border-color:#1E2533;'>", unsafe_allow_html=True)

    if active == 0:
        _tab_ranking(df, ranked)
    elif active == 1:
        _tab_busca(df)
    elif active == 2:
        _tab_carteira(ranked)
    elif active == 3:
        _tab_backtest()
    else:
        _tab_evidence_review()


# ── Tab 1: Ranking (cards por tipo) ───────────────────────────────────────────

def _score_cls(score) -> str:
    if score is None or pd.isna(score):
        return "fii-sc-mid"
    return "fii-sc-high" if score >= 66 else "fii-sc-mid" if score >= 33 else "fii-sc-low"


def _fii_card_html(row: pd.Series) -> str:
    tk = escape(str(row["Ticker"]))
    nome = escape(str(row.get("Nome") or tk)[:34], quote=True)
    seg = escape(str(row.get("Segmento") or "—")[:30])
    score = row.get("Score")
    dy, pvp = row.get("DY_12m"), row.get("P/VP")
    confidence, coverage = row.get("Confiança"), row.get("Cobertura")
    _, _, color = _TIPO_META.get(str(row.get("Tipo") or "").lower(), _TIPO_OUTROS)
    sc_txt = f"{score:.0f}" if pd.notna(score) else "—"
    dy_txt = f"{dy*100:.1f}%" if pd.notna(dy) else "—"
    pvp_txt = f"{pvp:.2f}" if pd.notna(pvp) else "—"
    conf_txt = f"{confidence:.0%}" if pd.notna(confidence) else "—"
    cov_txt = f"{coverage:.0%}" if pd.notna(coverage) else "—"
    return (
        f'<div class="fii-card" style="border-top:3px solid {color};">'
        f'  <div class="fii-top"><span class="fii-tk">{tk}</span>'
        f'    <span class="fii-score {_score_cls(score)}">{sc_txt}</span></div>'
        f'  <div class="fii-nome" title="{nome}">{nome}</div>'
        f'  <div class="fii-seg">{seg}</div>'
        f'  <div class="fii-mini">'
        f'    <div><span class="lbl">DY 12m</span><span class="val">{dy_txt}</span></div>'
        f'    <div><span class="lbl">P/VP</span><span class="val">{pvp_txt}</span></div>'
        f'    <div><span class="lbl">Conf.</span><span class="val">{conf_txt}</span></div>'
        f'    <div><span class="lbl">Cob.</span><span class="val">{cov_txt}</span></div>'
        f'  </div>'
        f'</div>'
    )


def _kpi_html(label: str, value, sub: str | None = None,
              sub_color: str = "#00C896", accent: str = "#00C896") -> str:
    """Card CSS de KPI (rótulo, valor grande e sub opcional)."""
    label, value = escape(str(label)), escape(str(value))
    sub_html = (
        f'<div class="sub" style="color:{sub_color};">{escape(str(sub))}</div>'
        if sub else ""
    )
    return (f'<div class="fii-kpi" style="border-left-color:{accent};">'
            f'<div class="lbl">{label}</div><div class="val">{value}</div>{sub_html}</div>')


def _info_card_html(title: str, body: str, *, accent: str = "#4A9EFF") -> str:
    return (f'<div class="fii-info-card" style="border-left-color:{accent};">'
            f'<div class="title">{escape(title)}</div>{escape(body)}</div>')


def _fii_data_health_metrics(
    vitrine: pd.DataFrame,
    ranked: pd.DataFrame,
    inputs: pd.DataFrame,
    scored: list[dict],
    gate,
) -> dict[str, float | int | str]:
    """Calcula os números exibidos no informativo de consistência do App 4."""
    source_rows = len(inputs)
    required_coverage: list[float] = []
    for row in inputs.to_dict("records"):
        metadata = row.get("snapshot_metadata") or {}
        snapshot_cov = metadata.get("coverage") if isinstance(metadata, dict) else None
        value = snapshot_cov.get("coverage_pct") if isinstance(snapshot_cov, dict) else None
        if value is not None:
            try:
                required_coverage.append(float(value) / 100.0)
                continue
            except (TypeError, ValueError):
                pass
        present = sum(row.get(field) is not None and pd.notna(row.get(field))
                      for field in _SNAPSHOT_REQUIRED_FIELDS)
        required_coverage.append(present / len(_SNAPSHOT_REQUIRED_FIELDS))

    ready_count = sum(row.get("data_readiness_status") == "ready" for row in scored)
    confidence_qualified_count = sum(
        float(row.get("confidence") or 0.0) >= .75 for row in scored
    )
    return {
        "snapshot_rows": source_rows,
        "vitrine_rows": len(vitrine),
        "ranked_rows": len(ranked),
        "scoreable_rows": len(scored),
        "required_coverage": sum(required_coverage) / len(required_coverage)
        if required_coverage else 0.0,
        "ready_count": ready_count,
        "ready_fraction": ready_count / len(scored) if scored else 0.0,
        "confidence_qualified_count": confidence_qualified_count,
        "confidence_qualified_fraction": (
            confidence_qualified_count / len(scored) if scored else 0.0
        ),
        "median_confidence": float(getattr(gate, "median_confidence", 0.0) or 0.0),
        "snapshot_version": str(
            next((
                (row.get("snapshot_metadata") or {}).get("schema_version")
                for row in inputs.to_dict("records")
                if isinstance(row.get("snapshot_metadata"), dict)
                and (row.get("snapshot_metadata") or {}).get("schema_version")
            ),
            "fallback_market_tables",
        )),
    }


def _publication_gate_message(
    metrics: dict[str, float | int | str],
    gate,
) -> str:
    """Traduz o gate técnico em uma mensagem verificável na própria tela."""
    scoreable = int(metrics["scoreable_rows"])
    ready = int(metrics["ready_count"])
    ready_fraction = float(metrics["ready_fraction"])
    confidence_qualified = int(metrics["confidence_qualified_count"])
    confidence_qualified_fraction = float(metrics["confidence_qualified_fraction"])
    confidence = float(metrics["median_confidence"])
    parts = [
        f"Diagnóstico do universo completo: prontidão metodológica {ready}/{scoreable} "
        f"({ready_fraction:.1%}; mínimo 80%)",
        f"confiança ≥75%: {confidence_qualified}/{scoreable} "
        f"({confidence_qualified_fraction:.1%})",
        f"confiança mediana {confidence:.1%} (mínimo 75%)",
    ]
    if any("backtest" in reason or "robustez" in reason for reason in gate.reasons):
        parts.append("backtest point-in-time/robustez estatística pendente")
    if any("cobertura do universo" in reason for reason in gate.reasons):
        parts.append(f"cobertura do universo {float(gate.universe_coverage):.1%}")
    return " · ".join(parts)


def _tab_evidence_review() -> None:
    """Revisão humana explícita; decisões são versionadas e auditáveis."""
    from core.config import settings
    from core.fii_evidence_review import (
        load_pending_evidence, review_backlog_summary, review_evidence,
    )

    st.subheader("Revisão auditável de evidências documentais")
    st.markdown(_info_card_html(
        "O que esta etapa faz",
        "Valores extraídos de relatórios gerenciais só se tornam dados aceitos depois de uma "
        "decisão explícita. Cada decisão preserva documento, hash, página, parser, revisor e "
        "instante de conhecimento; rejeições automáticas não calibram o parser como revisão humana.",
        accent="#B084F6",
    ), unsafe_allow_html=True)
    summary = review_backlog_summary()
    if not summary.get("available"):
        st.info("A fila documental não está disponível neste banco: "
                + str(summary.get("reason") or "sem detalhes"))
        return
    k1, k2, k3 = st.columns(3)
    k1.markdown(_kpi_html("Evidências pendentes", summary.get("pending", 0),
                          accent="#F6C90E"), unsafe_allow_html=True)
    k2.markdown(_kpi_html("FIIs na fila", summary.get("pending_tickers", 0),
                          accent="#4A9EFF"), unsafe_allow_html=True)
    k3.markdown(_kpi_html("Revisões humanas", summary.get("human_reviewed", 0),
                          accent="#00C896"), unsafe_allow_html=True)

    initial = load_pending_evidence(limit=300)
    if not initial:
        st.success("Não há evidências pendentes neste banco.")
        return
    tickers = sorted({str(row.get("ticker") or "") for row in initial if row.get("ticker")})
    metrics = sorted({str(row.get("metric_name") or "") for row in initial if row.get("metric_name")})
    f1, f2 = st.columns(2)
    ticker = f1.selectbox("FII", ["(todos)", *tickers], key="fii_review_ticker")
    metric = f2.selectbox("Métrica", ["(todas)", *metrics], key="fii_review_metric")
    rows = load_pending_evidence(
        limit=50,
        tickers=[] if ticker == "(todos)" else [ticker],
        metrics=[] if metric == "(todas)" else [metric],
    )
    if not rows:
        st.info("Nenhuma evidência corresponde aos filtros.")
        return
    selected_id = st.selectbox(
        "Evidência",
        [int(row["id"]) for row in rows],
        format_func=lambda value: next(
            f"#{value} · {row.get('ticker') or 'sem ticker'} · {row.get('metric_name')} · "
            f"{float(row.get('confidence') or 0):.0%}"
            for row in rows if int(row["id"]) == value
        ),
        key="fii_review_evidence_id",
    )
    evidence = next(row for row in rows if int(row["id"]) == int(selected_id))
    meta = st.columns(4)
    meta[0].metric("Ticker", evidence.get("ticker") or "—")
    meta[1].metric("Métrica", evidence.get("metric_name") or "—")
    meta[2].metric("Valor extraído", str(evidence.get("normalized_value")))
    meta[3].metric("Confiança da extração", f"{float(evidence.get('confidence') or 0):.0%}")
    st.code(str(evidence.get("evidence_text") or ""), language=None, wrap_lines=True)
    st.caption(
        f"Documento: {evidence.get('document_type')} · referência: "
        f"{evidence.get('reference_date') or 'não informada'} · página: "
        f"{evidence.get('page_number') or 'não identificada'} · parser: "
        f"{evidence.get('parser_name')} {evidence.get('parser_version')} · SHA-256: "
        f"{str(evidence.get('content_sha256') or '')[:20]}…"
    )

    with st.form("fii_evidence_review_form", clear_on_submit=False):
        decision_label = st.radio(
            "Decisão", ["Aceitar", "Corrigir", "Rejeitar"], horizontal=True,
            key="fii_review_decision",
        )
        raw_value = evidence.get("normalized_value")
        try:
            default_value = float(raw_value)
        except (TypeError, ValueError):
            default_value = 0.0
        corrected = st.number_input(
            "Valor corrigido", value=default_value, format="%.6f",
            disabled=decision_label != "Corrigir",
        )
        reviewer_default = settings.OWNER_USER_ID or "app_operator"
        reviewer = st.text_input("Identificador do revisor", value=reviewer_default)
        note = st.text_area("Justificativa/observação", max_chars=2000)
        submitted = st.form_submit_button("Registrar decisão", type="primary")
    if submitted:
        decisions = {"Aceitar": "accepted", "Corrigir": "corrected", "Rejeitar": "rejected"}
        try:
            report = review_evidence(
                int(selected_id), decision=decisions[decision_label], reviewer_id=reviewer,
                corrected_value=corrected if decision_label == "Corrigir" else None,
                note=note,
            )
            st.success(
                f"Decisão registrada. Hash de auditoria: {report['review_hash'][:16]}…"
            )
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            st.error(f"Não foi possível registrar a revisão: {exc}")


def _render_data_health_summary(metrics: dict[str, float | int | str], gate) -> None:
    """Cobertura operacional e prontidão para recomendação, recolhidas por padrão.

    Auditoria não disputa espaço com a decisão: o rótulo do expander carrega os
    três números que resumem o estado dos dados, de modo que o essencial é legível
    sem abrir, e o detalhamento fica a um clique. A divergência de sincronização
    permanece fora do expander — é alarme, não diagnóstico de rotina.
    """
    with st.expander(
        f"Qualidade dos dados · prontidão {int(metrics['ready_count'])}/"
        f"{int(metrics['scoreable_rows'])} · confiança mediana "
        f"{float(metrics['median_confidence']):.0%} · campos essenciais "
        f"{float(metrics['required_coverage']):.0%}"
    ):
        _render_data_health_detail(metrics, gate)
    if metrics["vitrine_rows"] != metrics["snapshot_rows"]:
        st.warning(
            "Divergência de sincronização: a vitrine usada no ranking contém "
            f"{metrics['vitrine_rows']} fundos, mas o snapshot metodológico contém "
            f"{metrics['snapshot_rows']}. O ranking pode estar em cache ou em um build anterior. "
            "Após o redeploy, atualize a página para reconciliar as fontes."
        )


def _render_data_health_detail(metrics: dict[str, float | int | str], gate) -> None:
    st.caption(_publication_gate_message(metrics, gate))
    cards = st.columns(5)
    cards[0].markdown(
        _kpi_html("Snapshot consumido", f"{metrics['snapshot_rows']}/{metrics['snapshot_rows']}",
                  "inputs recebidos pelo App 4", accent="#4A9EFF"),
        unsafe_allow_html=True,
    )
    cards[1].markdown(
        _kpi_html("Campos essenciais", f"{metrics['required_coverage']:.1%}",
                  "média de 10 campos", accent="#00C896"),
        unsafe_allow_html=True,
    )
    cards[2].markdown(
        _kpi_html("FIIs pontuáveis", f"{metrics['scoreable_rows']}/{metrics['snapshot_rows']}",
                  f"ranking exibido: {metrics['ranked_rows']}", accent="#B084F6"),
        unsafe_allow_html=True,
    )
    cards[3].markdown(
        _kpi_html("Prontidão metodológica", f"{metrics['ready_count']}/{metrics['scoreable_rows']}",
                  "campos críticos + confiança ≥ 75%", accent="#F6C90E"),
        unsafe_allow_html=True,
    )
    cards[4].markdown(
        _kpi_html("Confiança mediana", f"{metrics['median_confidence']:.1%}",
                  "mínimo para publicação: 75%", accent="#FC5C7D"),
        unsafe_allow_html=True,
    )
    validation_pending = any(
        "backtest" in reason or "robustez" in reason for reason in gate.reasons
    )
    publication_note = (
        "A publicação continua bloqueada enquanto a confiança e a validação point-in-time não forem aprovadas."
        if validation_pending else
        "A validação point-in-time foi aprovada; a publicação continua bloqueada somente pelos gates de "
        "cobertura crítica e confiança dos fundos."
    )
    st.markdown(_info_card_html(
        "Como interpretar estes números",
        "Snapshot consumido mede se o App 4 recebeu os dados. Campos essenciais mede a completude dos inputs. "
        "FIIs pontuáveis mede quantos possuem tipo válido. Prontidão metodológica exige cobertura dos campos críticos "
        "e confiança mínima de 75%; é diferente de apenas ter dados no snapshot. "
        f"{publication_note} "
        f"Fonte: {metrics['snapshot_version']}.",
        accent="#00C896",
    ), unsafe_allow_html=True)


def _scenario_cards_html(values: dict[str, float]) -> str:
    cards = []
    for name, value in values.items():
        css = "pos" if float(value) >= 0 else "neg"
        label = str(name).replace("_", " ").title()
        cards.append(
            f'<div class="fii-scenario {css}"><div class="name">{escape(label)}</div>'
            f'<div class="impact">{float(value):+.1%}</div></div>'
        )
    return '<div class="fii-scenario-grid">' + "".join(cards) + "</div>"


def _selection_card_html(explanation: dict, *, expanded: bool = False) -> str:
    ticker = escape(str(explanation.get("ticker") or "—"))
    fii_type = str(explanation.get("tipo") or "").lower()
    _, type_label, color = _TIPO_META.get(fii_type, _TIPO_OUTROS)
    strengths = "".join(
        f"<li>{escape(str(reason))}</li>" for reason in explanation.get("strengths") or []
    ) or "<li>Sem destaque quantitativo adicional.</li>"
    facts = "".join(
        f'<div class="fii-selection-fact">{escape(str(fact))}</div>'
        for fact in explanation.get("facts") or []
    )
    operating = "".join(
        f"<li>{escape(str(item))}</li>" for item in explanation.get("operating") or []
    )
    structure = "".join(
        f"<li>{escape(str(item))}</li>" for item in explanation.get("structure") or []
    )
    market = "".join(
        f"<li>{escape(str(item))}</li>" for item in explanation.get("market") or []
    )
    caveats = explanation.get("caveats") or []
    caveat_html = ""
    if caveats:
        caveat_html = (
            '<div class="section">Pontos que exigem diligência</div>'
            '<div class="fii-selection-caveat">' + "<br>".join(
                "• " + escape(str(caveat)) for caveat in caveats
            ) + "</div>"
        )
    open_attr = " open" if expanded else ""
    return (
        f'<details class="fii-selection-card" style="border-top-color:{color};"{open_attr}>'
        '<summary><div class="fii-selection-head">'
        f'<span class="fii-selection-ticker">{ticker}</span>'
        f'<span class="fii-selection-rank">#{int(explanation.get("rank") or 0)} de '
        f'{int(explanation.get("peer_count") or 0)} · top {int(explanation.get("top_percent") or 0)}%</span>'
        '</div>'
        f'<div class="fii-selection-meta">{escape(type_label)} · peso '
        f'{float(explanation.get("weight") or 0):.1%}</div></summary>'
        '<div class="fii-selection-body"><div class="section">Leitura objetiva</div>'
        f'<div class="fii-selection-facts">{facts}</div>'
        '<div class="section">Por que entrou · comparação com pares</div>'
        f'<ul>{strengths}</ul>'
        + (f'<div class="section">Qualidade e renda</div><ul>{operating}</ul>' if operating else '')
        + (f'<div class="section">Estrutura específica do fundo</div><ul>{structure}</ul>' if structure else '')
        + (f'<div class="section">Relação com o mercado brasileiro</div><ul>{market}</ul>' if market else '')
        + '<div class="section">Papel na seleção</div>'
        + f'<div>{escape(str(explanation.get("role") or "—"))}</div>{caveat_html}'
        + f'<div class="fii-selection-evidence">Confiança '
        f'{float(explanation.get("confidence") or 0):.0%} · cobertura '
        f'{float(explanation.get("coverage") or 0):.0%} · referência mais recente '
        f'{escape(str(explanation.get("data_reference") or "não informada"))} · correlações com '
        f'{int(explanation.get("relationship_months") or 0)} meses coincidentes. '
        'Dados ausentes não recebem valor estimado.</div></div></details>'
    )


def _render_fii_chat(*, items: list[dict], scored: list[dict], methodology_rows: list[dict],
                     result: dict, scenario: MacroScenario, reports: list[dict],
                     prices: pd.DataFrame) -> None:
    """Chat contextual da carteira de FIIs, inspirado na Avaliação de Portfólio B3."""
    st.markdown("---")
    st.markdown("#### 💬 Tire dúvidas sobre os FIIs e a seleção")
    st.markdown(_info_card_html(
        "Chat especializado em FIIs",
        "Pergunte por que um fundo entrou, compare pares, avalie riscos de vacância, crédito, "
        "indexadores, concentração, liquidez ou o efeito do cenário macro. A resposta usa os "
        "dados da seleção atual e explicita quando uma informação não está disponível.",
        accent="#B084F6",
    ), unsafe_allow_html=True)

    if not llm_disponivel():
        st.info("Nenhum provedor LLM configurado. Adicione OPENAI_API_KEY ou GEMINI_API_KEY.")
        return

    providers = provedores_disponiveis()
    provider_labels = {"openai": "OpenAI", "gemini": "Gemini"}
    st.caption("Provedor disponível: " + ", ".join(
        provider_labels.get(provider, provider) for provider in providers))

    signature = repr((
        tuple(sorted((str(item.get("ticker")), round(float(item.get("weight") or 0), 6))
                     for item in items)),
        tuple(sorted(scenario.__dict__.items())),
    ))
    previous_signature = st.session_state.get("fii_chat_context_signature")
    if previous_signature is not None and previous_signature != signature:
        st.session_state.pop("fii_chat_history", None)
        st.caption("O histórico foi reiniciado porque a seleção ou o cenário mudou.")
    st.session_state["fii_chat_context_signature"] = signature

    _, clear_col = st.columns([5, 1])
    with clear_col:
        if st.button("🗑️ Limpar chat", key="fii_chat_clear", width="stretch"):
            st.session_state.pop("fii_chat_history", None)
            st.rerun()

    suggestions = (
        "Quais são os principais riscos desta seleção?",
        "Quais FIIs se destacam em renda sem depender apenas do DY?",
        "Como o cenário de juros afeta tijolo, papel e FoFs desta carteira?",
    )
    suggested_input = None
    suggestion_cols = st.columns(3)
    for index, question in enumerate(suggestions):
        with suggestion_cols[index]:
            if st.button(question, key=f"fii_chat_suggestion_{index}",
                         width="stretch"):
                suggested_input = question

    history: list[dict] = st.session_state.get("fii_chat_history", [])
    for message in history:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    typed_input = st.chat_input(
        "Pergunte sobre os FIIs ou sobre a carteira…", key="fii_chat_input")
    user_input = suggested_input or typed_input
    if not user_input:
        return

    history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
    with st.chat_message("assistant"):
        with st.spinner("Consultando seleção, pares, cenário e qualidade dos dados…"):
            try:
                context = build_fii_chat_context(
                    user_question=user_input,
                    selected_items=items,
                    scored_rows=scored,
                    methodology_rows=methodology_rows,
                    portfolio_result=result,
                    scenario=scenario,
                    reports=reports,
                    prices=prices,
                )
                answer = chat_com_fiis(context, history[:-1], user_input)
            except Exception as exc:
                answer = f"Não foi possível consultar a LLM neste momento: {exc}"
        st.markdown(answer)
        st.caption("Análise educacional baseada nos dados disponíveis; não constitui recomendação.")
    history.append({"role": "assistant", "content": answer})
    st.session_state["fii_chat_history"] = history


def _comp_tipo_chart(pf: pd.DataFrame) -> None:
    """Barras horizontais da composição por tipo, rotuladas em PERCENTUAL."""
    import plotly.express as px
    comp = (pf.groupby("tipo")["peso"].sum() * 100).sort_values(ascending=True)
    fig = px.bar(x=comp.values, y=comp.index, orientation="h",
                 text=[f"{v:.0f}%" for v in comp.values])
    fig.update_traces(textposition="outside", marker_color="#4A9EFF", cliponaxis=False)
    fig.update_layout(height=200, margin=dict(l=0, r=28, t=4, b=0),
                      xaxis=dict(visible=False, range=[0, max(float(comp.max()) * 1.18, 1)]),
                      yaxis_title=None, plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)", font_color="#CBD5E0")
    st.plotly_chart(fig, width="stretch")


def _number_or_none(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _first_number(*values):
    for value in values:
        number = _number_or_none(value)
        if number is not None:
            return number
    return None


def _exposure_diversification(exposures) -> float | None:
    """Índice 1-HHI dos pesos divulgados; ausência não vira diversidade zero."""
    if not isinstance(exposures, dict) or not exposures:
        return None
    weights = [_number_or_none(value) for value in exposures.values()]
    weights = [value for value in weights if value is not None and value > 0]
    total = sum(weights)
    if not weights or total <= 0:
        return None
    return float(1 - sum((value / total) ** 2 for value in weights))


def _fii_specific_metrics_frame(inputs: pd.DataFrame) -> pd.DataFrame:
    """Métricas estruturais aplicáveis a cada tipo de FII para a tabela única."""
    columns = [
        "Ticker", "Qtd. ativos", "Vacância", "Imóveis", "Divers. imóveis",
        "Regiões", "Divers. regiões", "Qtd. papéis", "Divers. papel",
        "Qtd. fundos", "Divers. FoF", "Cresc. a.a.", "Pior queda", "Hist. (m)",
    ]
    if inputs is None or inputs.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for item in inputs.to_dict("records"):
        fii_type = str(item.get("tipo") or "").lower()
        is_brick = fii_type in {"tijolo", "hibrido"}
        is_paper = fii_type in {"papel", "hibrido"}
        is_fof = fii_type in {"fof", "hibrido"}
        holdings = item.get("holdings") if isinstance(item.get("holdings"), dict) else {}
        issuers = item.get("issuers") if isinstance(item.get("issuers"), dict) else {}
        regions = item.get("regions") if isinstance(item.get("regions"), dict) else {}
        property_count = _number_or_none(item.get("property_count"))
        financial_count = _number_or_none(item.get("financial_asset_count"))
        holding_count = len(holdings) if holdings else None
        total_count = _number_or_none(item.get("portfolio_item_count"))
        if total_count is None:
            parts = [value for value in (property_count, financial_count, holding_count)
                     if value is not None]
            total_count = sum(parts) if parts else None
        paper_diversification = _first_number(
            item.get("debtor_diversification"), item.get("issuer_diversification"))
        if paper_diversification is None:
            paper_diversification = _exposure_diversification(issuers)
        geographic_diversification = _number_or_none(item.get("geographic_diversification"))
        if geographic_diversification is None:
            geographic_diversification = _exposure_diversification(regions)
        rows.append({
            "Ticker": str(item.get("ticker") or ""),
            "Qtd. ativos": total_count,
            "Vacância": _number_or_none(item.get("vacancia_fisica")) if is_brick else None,
            "Imóveis": property_count if is_brick and property_count and property_count > 0 else None,
            "Divers. imóveis": (_number_or_none(item.get("property_diversification"))
                                 if is_brick else None),
            "Regiões": (_number_or_none(item.get("region_count"))
                        if is_brick and _number_or_none(item.get("region_count")) else None),
            "Divers. regiões": geographic_diversification if is_brick else None,
            "Qtd. papéis": financial_count if is_paper and financial_count and financial_count > 0 else None,
            "Divers. papel": paper_diversification if is_paper else None,
            "Qtd. fundos": holding_count if is_fof else None,
            "Divers. FoF": _exposure_diversification(holdings) if is_fof else None,
            "Cresc. a.a.": _number_or_none(item.get("total_return_trend")),
            "Pior queda": _number_or_none(item.get("max_drawdown")),
            "Hist. (m)": _number_or_none(item.get("history_months")),
        })
    return pd.DataFrame(rows, columns=columns).drop_duplicates("Ticker", keep="last")


def _enrich_portfolio_view(frame: pd.DataFrame, enrichment: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty or enrichment is None or enrichment.empty:
        return frame
    merged = frame.merge(enrichment, on="Ticker", how="left", suffixes=("", "__enrichment"))
    for column in [name for name in merged.columns if name.endswith("__enrichment")]:
        base = column.removesuffix("__enrichment")
        if base in merged.columns:
            merged[base] = merged[base].combine_first(merged[column])
            merged = merged.drop(columns=column)
        else:
            merged = merged.rename(columns={column: base})
    return merged


def _quality_portfolio_view(pf: pd.DataFrame) -> pd.DataFrame:
    """Normaliza a carteira retrospectiva garantindo uma única chave Ticker."""
    source = pf.drop(columns=["Ticker"], errors="ignore")
    return source.rename(columns={
        "ticker": "Ticker", "peso": "Peso", "tipo": "Tipo",
        "segmento": "Segmento", "Liquidez_Diaria": "Liquidez/dia", "dy_12m": "DY 12m",
        "pvp": "P/VP", "CAGR": "Cresc. a.a.", "Max_Drawdown": "Pior queda",
        "Hist_Meses": "Hist. (m)", "N_Regioes": "Regiões", "Num_Imoveis": "Imóveis",
        "score": "Score",
    })[["Ticker", "Peso", "Tipo", "Segmento", "Liquidez/dia", "DY 12m",
        "P/VP", "Cresc. a.a.", "Pior queda", "Hist. (m)", "Regiões", "Imóveis",
        "Score"]]


def _integrated_preference_controls() -> dict:
    """Controles globais da seleção integrada; todos afetam a mesma carteira."""
    st.markdown("**Carteira e elegibilidade**")
    c1, c2, c3, c4 = st.columns(4)
    n_assets = c1.slider("Nº máximo de FIIs", 8, 20, 12, key="fii_pref_integrated_assets")
    max_asset = c2.slider("Máx. por FII (%)", 5, 25, 15, 1,
                          key="fii_pref_integrated_max_asset") / 100
    min_liquidity = c3.slider("Liquidez mín. (R$ mi/dia)", 0.0, 20.0, 1.0, .5,
                              key="fii_pref_integrated_liquidity") * 1e6
    min_history = c4.slider("Histórico mín. (meses)", 0, 60, 24, 6,
                            key="fii_pref_integrated_history")

    c5, c6, c7, c8 = st.columns(4)
    min_dy = c5.slider("DY 12m mín. (%)", 0.0, 20.0, 8.0, .5,
                       key="fii_pref_integrated_dy") / 100
    max_drawdown = c6.slider("Drawdown máx. tolerado (%)", 10, 60, 35, 5,
                             key="fii_pref_integrated_drawdown") / 100
    correlation_penalty = c7.slider(
        "Penalização por correlação", 0.0, .30, .12, .02,
        key="fii_pref_integrated_correlation",
        help="Reduz pesos de combinações que historicamente oscilaram juntas.")
    pvp_below_one = c8.checkbox("Exigir P/VP abaixo de 1", value=False,
                                key="fii_pref_integrated_pvp")

    st.markdown("**Cenário macroeconômico e estresse**")
    m1, m2, m3 = st.columns(3)
    selic = m1.number_input("Selic (%)", 0.0, 30.0, 15.0, .25,
                            key="fii_pref_integrated_selic")
    ipca = m2.number_input("IPCA (%)", -2.0, 20.0, 4.5, .25,
                           key="fii_pref_integrated_ipca")
    delta = m3.number_input("Δ Selic 12m (p.p.)", -15.0, 15.0, 0.0, .25,
                            key="fii_pref_integrated_delta")
    s1, s2 = st.columns(2)
    vacancy_shock = s1.slider("Choque de vacância (%)", 0.0, 20.0, 8.0, 1.0,
                              key="fii_pref_integrated_vacancy") / 100
    credit_event = s2.slider("Eventos de crédito (%)", 0.0, 10.0, 3.0, .5,
                             key="fii_pref_integrated_credit") / 100

    with st.expander("Filtros patrimoniais opcionais para Tijolo/Híbridos"):
        g1, g2, g3 = st.columns(3)
        require_regions = g1.checkbox("Ao menos 2 regiões", value=False,
                                      key="fii_pref_integrated_regions")
        require_properties = g2.checkbox("Ao menos 8 imóveis", value=False,
                                         key="fii_pref_integrated_properties")
        require_multicategory = g3.checkbox("Multicategoria/híbrido", value=False,
                                            key="fii_pref_integrated_multicategory")
        st.caption("Quando ativados, dados patrimoniais ausentes reprovam o fundo; não viram zero.")

    uncertainty_cap = st.slider(
        "Incerteza ponderada máxima da carteira (%)", 20, 50, 35, 1,
        key="fii_pref_integrated_uncertainty",
        help="Limite matemático para construir uma carteira de diligência. "
             "Não eleva a confiança dos FIIs nem libera recomendação definitiva; "
             "o gate point-in-time continua independente.",
    ) / 100

    return {
        "scenario": MacroScenario(
            selic=selic, ipca=ipca, selic_change_12m=delta,
            vacancy_shock=vacancy_shock, credit_event_rate=credit_event,
        ),
        "portfolio_policy": PortfolioPolicy(
            max_assets=n_assets, max_asset=max_asset,
            min_daily_liquidity=min_liquidity,
            max_weighted_uncertainty=uncertainty_cap,
        ),
        "eligibility_policy": IntegratedEligibilityPolicy(
            min_daily_liquidity=min_liquidity, min_dy_12m=min_dy,
            min_history_months=min_history, max_drawdown=max_drawdown,
            require_pvp_below_one=pvp_below_one,
            require_multi_region=require_regions,
            require_min_properties=require_properties,
            require_multicategory=require_multicategory,
        ),
        "correlation_penalty": correlation_penalty,
    }


def _portfolio_return_correlation(prices: pd.DataFrame, order: list[str],
                                  *, min_months: int = 12) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retornos e correlação par-a-par com amostra mínima explícita."""
    if prices is None or prices.empty:
        return pd.DataFrame(), pd.DataFrame()
    returns = prices.pct_change(fill_method=None)
    usable = [ticker for ticker in order if ticker in returns.columns
              and int(returns[ticker].notna().sum()) >= min_months]
    if len(usable) < 2:
        return returns, pd.DataFrame()
    corr = returns[usable].corr(min_periods=min_months)
    return returns, corr


def _render_portfolio_correlation(weights: dict[str, float],
                                  fii_types: dict[str, str] | None = None) -> pd.DataFrame:
    order = sorted(
        weights,
        key=lambda ticker: (
            str((fii_types or {}).get(ticker) or ""), -float(weights[ticker]), ticker),
    )
    prices = _mr.load_precos_mensais(tuple(sorted(weights)))
    returns, corr = _portfolio_return_correlation(prices, order)
    st.markdown("#### Correlação entre os FIIs selecionados")
    if corr.empty or corr.notna().to_numpy().sum() <= len(corr):
        st.info("Não há pelo menos dois FIIs com 12 meses coincidentes para calcular a correlação.")
        return returns
    avg_correlation = _fz.mean_correlation(corr)
    st.caption(
        "Correlação dos retornos totais mensais, com mínimo de 12 observações por par. "
        + (f"Média entre os pares: **{avg_correlation:.2f}**. "
           if avg_correlation is not None else "")
        + "Azul indica menor correlação; rosa indica maior correlação."
    )
    import plotly.express as px
    color_scale = [[0.0, "#4A9EFF"], [0.5, "#E2E8F0"], [1.0, "#FC5C7D"]]
    fig = px.imshow(
        corr, text_auto=".2f", zmin=-1, zmax=1, aspect="auto",
        color_continuous_scale=color_scale,
    )
    fig.update_layout(
        height=max(340, 38 * len(corr)), margin=dict(l=0, r=0, t=8, b=0),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font_color="#CBD5E0", coloraxis_colorbar=dict(title="Correlação"),
    )
    fig.update_xaxes(side="bottom", tickangle=-45)
    st.plotly_chart(fig, width="stretch", key="fii_selected_correlation")
    return returns


def _render_portfolio_history_diagnostics(weights: dict[str, float],
                                          returns: pd.DataFrame) -> None:
    """Preserva comparação com o mercado e curva de diversificação histórica."""
    port_cols = [ticker for ticker in weights if ticker in getattr(returns, "columns", [])]
    common = returns[port_cols].dropna() if port_cols else pd.DataFrame()
    market = _mr.load_mercado_retorno_mensal()

    def annualized(series):
        clean = series.dropna()
        if len(clean) < 6:
            return None
        years = len(clean) / 12.0
        cumulative = float((1 + clean).prod() - 1)
        return (1 + cumulative) ** (1 / years) - 1 if years > .5 else None

    def volatility(series):
        clean = series.dropna()
        return float(clean.std(ddof=0) * (12 ** .5)) if len(clean) >= 6 else None

    ifix_volatility = None
    if len(common) >= 6:
        total = sum(weights[ticker] for ticker in port_cols) or 1.0
        portfolio_return = sum(
            common[ticker] * (weights[ticker] / total) for ticker in port_cols)
        comparison = portfolio_return.rename("Carteira").to_frame()
        if not market.empty:
            comparison = comparison.join(market[["IFIX", "Universo"]], how="inner")
        comparison = comparison.dropna(how="any")
        portfolio_annual = annualized(comparison["Carteira"]) if not comparison.empty else None
        ifix_annual = annualized(comparison["IFIX"]) if "IFIX" in comparison else None
        universe_annual = annualized(comparison["Universo"]) if "Universo" in comparison else None
        ifix_volatility = volatility(comparison["IFIX"]) if "IFIX" in comparison else None
        alpha = (portfolio_annual - ifix_annual
                 if portfolio_annual is not None and ifix_annual is not None else None)
        st.markdown("#### Retrospectiva da seleção vs. mercado")
        cards = st.columns(4)
        cards[0].markdown(_kpi_html(
            "Seleção atual", f"{portfolio_annual:.1%}" if portfolio_annual is not None else "—",
            accent="#00C896", sub=f"a.a. · {len(comparison)} meses", sub_color="#4A5568"),
            unsafe_allow_html=True)
        cards[1].markdown(_kpi_html(
            "IFIX", f"{ifix_annual:.1%}" if ifix_annual is not None else "—",
            accent="#9CA3AF", sub="índice de FIIs", sub_color="#4A5568"),
            unsafe_allow_html=True)
        cards[2].markdown(_kpi_html(
            "Mercado (mediana)", f"{universe_annual:.1%}" if universe_annual is not None else "—",
            accent="#9CA3AF", sub="universo de FIIs", sub_color="#4A5568"),
            unsafe_allow_html=True)
        cards[3].markdown(_kpi_html(
            "vs. IFIX", f"{alpha:+.1%}" if alpha is not None else "—",
            accent="#00C896" if (alpha or 0) >= 0 else "#FC5C7D",
            sub="retorno anualizado relativo", sub_color="#4A5568"),
            unsafe_allow_html=True)
        st.caption(
            "Diagnóstico in-sample das posições atuais na mesma janela. Como estabilidade "
            "histórica participa da seleção, este resultado não é evidência preditiva nem "
            "substitui o backtest point-in-time.")

    st.markdown("#### Risco × número de fundos")
    curve = _fz.risk_curve(returns, weights) if not returns.empty else []
    if len(curve) < 2:
        st.caption("Sem histórico suficiente entre os fundos selecionados para traçar a curva.")
        return
    curve_frame = pd.DataFrame(curve)
    curve_frame["Volatilidade anual (%)"] = curve_frame["vol"] * 100
    import plotly.express as px
    figure = px.line(curve_frame, x="n", y="Volatilidade anual (%)", markers=True)
    figure.update_traces(line_color="#00C896", marker_color="#00C896")
    if ifix_volatility is not None:
        figure.add_hline(
            y=ifix_volatility * 100, line_dash="dash", line_color="#FC5C7D",
            annotation_text=f"Vol. IFIX {ifix_volatility:.1%}",
            annotation_position="top right", annotation_font_color="#FC5C7D")
    figure.update_layout(
        height=300, margin=dict(l=0, r=10, t=6, b=0),
        xaxis=dict(title="Nº de fundos na carteira", dtick=1, tickmode="linear",
                   gridcolor="#1E2533"),
        yaxis=dict(title="Volatilidade anual (%)", gridcolor="#1E2533"),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_color="#CBD5E0")
    months = int(curve_frame["meses"].iloc[0]) if "meses" in curve_frame else 0
    effective = _fz.effective_n(weights)
    st.caption(
        f"Efeito incremental da diversificação na mesma janela comum de {months} meses. "
        f"Número efetivo: {effective:.1f} de {len(weights)}." if effective else
        f"Efeito incremental da diversificação na mesma janela comum de {months} meses.")
    st.plotly_chart(figure, width="stretch", key="fii_integrated_risk_curve")


def _merge_portfolio_views(primary: pd.DataFrame | None,
                           complementary: pd.DataFrame | None) -> pd.DataFrame:
    """Une as seleções v4 e complementar sem duplicar ativos ou métricas comuns."""
    frames = [frame.copy() for frame in (primary, complementary)
              if frame is not None and not frame.empty]
    if not frames:
        return pd.DataFrame()
    merged = frames[0]
    for extra in frames[1:]:
        merged = merged.merge(extra, on="Ticker", how="outer", suffixes=("", "__extra"))
        for column in list(merged.columns):
            if not column.endswith("__extra"):
                continue
            base = column.removesuffix("__extra")
            if base in merged.columns:
                merged[base] = merged[base].combine_first(merged[column])
                merged = merged.drop(columns=column)
            else:
                merged = merged.rename(columns={column: base})
    preferred = [
        "Ticker", "Tipo", "Segmento", "Peso", "Score", "Peso v4", "Peso complementar",
        "Score v4", "Score complementar", "Confiança", "Cobertura", "DY 12m",
        "P/VP", "Liquidez/dia", "Qtd. ativos", "Vacância", "Imóveis",
        "Divers. imóveis", "Regiões", "Divers. regiões", "Qtd. papéis",
        "Divers. papel", "Qtd. fundos", "Divers. FoF", "Cresc. a.a.",
        "Pior queda", "Hist. (m)", "Status",
    ]
    ordered = [column for column in preferred if column in merged.columns]
    ordered.extend(column for column in merged.columns if column not in ordered)
    sort_columns = [column for column in ("Peso", "Peso v4", "Peso complementar")
                    if column in merged.columns]
    result = merged[ordered]
    if sort_columns:
        result = result.sort_values(by=sort_columns, ascending=False, na_position="last")
    return result.reset_index(drop=True)


def _render_portfolio_table(slot, primary: pd.DataFrame | None,
                            complementary: pd.DataFrame | None = None) -> None:
    show = _merge_portfolio_views(primary, complementary)
    if show.empty:
        return
    slot.dataframe(show, width="stretch", hide_index=True, column_config={
        "Peso": st.column_config.NumberColumn(format="percent"),
        "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
        "Peso v4": st.column_config.NumberColumn(format="percent"),
        "Peso complementar": st.column_config.NumberColumn(format="percent"),
        "Score v4": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f"),
        "Score complementar": st.column_config.ProgressColumn(
            min_value=0, max_value=100, format="%.1f"),
        "Confiança": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
        "Cobertura": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent"),
        "DY 12m": st.column_config.NumberColumn(format="percent"),
        "P/VP": st.column_config.NumberColumn(format="%.2f"),
        "Liquidez/dia": st.column_config.NumberColumn(format="R$ %.0f"),
        "Qtd. ativos": st.column_config.NumberColumn(format="%d", help="Total de itens declarado na carteira do fundo."),
        "Vacância": st.column_config.NumberColumn(format="percent", help="Vacância física; aplicável a tijolo e híbridos."),
        "Imóveis": st.column_config.NumberColumn(format="%d"),
        "Divers. imóveis": st.column_config.NumberColumn(
            format="percent", help="1−HHI da participação na receita por imóvel; exige cobertura mínima de 60%."),
        "Regiões": st.column_config.NumberColumn(format="%d"),
        "Divers. regiões": st.column_config.NumberColumn(
            format="percent", help="1−HHI da exposição regional divulgada."),
        "Qtd. papéis": st.column_config.NumberColumn(
            format="%d", help="Quantidade de ativos financeiros declarados; aplicável a papel e híbridos."),
        "Divers. papel": st.column_config.NumberColumn(
            format="percent", help="1−HHI por devedor ou emissor disponível; não presume que emissor seja devedor."),
        "Qtd. fundos": st.column_config.NumberColumn(
            format="%d", help="Quantidade de fundos investidos identificados no look-through."),
        "Divers. FoF": st.column_config.NumberColumn(
            format="percent", help="1−HHI dos pesos dos fundos investidos; aplicável a FoFs e híbridos."),
        "Cresc. a.a.": st.column_config.NumberColumn(format="percent"),
        "Pior queda": st.column_config.NumberColumn(format="percent"),
        "Hist. (m)": st.column_config.NumberColumn(format="%d"),
    })


def _render_grupo(tipo_key: str, grupo: pd.DataFrame) -> None:
    """Cabeçalho do tipo + grade de cards (4 por linha), com botão Analisar."""
    emoji, label, color = _TIPO_META.get(tipo_key, _TIPO_OUTROS)
    st.markdown(
        f'<div class="fii-hdr" style="border-color:{color}55;">'
        f'<span>{emoji} {label}</span><span class="cnt">· {len(grupo)} FIIs</span></div>',
        unsafe_allow_html=True)
    grupo = grupo.reset_index(drop=True)
    for i in range(0, len(grupo), 4):
        cols = st.columns(4, gap="small")
        for j, (_, row) in enumerate(grupo.iloc[i:i + 4].iterrows()):
            with cols[j]:
                st.markdown(_fii_card_html(row), unsafe_allow_html=True)
                if st.button("Analisar 🔎", key=f"fii_an_{row['Ticker']}",
                             width="stretch"):
                    st.session_state["fii_sel_ticker"] = row["Ticker"]
                    st.session_state["fii_active_tab"] = 1
                    st.rerun()


def _tab_ranking(df: pd.DataFrame, ranked: pd.DataFrame) -> None:
    fora = len(df) - len(ranked)
    c1, c2, c3, c4 = st.columns([2, 1.3, 1, 1])
    with c1:
        seg = st.selectbox("Segmento", ["(todos)"] + _mr.load_fii_segmentos(), index=0)
    with c2:
        tipos = ["(todos)"] + sorted(ranked["Tipo"].dropna().unique().tolist())
        tipo = st.selectbox("Tipo", tipos, index=0,
                            help="tijolo=imóveis · papel=CRI · fof=cotas de FII · híbrido")
    with c3:
        dy_min = st.slider("DY 12m mín. (%)", 0.0, 20.0, 0.0, 0.5)
    with c4:
        pvp_max = st.slider("P/VP máx.", 0.5, 1.5, 1.3, 0.05)

    view = ranked.copy()
    if seg != "(todos)":
        view = view[view["Segmento"] == seg]
    if tipo != "(todos)":
        view = view[view["Tipo"] == tipo]
    view = view[(view["DY_12m"].fillna(0) * 100 >= dy_min) & (view["P/VP"].fillna(99) <= pvp_max)]

    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(_kpi_html("FIIs no ranking", len(view), accent="#4A9EFF"),
                unsafe_allow_html=True)
    if not view.empty:
        top = view.iloc[0]
        k2.markdown(_kpi_html("🏆 Top", top["Ticker"], f"↑ {top['Score']:.0f} pts",
                              accent="#F6C90E"), unsafe_allow_html=True)
        k3.markdown(_kpi_html("DY 12m mediano", f"{view['DY_12m'].median()*100:.1f}%"),
                    unsafe_allow_html=True)
        k4.markdown(_kpi_html("P/VP mediano", f"{view['P/VP'].median():.2f}",
                              accent="#B084F6"), unsafe_allow_html=True)

    if view.empty:
        st.warning("Nenhum FII atende aos filtros.")
    else:
        tipo_lower = view["Tipo"].astype(str).str.lower()
        vistos: set[str] = set()
        for tk_tipo in _TIPO_ORDER:
            grupo = view[tipo_lower == tk_tipo]
            if not grupo.empty:
                _render_grupo(tk_tipo, grupo)
                vistos.add(tk_tipo)
        resto = view[~tipo_lower.isin(vistos)]      # tipos None/desconhecidos
        if not resto.empty:
            _render_grupo("__outros__", resto)
        with st.expander("📋 Ver como tabela"):
            show = view[["Ticker", "Nome", "Segmento", "Tipo", "Preço", "DY_12m",
                         "P/VP", "VPA", "Liquidez_Diaria", "Cotistas", "Score",
                         "Confiança", "Cobertura", "Status_Publicação"]]
            st.dataframe(show, width="stretch", hide_index=True, column_config={
                "Nome": st.column_config.TextColumn("Nome", width="medium"),
                "Preço": st.column_config.NumberColumn("Preço", format="R$ %.2f"),
                "DY_12m": st.column_config.NumberColumn("DY 12m", format="percent"),
                "P/VP": st.column_config.NumberColumn("P/VP", format="%.2f"),
                "VPA": st.column_config.NumberColumn("VPA", format="R$ %.2f"),
                "Liquidez_Diaria": st.column_config.NumberColumn("Liquidez/dia", format="R$ %.0f"),
                "Cotistas": st.column_config.NumberColumn("Cotistas", format="%d"),
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
                "Confiança": st.column_config.ProgressColumn("Confiança", min_value=0, max_value=1, format="percent"),
                "Cobertura": st.column_config.ProgressColumn("Cobertura", min_value=0, max_value=1, format="percent"),
                "Status_Publicação": "Status",
            })
    ts = df["updated_at"].max() if "updated_at" in df.columns else None
    raw_gate = st.session_state.get("fii_raw_publication_gate")
    status_copy = _selection_status_copy(
        # O gate só pode ser publicável quando a validação PIT aplicável passou.
        validation_applicable=bool(raw_gate and raw_gate.can_publish_recommendation),
        can_publish=bool(raw_gate and raw_gate.can_publish_recommendation),
    )
    st.caption(f"Metodologia Integrada {METHODOLOGY_VERSION}: comparação somente dentro de cada categoria; "
               f"dados ausentes reduzem cobertura e confiança, sem imputação neutra. "
               f"{fora} fundos ficaram sem score por tipo ausente/inválido. "
               f"Atualizado: {fmt_datetime_br(ts) if ts is not None else '—'}. "
               + status_copy["footer"])


# ── Tab 2: Busca de ativo (detalhe por FII) ───────────────────────────────────

def _fmt_pct(v) -> str:
    return f"{v*100:.1f}%" if v is not None and pd.notna(v) else "—"


def _fmt_money(v, dec: int = 2) -> str:
    return f"R$ {v:,.{dec}f}" if v is not None and pd.notna(v) else "—"


_TIPOS_TIJOLO = {"tijolo", "hibrido"}


def _tab_busca(df: pd.DataFrame) -> None:
    st.caption("Selecione um FII para ver o histórico fundamentalista, vacância, "
               "composição e a carteira de imóveis (com a região, p/ tijolo/logística).")

    opts = df.sort_values("Score", ascending=False, na_position="last")
    labels = {f"{r['Ticker']} — {r['Nome']}": r["Ticker"] for _, r in opts.iterrows()}
    if not labels:
        st.info("Sem FIIs no banco.")
        return
    # pré-seleção vinda de um card da aba Ranking (botão "Analisar")
    keys = list(labels.keys())
    pre = st.session_state.pop("fii_sel_ticker", None)
    idx = next((i for i, (_, t) in enumerate(labels.items()) if t == pre), 0) if pre else 0
    sel = st.selectbox("FII", keys, index=idx)
    tk = labels[sel]

    d = _mr.load_fii_one(tk)
    if d is None or d.empty:
        st.warning(f"Sem dados para {tk}.")
        return
    tipo = (d.get("Tipo") or "").strip().lower()

    # ── Cabeçalho / KPIs ──────────────────────────────────────────────────────
    st.markdown(f"### {d.get('Ticker')} · {d.get('Nome') or ''}")
    tags = [t for t in (d.get("Tipo"), d.get("Segmento"), d.get("Gestao")) if t]
    if tags:
        st.caption(" · ".join(str(t) for t in tags))

    pl = d.get("Patrimonio")
    cot = (f"{int(d['Cotistas']):,}".replace(",", ".")
           if pd.notna(d.get("Cotistas")) else "—")
    vac = d.get("Vacancia")
    if tipo in _TIPOS_TIJOLO:
        ref = d.get("Vacancia_Ref")
        vac_val = _fmt_pct(vac)
        vac_sub = f"Status Invest{' · ' + str(ref) if ref else ''}"
    else:
        vac_val, vac_sub = "n/a", "não se aplica (papel/FoF)"

    r1 = st.columns(4)
    r1[0].markdown(_kpi_html("Preço", _fmt_money(d.get("Preço"))), unsafe_allow_html=True)
    r1[1].markdown(_kpi_html("P/VP", f"{d.get('P/VP'):.2f}" if pd.notna(d.get("P/VP")) else "—",
                             accent="#B084F6"), unsafe_allow_html=True)
    r1[2].markdown(_kpi_html("DY 12m", _fmt_pct(d.get("DY_12m")), accent="#00C896"),
                   unsafe_allow_html=True)
    r1[3].markdown(_kpi_html("VPA", _fmt_money(d.get("VPA")), accent="#4A9EFF"),
                   unsafe_allow_html=True)
    r2 = st.columns(4)
    r2[0].markdown(_kpi_html("Patrimônio", _fmt_money(pl / 1e9, 2) + " bi" if pd.notna(pl) else "—"),
                   unsafe_allow_html=True)
    r2[1].markdown(_kpi_html("Cotistas", cot, accent="#4A9EFF"), unsafe_allow_html=True)
    r2[2].markdown(_kpi_html("Vacância", vac_val, sub=vac_sub, sub_color="#4A5568",
                             accent="#F6C90E"), unsafe_allow_html=True)
    r2[3].markdown(_kpi_html("Liquidez/dia", _fmt_money(d.get("Liquidez_Diaria"), 0)),
                   unsafe_allow_html=True)

    st.divider()

    # ── Histórico fundamentalista (P/VP e VPA mensais) ────────────────────────
    st.markdown("#### 📈 Histórico fundamentalista")
    met = _mr.load_fii_metrics_mensal(tk)
    if met.empty:
        st.info("Sem série mensal da CVM para este FII (rode `fiis-metrics`). "
                "Abaixo, ainda assim, o histórico de preço e proventos.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.caption("P/VP mensal (preço bruto ÷ VPA)")
            pvp_serie = met.dropna(subset=["P/VP"])[["Data", "P/VP"]]
            if pvp_serie.empty:
                st.caption("— sem preço bruto casado com o VPA.")
            else:
                st.line_chart(pvp_serie.set_index("Data"))
        with c2:
            st.caption("VPA mensal (valor patrimonial da cota)")
            st.line_chart(met[["Data", "VPA"]].dropna().set_index("Data"))

    # proventos (série) — sempre que houver
    series = _mr.load_fii_series((tk,))
    divs = (series.get("dividendos") or {}).get(tk) or []
    if divs:
        dv = pd.DataFrame(divs, columns=["Data", "Provento"])
        dv["Data"] = pd.to_datetime(dv["Data"], errors="coerce")
        dv = dv.dropna(subset=["Data"]).set_index("Data").sort_index()
        st.caption("Proventos pagos por cota (R$)")
        st.bar_chart(dv["Provento"])

    st.divider()

    # ── Composição de ativos ──────────────────────────────────────────────────
    comp = {"Imóveis": d.get("Pct_Imoveis"), "Papel/CRI": d.get("Pct_Papel"),
            "Caixa": d.get("Pct_Caixa"), "Cotas de FII": d.get("Pct_Fundos")}
    comp = {k: float(v) for k, v in comp.items() if v is not None and pd.notna(v)}
    if comp:
        st.markdown("#### 🧩 Composição de ativos")
        st.caption("Participação por classe de ativo sobre o ativo total (CVM).")
        st.bar_chart(pd.Series(comp, name="Composição"))

    st.divider()

    # ── Imóveis do fundo ──────────────────────────────────────────────────────
    st.markdown("#### 🏢 Imóveis do fundo")
    if tipo and tipo not in _TIPOS_TIJOLO:
        st.info(f"Este FII é do tipo **{tipo}** — não detém imóveis diretamente "
                "(carteira de papel/cotas). Sem lista de imóveis.")
        return
    imoveis = _mr.load_fii_imoveis(tk)
    n = d.get("Num_Imoveis")
    n_val = int(n) if pd.notna(n) else (len(imoveis) if not imoveis.empty else 0)
    regioes = imoveis["Região"].dropna().nunique() if not imoveis.empty else 0
    area_tot = (f"{imoveis['Área_m2'].sum():,.0f}".replace(",", ".")
                if (not imoveis.empty and imoveis["Área_m2"].notna().any()) else "—")
    cols = st.columns(3)
    cols[0].markdown(_kpi_html("Nº de imóveis", n_val, accent="#00C896"), unsafe_allow_html=True)
    cols[1].markdown(_kpi_html("Regiões", regioes or "—", accent="#B084F6"), unsafe_allow_html=True)
    cols[2].markdown(_kpi_html("Área total (m²)", area_tot, accent="#4A9EFF"), unsafe_allow_html=True)
    if not imoveis.empty:
        st.dataframe(imoveis, width="stretch", hide_index=True, column_config={
            "Imóvel": st.column_config.TextColumn("Imóvel", width="medium"),
            "Área_m2": st.column_config.NumberColumn("Área (m²)", format="%.0f"),
            "Vacância": st.column_config.NumberColumn("Vacância", format="percent"),
            "Pct_Receita": st.column_config.NumberColumn("% Receita", format="percent"),
        })
        # resumo por região (relevante p/ logística/tijolo)
        if imoveis["Região"].notna().any():
            por_reg = imoveis.groupby("Região").size().sort_values(ascending=False)
            st.caption("Imóveis por região")
            st.bar_chart(por_reg)
    else:
        st.info("Ainda não há imóveis coletados para este FII. Rode "
                "`python run_market_ingest.py fiis-imoveis` (coleta best-effort por scraping).")


# ── Tab 3: Carteira-modelo ────────────────────────────────────────────────────

def _tab_carteira(ranked: pd.DataFrame) -> None:
    st.subheader("Preferências da seleção")
    st.markdown(_info_card_html(
        "Seleção Integrada de FIIs · v6.5",
        "Um único motor combina elegibilidade histórica, score específico por tipo, "
        "qualidade dos dados, cenário macroeconômico, concentração e correlação. "
        "DY, P/VP e liquidez entram uma única vez, sem somar scores concorrentes.",
        accent="#00C896",
    ), unsafe_allow_html=True)
    with st.container(border=True):
        preferences = _integrated_preference_controls()

    # Transparência de defasagem (fix auditoria FII 2026-07): a decisão usa
    # dados CVM do último informe publicado e vacância de scraping — o
    # usuário precisa ver de QUANDO são antes de montar a carteira.
    _avisos = []
    if "cvm_ref_date" in ranked.columns:
        _ref_cvm = pd.to_datetime(ranked["cvm_ref_date"], errors="coerce").max()
        if pd.notna(_ref_cvm):
            _avisos.append(f"dados CVM (VPA/PL/composição) do informe de "
                           f"**{_ref_cvm.strftime('%m/%Y')}**")
    if "vacancia_ref_date" in ranked.columns:
        _ref_vac = pd.to_datetime(ranked["vacancia_ref_date"], errors="coerce").max()
        if pd.notna(_ref_vac):
            _avisos.append(f"vacância coletada por scraping em "
                           f"**{_ref_vac.strftime('%d/%m/%Y')}** (data da coleta, "
                           "não do dado)")
    if _avisos:
        st.markdown(_info_card_html(
            "Defasagem das fontes",
            " · ".join(item.replace("**", "") for item in _avisos) +
            ". Preço, DY e liquidez vêm da última ingestão Brapi.",
            accent="#F6C90E",
        ), unsafe_allow_html=True)
    _carteira_integrada(preferences)


def _carteira_integrada(preferences: dict):
    st.subheader(f"Resultado da seleção · Metodologia Integrada v{METHODOLOGY_VERSION.split('.')[0]}")
    st.markdown(_info_card_html(
        "Como a carteira é construída",
        "Primeiro são aplicados filtros de elegibilidade. Depois, cada FII é comparado apenas "
        "com fundos do mesmo tipo. Os pesos equilibram qualidade, confiança, renda, cenários, "
        "concentração e correlação; o rebalanceamento continua orientado por eventos.",
        accent="#00C896",
    ), unsafe_allow_html=True)
    scenario = preferences["scenario"]
    portfolio_policy = preferences["portfolio_policy"]
    eligibility_policy = preferences["eligibility_policy"]
    st.markdown(_info_card_html(
        "Regime quantitativo",
        classify_macro_regime(scenario).replace("_", " ").title(),
        accent="#4A9EFF",
    ), unsafe_allow_html=True)

    inputs = _mr.load_fii_methodology_inputs()
    eligible_rows, eligibility = apply_integrated_eligibility(
        inputs.to_dict("records") if not inputs.empty else [], eligibility_policy)
    st.markdown(_info_card_html(
        "Universo elegível",
        f"{eligibility['eligible_count']} de {eligibility['universe_count']} FIIs passaram "
        "pelos filtros escolhidos. Ausências em métricas exigidas reprovam o ativo; não recebem "
        "nota neutra.",
        accent="#4A9EFF" if eligible_rows else "#FC5C7D",
    ), unsafe_allow_html=True)
    if eligibility.get("exclusion_counts"):
        with st.expander("Diagnóstico dos filtros de elegibilidade"):
            exclusions = pd.DataFrame([
                {"Motivo": reason, "FIIs excluídos": count}
                for reason, count in eligibility["exclusion_counts"].items()
            ])
            st.dataframe(exclusions, hide_index=True, width="stretch")
    if not eligible_rows:
        st.error("Nenhum FII atende à combinação escolhida. Relaxe os filtros de elegibilidade.")
        st.session_state.pop("fii_port", None)
        return None

    validation = _mr.load_fii_validation_status(METHODOLOGY_VERSION)
    validation_status = (
        "passed" if validation_supports_strategy(validation, LIVE_PORTFOLIO_STRATEGY_ID)
        else "unvalidated"
    )
    scored = score_fiis_by_type(eligible_rows, validation_status=validation_status)
    investable_gate = evaluate_publication_gate(
        scored, expected_universe=len(eligible_rows),
        validation_status=validation_status,
        snapshot_as_of=_snapshot_as_of(inputs),
    )
    st.session_state["fii_investable_publication_gate"] = investable_gate
    investable_ready = sum(
        row.get("data_readiness_status") == "ready" for row in scored
    )
    gate_cards = st.columns(3)
    gate_cards[0].markdown(_kpi_html(
        "Prontidão do universo elegível",
        f"{investable_ready}/{len(scored)}",
        sub="mínimo de 80% para publicação", accent="#F6C90E",
    ), unsafe_allow_html=True)
    gate_cards[1].markdown(_kpi_html(
        "Confiança mediana elegível", f"{investable_gate.median_confidence:.1%}",
        sub="mínimo de 75%", accent="#00C896" if investable_gate.median_confidence >= .75 else "#FC5C7D",
    ), unsafe_allow_html=True)
    gate_cards[2].markdown(_kpi_html(
        "Validação PIT", "Aprovada" if validation_status == "passed" else "Pendente",
        sub=f"metodologia {METHODOLOGY_VERSION}",
        accent="#00C896" if validation_status == "passed" else "#FC5C7D",
    ), unsafe_allow_html=True)

    # O universo de correlação replica o pool máximo do otimizador e evita
    # consultar séries de centenas de fundos a cada alteração dos controles.
    per_type = max(int(portfolio_policy.max_assets), 12)
    correlation_candidates: list[str] = []
    for fii_type in _TIPO_ORDER:
        correlation_candidates.extend([
            str(row["ticker"]) for row in scored if row.get("tipo") == fii_type
        ][:per_type])
    correlation_candidates = list(dict.fromkeys(correlation_candidates))
    candidate_prices = _mr.load_precos_mensais(tuple(sorted(correlation_candidates)))
    _, candidate_correlation = _portfolio_return_correlation(
        candidate_prices, correlation_candidates, min_months=12)
    previous_weights: dict[str, float] = {}
    active_model: dict = {}
    try:
        from core.fii_portfolio_model import load_active_fii_portfolio_model
        active_model = load_active_fii_portfolio_model()
        previous_weights = {
            str(item.get("ticker") or ""): float(item.get("weight") or 0.0)
            for item in (active_model.get("items") or [])
            if item.get("ticker") and float(item.get("weight") or 0.0) > 0
        }
    except (RuntimeError, ValueError, TypeError, SQLAlchemyError):
        previous_weights = {}
    result = optimize_diligence_portfolio(
        scored, scenario, policy=portfolio_policy,
        correlation_matrix=(candidate_correlation.to_dict()
                            if not candidate_correlation.empty else None),
        correlation_penalty=float(preferences["correlation_penalty"]),
        previous_weights=previous_weights,
    )
    if not result.get("items"):
        st.error("Não foi possível construir uma carteira factível: " +
                 " · ".join(result.get("blockers") or []))
        return None
    portfolio_can_publish = bool(
        result.get("can_publish") and investable_gate.can_publish_recommendation
    )
    st.session_state["fii_portfolio_can_publish"] = portfolio_can_publish
    if portfolio_can_publish:
        st.success("Carteira apta à publicação segundo os gates vigentes.")
    else:
        blockers = list(result.get("blockers") or []) + list(investable_gate.reasons)
        st.warning("Rascunho não publicável: " + " · ".join(dict.fromkeys(blockers)))
    items = result["items"]
    monitor = build_fii_portfolio_monitor(
        current_items=items,
        saved_model=active_model,
        snapshot_as_of=_snapshot_as_of(inputs),
        validation=validation,
        expected_strategy_id=LIVE_PORTFOLIO_STRATEGY_ID,
        expected_methodology_version=METHODOLOGY_VERSION,
        gate_can_publish=portfolio_can_publish,
        dimension_coverage=result.get("dimension_coverage") or {},
    )
    st.session_state["fii_portfolio_monitor"] = monitor
    _render_portfolio_monitor(monitor)
    show = pd.DataFrame([{
        "Ticker": item["ticker"], "Tipo": item["tipo"],
        "Segmento": item.get("sector"), "Peso": item["weight"],
        "Score": item["type_score"], "Confiança": item["confidence"],
        "Cobertura": item["coverage"], "DY 12m": item.get("dy_12m"),
        "P/VP": item.get("pvp"), "Liquidez/dia": item.get("liquidez_diaria"),
        "Status": item["publication_status"],
    } for item in items])
    specific_metrics = _fii_specific_metrics_frame(inputs)
    show = _enrich_portfolio_view(show, specific_metrics)
    table_slot = st.empty()
    _render_portfolio_table(table_slot, show)
    valid_pvp = [(float(item["pvp"]), float(item["weight"])) for item in items
                 if item.get("pvp") is not None and pd.notna(item.get("pvp"))]
    pvp_weight = sum(weight for _, weight in valid_pvp)
    weighted_pvp = (sum(value * weight for value, weight in valid_pvp) / pvp_weight
                    if pvp_weight else None)
    average_confidence = sum(float(item["confidence"]) * float(item["weight"])
                             for item in items)
    k1, k2, k3 = st.columns(3)
    k1.markdown(_kpi_html("Ativos selecionados", len(items),
                          sub=f"{eligibility['eligible_count']} elegíveis",
                          accent="#4A9EFF"),
                unsafe_allow_html=True)
    k2.markdown(_kpi_html("DY histórico ponderado", f"{result['trailing_yield_12m']:.1%}",
                          sub="distribuições dos últimos 12 meses; não é previsão"),
                unsafe_allow_html=True)
    k3.markdown(_kpi_html("P/VP ponderado",
                          f"{weighted_pvp:.2f}" if weighted_pvp is not None else "—",
                          accent="#B084F6"), unsafe_allow_html=True)
    k4, k5, k6 = st.columns(3)
    k4.markdown(_kpi_html("Número efetivo", f"{result['effective_assets']:.1f}",
                          accent="#F6C90E"), unsafe_allow_html=True)
    k5.markdown(_kpi_html("Confiança ponderada", f"{average_confidence:.0%}",
                          accent="#00C896"), unsafe_allow_html=True)
    corr_coverage = float((result.get("correlation_info") or {}).get("coverage") or 0)
    k6.markdown(_kpi_html("Cobertura da correlação", f"{corr_coverage:.0%}",
                          sub=f"{len(result.get('unresolved_dimensions') or [])} dimensões sem cobertura",
                          accent="#FC5C7D" if corr_coverage < .80 else "#4A9EFF"),
                unsafe_allow_html=True)
    if result.get("unresolved_dimensions"):
        st.caption(
            "Look-through adicional ainda sem cobertura suficiente: "
            + ", ".join(result["unresolved_dimensions"])
            + ". Esses limites só são aplicados quando observáveis; setor e "
              "emissor possuem histórico point-in-time obrigatório."
        )
    weights = {item["ticker"]: item["weight"] for item in items}
    fii_types = {item["ticker"]: item["tipo"] for item in items}
    returns = _render_portfolio_correlation(weights, fii_types)
    comp_left, comp_right = st.columns([2, 1])
    with comp_left:
        st.markdown(_info_card_html(
            "Cenários estruturais",
            "Sensibilidades quantitativas para comparação; não representam previsões.",
            accent="#4A9EFF",
        ), unsafe_allow_html=True)
        st.markdown(_scenario_cards_html(result["scenario_returns"]), unsafe_allow_html=True)
    with comp_right:
        st.caption("Composição por tipo (%)")
        _comp_tipo_chart(pd.DataFrame([
            {"tipo": item["tipo"], "peso": item["weight"]} for item in items
        ]))
    report_prices = _mr.load_precos_mensais(tuple(sorted(set(weights) | {"XFIX11", "BOVA11"})))
    explanations = build_selection_reports(items, scored, scenario=scenario, prices=report_prices)
    st.markdown("#### Por que estes FIIs avançaram para a seleção")
    st.markdown(_info_card_html(
        "Critério de comparação",
        "Comparação exclusiva com fundos do mesmo tipo. O score já incorpora renda, P/VP, "
        "liquidez, estabilidade histórica, governança e métricas próprias da categoria. "
        "O otimizador combina score (45%), confiança (30%), renda (25%) e uma preferência "
        "moderada por evidência nominal observada, penalizando concentração, estresse e "
        "correlação. "
        + ("Os destaques passaram pelos gates vigentes."
           if result.get("can_publish")
           else "Os destaques permanecem prioridades de diligência."),
        accent="#00C896",
    ), unsafe_allow_html=True)
    explanation_cols = st.columns(2)
    for index, explanation in enumerate(explanations):
        with explanation_cols[index % 2]:
            st.markdown(_selection_card_html(explanation, expanded=index < 2),
                        unsafe_allow_html=True)
    port = [{"ticker": item["ticker"], "peso": item["weight"], "tipo": item["tipo"],
             "score": item["type_score"], "dy_12m": item.get("dy_12m"),
             "pvp": item.get("pvp"), "segmento": item.get("sector")}
            for item in items]
    st.session_state["fii_port"] = weights
    _render_portfolio_history_diagnostics(weights, returns)
    _render_fii_chat(
        items=items,
        scored=scored,
        methodology_rows=inputs.to_dict("records"),
        result=result,
        scenario=scenario,
        reports=explanations,
        prices=report_prices,
    )
    _render_save_portfolio(
        port, {"metodo": "fii_integrated_v6_5", "model_version": INTEGRATED_MODEL_VERSION,
               "strategy_id": LIVE_PORTFOLIO_STRATEGY_ID,
               "methodology_version": METHODOLOGY_VERSION,
               "formula_version": FORMULA_VERSION,
               "regime": classify_macro_regime(scenario),
               "scenario": scenario.__dict__, "policy": result.get("policy"),
               "eligibility": eligibility.get("policy"),
               "correlation_penalty": result.get("correlation_penalty")},
        {"trailing_yield_12m": result["trailing_yield_12m"],
         "expected_yield": result["expected_yield"],
         "effective_assets": result["effective_assets"],
         "scenario_returns": result["scenario_returns"],
         "correlation_risk": result.get("correlation_risk"),
         "correlation_coverage": corr_coverage,
         "average_confidence": average_confidence,
         "eligible_count": eligibility["eligible_count"]}, key="fii_save_model_integrated_v6_5")
    return table_slot, show, specific_metrics


def _render_portfolio_monitor(monitor: dict) -> None:
    status = str(monitor.get("status") or "blocked")
    label = {"ok": "OK", "warning": "Revisão", "blocked": "Bloqueado"}.get(
        status, "Indisponível"
    )
    metrics = monitor.get("metrics") or {}
    with st.expander(
        f"🛡️ Monitoramento operacional · {label}",
        expanded=status != "ok",
    ):
        columns = st.columns(3)
        age = metrics.get("snapshot_age_days")
        turnover = metrics.get("turnover")
        drift = metrics.get("max_asset_weight_drift")
        columns[0].metric(
            "Idade do snapshot",
            f"{int(age)} dias" if age is not None else "—",
        )
        columns[1].metric(
            "Turnover vs. versão salva",
            f"{float(turnover):.1%}" if turnover is not None else "—",
        )
        columns[2].metric(
            "Maior drift por FII",
            f"{float(drift):.1%}" if drift is not None else "—",
        )
        checks = pd.DataFrame([
            {
                "Controle": item.get("code"),
                "Status": item.get("status"),
                "Diagnóstico": item.get("message"),
            }
            for item in (monitor.get("checks") or [])
        ])
        if not checks.empty:
            st.dataframe(checks, width="stretch", hide_index=True)
        st.caption(
            "Ausência de exposição permanece desconhecida e não é tratada como "
            "baixo risco. O monitor não executa ordens ou rebalanceamentos."
        )


def _render_save_portfolio(port: list[dict], params: dict, metrics: dict,
                           *, key: str) -> None:
    """Oferece a mesma persistência para qualquer método de seleção."""
    st.markdown("---")
    cs1, cs2 = st.columns([3, 1])
    with cs1:
        st.markdown(_info_card_html(
            "Publicação da carteira-modelo",
            "Ao salvar, o Dashboard Geral passa a usar exatamente estes ativos e pesos. "
            "A gravação só é liberada quando os gates de cobertura, consistência, "
            "atualização e validação forem aprovados.",
            accent="#F6C90E",
        ), unsafe_allow_html=True)
    with cs2:
        gate = st.session_state.get("fii_investable_publication_gate")
        can_publish = bool(
            gate and gate.can_publish_recommendation
            and st.session_state.get("fii_portfolio_can_publish", False)
        )
        if st.button("💾 Salvar carteira-modelo", width="stretch",
                     type="primary", key=key, disabled=not can_publish,
                     help=None if can_publish else "Publicação bloqueada pela metodologia integrada"):
            try:
                from core.fii_portfolio_model import save_fii_portfolio_model
                save_fii_portfolio_model(port, params, metrics)
                st.success(
                    "Carteira-modelo salva, relida e verificada na mesma transação."
                )
            except (RuntimeError, ValueError) as exc:
                st.error(f"Não foi possível salvar: {exc}")
            except SQLAlchemyError:
                st.error(
                    "Não foi possível salvar por uma falha transacional no banco. "
                    "Nenhuma substituição parcial foi mantida."
                )
    _render_portfolio_version_history(key)


def _render_portfolio_version_history(key: str) -> None:
    try:
        from core.fii_portfolio_model import (
            list_fii_portfolio_model_versions,
            restore_fii_portfolio_model,
        )
        versions = list_fii_portfolio_model_versions()
    except (RuntimeError, ValueError, SQLAlchemyError):
        return
    archived = [item for item in versions if item.get("status") == "archived"]
    if not archived:
        return
    with st.expander("🕘 Histórico e restauração da Carteira-modelo"):
        by_id = {str(item["id"]): item for item in archived}
        selected = st.selectbox(
            "Versão arquivada",
            options=list(by_id),
            format_func=lambda model_id: (
                f"{fmt_datetime_br(by_id[model_id].get('created_at'))} · "
                f"{by_id[model_id].get('item_count', 0)} FIIs · "
                f"{(by_id[model_id].get('params_json') or {}).get('methodology_version', 'legado')}"
            ),
            key=f"{key}_restore_version",
        )
        confirmed = st.checkbox(
            "Confirmo que desejo substituir a versão ativa por esta versão arquivada.",
            key=f"{key}_restore_confirm",
        )
        if st.button(
            "Restaurar versão selecionada",
            key=f"{key}_restore_action",
            disabled=not confirmed,
        ):
            try:
                restore_fii_portfolio_model(selected)
                st.success("Versão restaurada e verificada transacionalmente.")
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(f"Restauração bloqueada: {exc}")
            except SQLAlchemyError:
                st.error(
                    "Restauração bloqueada por uma falha transacional no banco; "
                    "a versão ativa anterior foi preservada."
                )


# ── Tab 4: Backtest ───────────────────────────────────────────────────────────

def _tab_backtest() -> None:
    st.subheader("Validação point-in-time da metodologia")
    validation = _mr.load_fii_validation_status(METHODOLOGY_VERSION)
    validation_metrics = validation.get("metrics") or {}
    pit = validation_metrics.get("backtest") or {}
    pit_status = str(validation.get("status") or "unvalidated")
    status_label = {"passed": "Aprovada", "blocked": "Bloqueada",
                    "failed": "Falhou"}.get(pit_status, "Não executada")
    cards = st.columns(5)
    cards[0].markdown(_kpi_html("Validação PIT", status_label,
                                accent="#00C896" if pit_status == "passed" else "#F6C90E"),
                      unsafe_allow_html=True)
    cards[1].markdown(_kpi_html("Períodos", int(pit.get("periods") or 0),
                                sub="mínimo metodológico: 36", sub_color="#4A5568",
                                accent="#4A9EFF"), unsafe_allow_html=True)
    cards[2].markdown(_kpi_html("Snapshots verificados",
                                f"{float(pit.get('verified_snapshot_fraction') or 0):.0%}",
                                sub="data de disponibilidade comprovada", sub_color="#4A5568",
                                accent="#B084F6"), unsafe_allow_html=True)
    cards[3].markdown(_kpi_html("Cobertura de retornos",
                                f"{float(pit.get('return_observation_coverage') or 0):.0%}",
                                accent="#4A9EFF"), unsafe_allow_html=True)
    ci = pit.get("excess_bootstrap") or {}
    ci_text = (f"{float(ci['lower']):+.2%} a {float(ci['upper']):+.2%}"
               if ci.get("lower") is not None and pd.notna(ci.get("lower")) else "—")
    cards[4].markdown(_kpi_html("IC bootstrap do excesso", ci_text,
                                sub="intervalo de 95%", sub_color="#4A5568",
                                accent="#00C896" if float(ci.get("lower") or -1) > 0 else "#F6C90E"),
                      unsafe_allow_html=True)
    blockers = validation.get("blockers") or []
    if blockers:
        st.warning("Validação ainda bloqueada: " + " · ".join(str(item) for item in blockers))
    else:
        st.success("Backtest PIT, cobertura, estabilidade, regimes e custos atenderam aos gates.")

    st.markdown("#### Retrospectiva da seleção atual")
    weights = st.session_state.get("fii_port")
    if not weights:
        st.info("Monte a carteira na aba **Carteira-modelo** primeiro.")
        return
    st.caption("Retrospectiva buy-and-hold das posições e pesos atuais. Há viés de "
               "sobrevivência e de seleção: não é um backtest point-in-time nem uma "
               "validação fora da amostra.")
    bench_nome = "IFIX (XFIX11)"   # a brapi não tem histórico do IFIX puro; XFIX11 (ETF) o replica
    series = _mr.load_fii_series(tuple(sorted(weights)))
    bench = _mr.load_fii_series(("XFIX11",)).get("precos", {}).get("XFIX11")
    # adjusted_close já é RETORNO TOTAL (ajustado por proventos+splits) → não
    # somar dividendos de novo (evita dupla contagem). XFIX11 é retorno total.
    serie, met = _fz.backtest(weights, series.get("precos", {}), {},
                              benchmark=bench, benchmark_nome=bench_nome)
    if serie.empty:
        st.warning("Sem série histórica suficiente para o backtest.")
        return
    bret = met.get("bench_retorno")
    alpha = (met["retorno_total"] - bret) if (met["retorno_total"] is not None and bret is not None) else None
    cart_val = f"{met['retorno_total']*100:.1f}%" if met["retorno_total"] is not None else "—"
    cart_sub = f"CAGR {met['cagr']*100:.1f}%" if met["cagr"] is not None else None
    alfa_val = f"{alpha*100:+.1f} p.p." if alpha is not None else "—"
    alfa_accent = "#00C896" if (alpha or 0) >= 0 else "#FC5C7D"
    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(_kpi_html("Carteira (total)", cart_val, cart_sub, accent="#00C896"),
                unsafe_allow_html=True)
    k2.markdown(_kpi_html("IFIX (benchmark)", f"{bret*100:.1f}%" if bret is not None else "—",
                          accent="#9CA3AF"), unsafe_allow_html=True)
    k3.markdown(_kpi_html("Alfa vs IFIX", alfa_val, accent=alfa_accent), unsafe_allow_html=True)
    k4.markdown(_kpi_html("Período", f"{met['anos']} anos · {met['n_ativos']} ativos",
                          accent="#4A9EFF"), unsafe_allow_html=True)
    cols = [c for c in ("Carteira", bench_nome) if c in serie.columns]
    st.line_chart(serie.set_index("Data")[cols])
    st.caption("Índice base 100 na mesma janela efetivamente disponível para carteira e "
               "XFIX11. Retorno total ajustado, sem custos ou impostos. O resultado mostra "
               "como a carteira atual teria se comportado; não reproduz decisões históricas.")

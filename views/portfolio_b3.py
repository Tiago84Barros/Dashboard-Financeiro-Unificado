"""
views/portfolio_b3.py — Criação de Portfólio B3
Roda o engine de scoring v2 em todos os segmentos, identifica líderes
históricos por ano (publication lag = 1), reconstrói o desempenho histórico
por segmento e monta uma carteira sugerida com empresas reais da B3.
"""
from __future__ import annotations

import html
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import core.b3_data as _db  # facade c/ feature flag MARKET_READ_SOURCE (default: legacy)
import core.data_reconciliacao as _recon
from core.b3_portfolio_model import save_b3_portfolio_model
from core.b3_methodology import MODEL_SCHEMA_VERSION, SCORE_VERSION
from design.componentes import card_metrica

# ── Importa engine compartilhado de empresas_b3 ───────────────────────────────
from views.empresas_b3 import (
    _GAMMA_DEF, _CAP_DEF, _SOFT_DEF, _REBAL_MONTH,
    _COR_POS, _COR_NEG, _COR_ALT, _COR_INF, _COR_NEU,
    _DIV_POR_ACAO_MAX,
    _apply_cap_soft, _apply_decay_penalty,
    _batch_yf_precos_mensais,
    _div_mes_sanitizado,
    _enrich_com_slopes,
    _fv, _fp, _logo_url,
    _get_pesos_setor,
    _plot_layout, _sec_hdr,
    _compute_score_entrada,
    _score_universo,
    _score_historico_ano,
    _select_n_heuristica,
    _so_acoes,
    _weights_from_scores,
    _yf_dividendos_anuais,
    _yf_multiplos_dividendos,
    _yf_trailing12m_divs,
)

# ── CSS incremental ───────────────────────────────────────────────────────────
_CSS = """
<style>
.pb3-seg-hdr {
    font-size:1.05rem;font-weight:800;color:#E2E8F0;margin:24px 0 2px;
}
.pb3-seg-sub {
    font-size:0.72rem;color:#4A5568;margin-bottom:10px;
}
.pb3-seg-val {
    font-size:0.80rem;color:#9CA3AF;margin-bottom:12px;
}
.pb3-emp-card {
    background:#12151E;border:1px solid #1E2533;border-radius:12px;
    padding:16px 16px 12px;text-align:center;
}
.pb3-emp-ticker { font-size:1.0rem;font-weight:800;color:#E2E8F0;margin:8px 0 2px; }
.pb3-emp-nome   { font-size:0.68rem;color:#718096;margin-bottom:8px; }
.pb3-emp-hist   { font-size:0.65rem;color:#4A5568;line-height:1.6; }
.pb3-emp-part   { font-size:1.10rem;font-weight:700;margin-top:8px; }
.pb3-lider-card {
    background:#12151E;border:1.5px solid rgba(0,200,150,.25);
    border-radius:12px;padding:20px 16px;text-align:center;
}
.pb3-lider-ticker { font-size:1.1rem;font-weight:800;color:#E2E8F0;margin:8px 0 2px; }
.pb3-lider-motivo { font-size:0.65rem;color:#00C896;font-weight:700;
                    text-transform:uppercase;letter-spacing:.08em; }
.pb3-lider-ano    { font-size:0.68rem;color:#4A5568;margin-top:4px; }
</style>
"""

_ANO_INICIO_DEFAULT = 2013


def _overlay_current_reconciled_row(
    hist_batch: dict[str, pd.DataFrame],
    df_mult_recon: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Atualiza a ultima linha historica com o snapshot reconciliado atual."""
    if not hist_batch or df_mult_recon.empty or "Ticker" not in df_mult_recon.columns:
        return hist_batch
    fields = [c for c in _recon.CANONICAL_MULTIPLOS_FIELDS if c in df_mult_recon.columns]
    by_ticker = df_mult_recon.drop_duplicates("Ticker").set_index("Ticker")
    out: dict[str, pd.DataFrame] = {}
    for tk, df_h in hist_batch.items():
        tk_clean = str(tk).upper().replace(".SA", "")
        if df_h is None or df_h.empty or tk_clean not in by_ticker.index:
            out[tk_clean] = df_h
            continue
        df = df_h.copy()
        if "Data" not in df.columns:
            out[tk_clean] = df
            continue
        datas = pd.to_datetime(df["Data"], errors="coerce")
        idx = datas.idxmax() if datas.notna().any() else df.index[-1]
        rec = by_ticker.loc[tk_clean]
        for field in fields:
            val = rec.get(field)
            if pd.notna(val):
                df.at[idx, field] = float(val)
        out[tk_clean] = df
    return out


def _build_entry_guard(
    df_mult_recon: pd.DataFrame,
    df_set: pd.DataFrame,
    hist_batch: dict[str, pd.DataFrame],
    anos_hist: dict[str, int] | None,
) -> tuple[dict[str, dict], pd.DataFrame]:
    """Calcula score_entrada atual por segmento para bloquear recomendacoes fracas."""
    if df_mult_recon.empty or df_set.empty or "Ticker" not in df_mult_recon.columns:
        return {}, pd.DataFrame()

    grupos_cols = ["ticker", "SETOR", "SUBSETOR", "SEGMENTO"]
    base = df_mult_recon.merge(
        df_set[grupos_cols].rename(columns={"ticker": "Ticker"}),
        on="Ticker",
        how="left",
    )
    frames: list[pd.DataFrame] = []
    for (setor, _subsetor, _segmento), grupo in base.groupby(["SETOR", "SUBSETOR", "SEGMENTO"], dropna=False):
        tks = [str(t).upper().replace(".SA", "") for t in grupo["Ticker"].dropna().tolist()]
        if not tks:
            continue
        scored = _score_universo(
            grupo.copy(),
            tks,
            _get_pesos_setor(str(setor)),
            df_hist_batch=hist_batch,
            group_col_prefer="SEGMENTO",
        )
        if scored.empty:
            continue
        entrada = _compute_score_entrada(scored, anos_hist)
        if not entrada.empty:
            frames.append(entrada)

    if not frames:
        return {}, pd.DataFrame()

    df_entry = pd.concat(frames, ignore_index=True)
    guard: dict[str, dict] = {}
    for _, row in df_entry.iterrows():
        tk = str(row.get("Ticker", "")).upper().replace(".SA", "")
        if not tk:
            continue
        guard[tk] = {
            "score_entrada": float(row.get("score_entrada", 0.0) or 0.0),
            "status_entrada": str(row.get("status_entrada", "")),
            "risk_penalty": float(row.get("r_penalty", 0.0) or 0.0),
        }
    return guard, df_entry


def _render_data_quality_box(summary: dict, audit: pd.DataFrame, hist_audit: pd.DataFrame) -> None:
    if not summary:
        return
    with st.expander("Qualidade dos fundamentos usados pela carteira", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            card_metrica("Tickers", int(summary.get("tickers", 0)), accent="#4A9EFF")
        with c2:
            card_metrica("Células inválidas", int(summary.get("celulas_invalidas", 0)),
                         accent="#FC5C7D")
        with c3:
            card_metrica("Correções web", int(summary.get("correcoes_web", 0)), accent="#F6C90E")
        with c4:
            card_metrica("Histórico saneado",
                         int(hist_audit["Ocorrencias"].sum()) if not hist_audit.empty else 0,
                         accent="#00C896")
        zeros = summary.get("campos_zero_suspeito") or []
        if zeros:
            st.warning("Campos tratados como ausentes por excesso de zeros: " + ", ".join(zeros))
        if not audit.empty:
            st.dataframe(audit.head(80), use_container_width=True, height=260)
        if not hist_audit.empty:
            st.caption("Outliers removidos do historico usado no backtest")
            st.dataframe(hist_audit.head(80), use_container_width=True, height=220)


# ══════════════════════════════════════════════════════════════════════════════
# Engine por segmento
# ══════════════════════════════════════════════════════════════════════════════

def _simular_seg_backtest(
    df_prec_seg: pd.DataFrame,
    lids_por_ano: dict[int, list[str]],
    pesos_por_ano: dict[int, dict[str, float]],
    aporte: float,
    taxa_selic_aa: float,
    selic_macro: dict[int, float],
    dividendos: dict[str, pd.Series] | None = None,
    cost_cfg=None,
) -> tuple[float, float, float, dict[str, float]]:
    """
    Reconstrói a carteira por segmento usando líderes identificados por ano.
    Reinveste dividendos mensais por ação (App1-compatible) quando dividendos != None.
    Retorna (valor_estrategia, valor_selic, valor_ew).
    """
    if df_prec_seg.empty:
        return 0.0, 0.0, 0.0, {}
    from core.transaction_costs import CostConfig, custo_compra
    if cost_cfg is None:
        cost_cfg = CostConfig.desligado()

    all_tks = list(df_prec_seg.columns)
    cotas_est: dict[str, float] = {tk: 0.0 for tk in all_tks}
    cotas_ew:  dict[str, float] = {tk: 0.0 for tk in all_tks}
    selic_acum = 0.0
    ultimo_ano = -1
    pesos_est: dict[str, float] = {}
    last_prices: dict[str, float] = {}

    for dt, row in df_prec_seg.iterrows():
        ano = dt.year
        mes = dt.month
        selic_aa_ano = selic_macro.get(ano, taxa_selic_aa)
        taxa_m = (1 + selic_aa_ano) ** (1 / 12) - 1
        selic_acum = selic_acum * (1 + taxa_m) + aporte

        # Rebalance realista (auditoria 2026-07): líderes do ano N assumem a
        # partir de ABRIL (FY N−1 publicado até 31/03). Jan–mar mantêm a
        # carteira do ano anterior.
        if ano != ultimo_ano and mes >= _REBAL_MONTH:
            ultimo_ano = ano
            lids = lids_por_ano.get(ano, [])
            if lids:
                pesos_est = pesos_por_ano.get(
                    ano, {tk: 1.0 / len(lids) for tk in lids})
            # sem líderes p/ o ano (ex.: ano corrente, fora do loop de
            # scoring): mantém a carteira vigente — antes a estratégia
            # parava de aportar enquanto Selic/EW seguiam (viés contra a
            # estratégia no ano corrente).

        # Reinvestimento de dividendos — idêntico ao App1
        if dividendos:
            for tk in all_tks:
                px_tk = float(row.get(tk, 0) or 0)
                if px_tk <= 0:
                    continue
                div = _div_mes_sanitizado(dividendos.get(tk), ano, mes, tk, px_tk)
                if div > 0:
                    if cotas_est[tk] > 0:
                        cotas_est[tk] += div * cotas_est[tk] / px_tk
                    if cotas_ew[tk] > 0:
                        cotas_ew[tk] += div * cotas_ew[tk] / px_tk

        # Estratégia
        est_disp = [tk for tk in pesos_est if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        if est_disp:
            tw = sum(pesos_est.get(tk, 0.0) for tk in est_disp) or 1.0
            for tk in est_disp:
                gross = aporte * pesos_est.get(tk, 0.0) / tw
                net = max(gross - custo_compra(tk, gross, cost_cfg), 0.0)
                cotas_est[tk] += net / float(row[tk])

        # EW benchmark
        ew_disp = [tk for tk in all_tks if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        for tk in ew_disp:
            last_prices[tk] = float(row[tk])
        if ew_disp:
            for tk in ew_disp:
                gross = aporte / len(ew_disp)
                net = max(gross - custo_compra(tk, gross, cost_cfg), 0.0)
                cotas_ew[tk] += net / float(row[tk])

    def _val(cotas: dict[str, float]) -> float:
        return sum(
            cotas[tk] * last_prices[tk]
            for tk in all_tks
            if tk in cotas and tk in last_prices
        )

    contrib_est = {
        tk: cotas_est[tk] * last_prices[tk]
        for tk in all_tks
        if tk in cotas_est and tk in last_prices
    }
    return _val(cotas_est), selic_acum, _val(cotas_ew), contrib_est


def _rank_ticker(score_map: dict[str, float], ticker: str) -> int | None:
    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    for idx, (tk, _) in enumerate(ranked, start=1):
        if tk == ticker:
            return idx
    return None


def _pode_incluir_maior_participacao(
    ticker: str,
    ultimo_lid: dict[str, int],
    score_proximo: dict[str, float],
    ano_ref: int,
    max_anos_lid: int,
) -> tuple[bool, str, int | None, int | None]:
    """Replica a regra do app1: maior participacao precisa ter recencia ou rank atual forte."""
    ultimo = ultimo_lid.get(ticker)
    rank_atual = _rank_ticker(score_proximo, ticker)
    if ultimo is not None and (ano_ref - int(ultimo)) <= int(max_anos_lid):
        return True, "Maior participacao com lideranca recente", ultimo, rank_atual
    if rank_atual is not None and rank_atual <= 3:
        return True, "Maior participacao ainda bem ranqueada no score atual", ultimo, rank_atual
    return False, "Maior participacao descartada por lideranca antiga/baixo rank atual", ultimo, rank_atual


def _processar_segmento(
    tickers: list[str],
    hist_batch: dict[str, pd.DataFrame],
    df_precos_all: pd.DataFrame,
    setor: str,
    subsetor: str,
    segmento: str,
    taxa_selic_aa: float,
    selic_macro: dict[int, float],
    macro_history: dict[int, dict[str, float]],
    aporte: float,
    ano_inicio: int,
    gamma: float,
    cap: float,
    soft: float,
    dividendos: dict[str, pd.Series] | None = None,
) -> dict | None:
    """
    Roda o engine de scoring ano-a-ano para um segmento.
    Retorna dict com líderes, backtest e score para o próximo ano, ou None.
    """
    if len(tickers) < 1:
        return None

    pesos = _get_pesos_setor(setor)
    tk_grupos = {tk: {"SETOR": setor, "SUBSETOR": subsetor, "SEGMENTO": segmento}
                 for tk in tickers}

    ano_atual = pd.Timestamp.now().year
    anos_lideranca: dict[str, int] = {}
    liderancas_hist: dict[str, list[int]] = {tk: [] for tk in tickers}
    lids_por_ano: dict[int, list[str]]    = {}
    pesos_por_ano: dict[int, dict[str, float]] = {}
    anos_com_score: list[int]              = []
    score_rows: list[dict]                 = []
    lideres_rows: list[dict]               = []
    constraint_warnings: list[str]         = []

    for ano in range(ano_inicio, ano_atual):
        score_map = _score_historico_ano(
            hist_batch, tickers, ano, pesos, tk_grupos, lag=1,
            macro_by_year=macro_history or None,
        )
        if not score_map:
            continue
        score_map = _apply_decay_penalty(score_map, anos_lideranca)
        anos_com_score.append(ano)
        for tk, score in score_map.items():
            score_rows.append({
                "Ano": ano, "ticker": tk, "Score_Ajustado": float(score),
                "SETOR": setor, "SUBSETOR": subsetor, "SEGMENTO": segmento,
            })

        ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
        scores_desc = [s for _, s in ranked[:3]]
        from core.portfolio_constraints import minimum_assets_for_cap
        n = _select_n_heuristica(scores_desc) if len(ranked) >= 2 else 1
        n = min(len(ranked), max(n, minimum_assets_for_cap(cap)))
        lids = [tk for tk, _ in ranked[:n] if tk in tickers]

        lids_por_ano[ano] = lids
        for tk in lids:
            lideres_rows.append({
                "Ano": ano, "ticker": tk,
                "SETOR": setor, "SUBSETOR": subsetor, "SEGMENTO": segmento,
            })
        if lids and len(lids) >= 2:
            try:
                w = _apply_cap_soft(_weights_from_scores(lids, score_map, gamma), cap, soft)
            except ValueError as exc:
                w = {tk: 1.0 / len(lids) for tk in lids}
                constraint_warnings.append(f"{ano}: {exc}")
        elif lids:
            w = {lids[0]: 1.0}
        else:
            w = {}
        pesos_por_ano[ano] = w

        novos = set(lids)
        for tk in list(anos_lideranca):
            if tk not in novos:
                del anos_lideranca[tk]
        for tk in novos:
            anos_lideranca[tk] = anos_lideranca.get(tk, 0) + 1
            liderancas_hist[tk].append(ano)

    if not anos_com_score:
        return None

    # Score para o próximo ano (dados até ano_atual - 1)
    score_proximo = _score_historico_ano(
        hist_batch, tickers, ano_atual, pesos, tk_grupos, lag=1,
        macro_by_year=macro_history or None,
    )
    if not score_proximo:
        return None
    ano_ref_score = ano_atual - 1

    # Líderes para próximo ano
    ranked_prox = sorted(score_proximo.items(), key=lambda x: x[1], reverse=True)
    scores_prox = [s for _, s in ranked_prox[:3]]
    from core.portfolio_constraints import minimum_assets_for_cap
    n_prox      = _select_n_heuristica(scores_prox) if len(ranked_prox) >= 2 else 1
    n_prox      = min(len(ranked_prox), max(n_prox, minimum_assets_for_cap(cap)))
    lids_prox   = [tk for tk, _ in ranked_prox[:n_prox] if tk in tickers]

    if lids_prox and len(lids_prox) >= 2:
        try:
            pesos_prox = _apply_cap_soft(
                _weights_from_scores(lids_prox, score_proximo, gamma), cap, soft
            )
        except ValueError as exc:
            pesos_prox = {tk: 1.0 / len(lids_prox) for tk in lids_prox}
            constraint_warnings.append(f"{ano_atual}: {exc}")
    elif lids_prox:
        pesos_prox = {lids_prox[0]: 1.0}
    else:
        pesos_prox = {}

    # Reconstrução histórica
    tks_disp = [tk for tk in tickers if tk in df_precos_all.columns]
    val_est = val_selic = val_ew = 0.0
    val_est_oos = val_selic_oos = val_ew_oos = 0.0
    contrib_est: dict[str, float] = {}
    if tks_disp and not df_precos_all.empty:
        df_prec_seg = df_precos_all[tks_disp].dropna(how="all")
        # Fix auditoria 2026-07: corta a série pelo ano_inicio. Sem o corte,
        # Selic e equal-weight recebiam aportes desde o início da janela de
        # preços (10 anos) enquanto a estratégia só começa em ano_inicio —
        # margens distorcidas e segmentos reprovados indevidamente.
        # Início em ABRIL do ano_inicio: mesma regra de publicação dos
        # balanços aplicada a TODAS as séries (janela justa).
        df_prec_seg = df_prec_seg[
            (df_prec_seg.index.year > int(ano_inicio)) |
            ((df_prec_seg.index.year == int(ano_inicio)) &
             (df_prec_seg.index.month >= _REBAL_MONTH))
        ]
        from core.transaction_costs import CostConfig
        _segment_costs = CostConfig.brasil_pf_default()
        val_est, val_selic, val_ew, contrib_est = _simular_seg_backtest(
            df_prec_seg, lids_por_ano, pesos_por_ano,
            aporte, taxa_selic_aa, selic_macro,
            dividendos=dividendos,
            cost_cfg=_segment_costs,
        )
        # Aprovação usa somente a janela final, não o mesmo histórico inteiro
        # usado para observar/desenvolver a estratégia. Início em abril e pelo
        # menos 18 meses úteis para evitar um holdout episódico.
        validation_start_year = int(df_prec_seg.index.max().year) - 2
        df_validation = df_prec_seg[
            (df_prec_seg.index.year > validation_start_year)
            | (
                (df_prec_seg.index.year == validation_start_year)
                & (df_prec_seg.index.month >= _REBAL_MONTH)
            )
        ]
        if len(df_validation) >= 18:
            val_est_oos, val_selic_oos, val_ew_oos, _ = _simular_seg_backtest(
                df_validation, lids_por_ano, pesos_por_ano,
                aporte, taxa_selic_aa, selic_macro,
                dividendos=dividendos,
                cost_cfg=_segment_costs,
            )

    total_lids = sum(len(v) for v in liderancas_hist.values())
    participacao = {
        tk: len(v) / total_lids
        for tk, v in liderancas_hist.items() if v
    } if total_lids > 0 else {}

    # Último ano que cada ticker liderou
    ultimo_lid: dict[str, int] = {
        tk: max(anos) for tk, anos in liderancas_hist.items() if anos
    }

    return {
        "setor": setor, "subsetor": subsetor, "segmento": segmento,
        "tickers": tickers,
        "liderancas_hist": liderancas_hist,
        "participacao": participacao,
        "ultimo_lid": ultimo_lid,
        "score_proximo": score_proximo,
        "ano_ref_score": ano_ref_score,
        "lids_prox": lids_prox,
        "pesos_prox": pesos_prox,
        "contrib_est": contrib_est,
        "ticker_maior_part": max(contrib_est, key=contrib_est.get) if contrib_est else None,
        "score_rows": score_rows,
        "lideres_rows": lideres_rows,
        "val_est": val_est,
        "val_selic": val_selic,
        "val_ew": val_ew,
        "val_est_oos": val_est_oos,
        "val_selic_oos": val_selic_oos,
        "val_ew_oos": val_ew_oos,
        "n_anos": len(anos_com_score),
        "ano_inicio": min(anos_com_score),
        "ano_fim": max(anos_com_score),
        "constraint_warnings": sorted(set(constraint_warnings)),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Componentes UI
# ══════════════════════════════════════════════════════════════════════════════

def _margem_pct(val: float, ref: float) -> float:
    # NaN/inf em val/ref (NaN não satisfaz ref<=0) propagaria p/ alpha_selic_medio
    if not (np.isfinite(val) and np.isfinite(ref)) or ref <= 0:
        return 0.0
    return (val / ref - 1) * 100


def _status_seg(val_est: float, val_selic: float, val_ew: float,
                thr_selic: float, thr_ew: float) -> str:
    m_selic = _margem_pct(val_est, val_selic)
    m_ew    = _margem_pct(val_est, val_ew)
    if m_selic >= thr_selic and (val_ew <= 0 or m_ew >= thr_ew):
        return "✅ Aprovado"
    if m_selic >= 0:
        return "⚠️ Abaixo da margem"
    return "❌ Falhou vs Selic"


def _bloco_segmento(res: dict, df_set: pd.DataFrame,
                    thr_selic: float, thr_ew: float,
                    max_anos_lid: int) -> None:
    """Renderiza um bloco de resultado por segmento."""
    setor, sub, seg = res["setor"], res["subsetor"], res["segmento"]
    setor_html, sub_html, seg_html = (
        html.escape(str(setor)), html.escape(str(sub)), html.escape(str(seg))
    )
    val_est, val_selic, val_ew = res["val_est"], res["val_selic"], res["val_ew"]
    m_selic = _margem_pct(val_est, val_selic)
    m_ew    = _margem_pct(val_est, val_ew)

    st.markdown(
        f'<div class="pb3-seg-hdr">{setor_html} › {sub_html} › {seg_html}</div>'
        f'<div class="pb3-seg-sub">'
        f'{res["n_anos"]} anos analisados · {res["ano_inicio"]}–{res["ano_fim"]}'
        f'</div>',
        unsafe_allow_html=True,
    )

    if val_est > 0:
        sub_val = f"Valor final da estratégia: **{_fv(val_est)}**"
        if val_selic > 0:
            sub_val += f" ({m_selic:+.1f}% vs Tesouro Selic)"
        if val_ew > 0:
            sub_val += f" · ({m_ew:+.1f}% vs Equal-Weight)"
        st.markdown(f'<div class="pb3-seg-val">{sub_val}</div>', unsafe_allow_html=True)

    # Cards das empresas que já lideraram
    participantes = sorted(
        res["participacao"].items(), key=lambda x: x[1], reverse=True
    )
    if not participantes:
        st.caption("Sem dados suficientes para reconstrução histórica.")
        return

    ano_atual = pd.Timestamp.now().year
    cols = st.columns(min(len(participantes), 4), gap="small")
    for j, (tk, part) in enumerate(participantes[:4]):
        anos_lid = res["liderancas_hist"].get(tk, [])
        nome_row = df_set[df_set["ticker"] == tk]
        nome = nome_row["nome_empresa"].iloc[0][:24] if not nome_row.empty else tk
        n_lids  = len(anos_lid)
        anos_str = ", ".join(str(a) for a in sorted(anos_lid))
        ultimo  = res["ultimo_lid"].get(tk, 0)
        anos_desde = ano_atual - ultimo if ultimo else 99
        cor_part = _COR_POS if anos_desde <= max_anos_lid else _COR_NEU
        with cols[j % 4]:
            st.markdown(
                f'<div class="pb3-emp-card">'
                f'<img src="{_logo_url(tk)}" style="width:40px;height:40px;'
                f'border-radius:8px;object-fit:contain;background:rgba(255,255,255,.06);'
                f'padding:4px;" onerror="this.style.display=\'none\'">'
                f'<div class="pb3-emp-ticker">{tk}</div>'
                f'<div class="pb3-emp-nome">{nome}</div>'
                f'<div class="pb3-emp-hist">'
                f'{"{}x Líder: {}".format(n_lids, anos_str) if anos_lid else "Sem lideranças"}'
                f'</div>'
                f'<div class="pb3-emp-part" style="color:{cor_part};">'
                f'{part*100:.1f}% participação'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown("<hr style='margin:16px 0;border-color:#1E2533;'>", unsafe_allow_html=True)


def _render_paineis_app1(
    resultados: list[dict],
    proximos_uniq: list[dict],
    df_precos_all: pd.DataFrame,
) -> None:
    """Paineis leves inspirados nos patches do app1, sem dependencias extras."""
    if not proximos_uniq:
        return

    score_global = pd.DataFrame([row for r in resultados for row in r.get("score_rows", [])])
    lideres_global = pd.DataFrame([row for r in resultados for row in r.get("lideres_rows", [])])
    selecionados = {p["tk"] for p in proximos_uniq}

    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>", unsafe_allow_html=True)
    st.caption("Teste incremental: painéis analíticos equivalentes aos patches do app1.")

    with st.expander("🧩 Patch 1 — Régua de Convicção", expanded=False):
        if score_global.empty:
            st.info("Régua de convicção indisponível para esta execução.")
        else:
            ultimo_ano = int(pd.to_numeric(score_global["Ano"], errors="coerce").max())
            df_ano = score_global[
                (score_global["Ano"] == ultimo_ano)
                & (score_global["ticker"].isin(selecionados))
            ].copy()
            if df_ano.empty:
                st.info("Nenhum ticker selecionado encontrado no score mais recente.")
            else:
                smin = float(df_ano["Score_Ajustado"].min())
                smax = float(df_ano["Score_Ajustado"].max())
                df_ano["Convicção"] = (
                    (df_ano["Score_Ajustado"] - smin) / ((smax - smin) + 1e-9) * 100
                )
                df_ano = df_ano.sort_values("Score_Ajustado", ascending=False)
                fig = px.bar(
                    df_ano, x="ticker", y="Convicção", color="Convicção",
                    color_continuous_scale=["#FC5C7D", "#F6C90E", "#00C896"],
                    hover_data=["SETOR", "SEGMENTO", "Score_Ajustado"],
                )
                fig.update_layout(**_plot_layout(320))
                fig.update_layout(coloraxis_showscale=False)
                st.plotly_chart(fig, use_container_width=True,
                                config={"displayModeBar": False}, key="pb3_patch1")

    with st.expander("🧩 Patch 2 — Dominância", expanded=False):
        if lideres_global.empty:
            st.info("Mapa de dominância indisponível para esta execução.")
        else:
            dom = (
                lideres_global[lideres_global["ticker"].isin(selecionados)]
                .groupby(["ticker", "SETOR", "SEGMENTO"], dropna=False)["Ano"]
                .agg(["count", lambda s: ", ".join(map(str, sorted(set(s.astype(int)))))]).reset_index()
            )
            dom.columns = ["Ticker", "Setor", "Segmento", "Anos como líder", "Anos"]
            dom = dom.sort_values("Anos como líder", ascending=False)
            st.dataframe(dom, use_container_width=True, hide_index=True)

    with st.expander("🧩 Patch 3 — Diversificação e Concentração", expanded=False):
        df_sel = pd.DataFrame(proximos_uniq)
        if df_sel.empty:
            st.info("Diversificação indisponível.")
        else:
            c1, c2 = st.columns(2)
            by_setor = df_sel["setor"].value_counts().reset_index()
            by_setor.columns = ["Setor", "Empresas"]
            fig_setor = px.bar(by_setor, x="Setor", y="Empresas", color="Setor")
            fig_setor.update_layout(**_plot_layout(320))
            fig_setor.update_layout(showlegend=False)
            c1.plotly_chart(fig_setor, use_container_width=True,
                            config={"displayModeBar": False}, key="pb3_patch3_setor")

            by_seg = df_sel["segmento"].value_counts().head(12).reset_index()
            by_seg.columns = ["Segmento", "Empresas"]
            fig_seg = px.bar(by_seg, x="Empresas", y="Segmento", orientation="h",
                             color="Empresas", color_continuous_scale="Teal")
            fig_seg.update_layout(**_plot_layout(320))
            fig_seg.update_layout(coloraxis_showscale=False)
            c2.plotly_chart(fig_seg, use_container_width=True,
                            config={"displayModeBar": False}, key="pb3_patch3_seg")

    with st.expander("🧩 Patch 4 — Benchmark do Segmento", expanded=False):
        rows = []
        for p in proximos_uniq:
            rows.append({
                "Ticker": p["tk"],
                "Setor": p.get("setor"),
                "Segmento": p.get("segmento"),
                "Rank score": p.get("rank_score"),
                "Alpha Selic (%)": round(float(p.get("alpha_selic", 0.0)), 1),
                "Alpha Equal-Weight (%)": round(float(p.get("alpha_ew", 0.0)), 1),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with st.expander("🧩 Patch 5 — Qualidade das Empresas", expanded=False):
        _render_patch5_qualidade(proximos_uniq, df_precos_all)


def _finite(v: object) -> bool:
    try:
        return v is not None and np.isfinite(float(v))
    except Exception:
        return False


def _fmt_pct(v: object, signed: bool = False) -> str:
    if not _finite(v):
        return "-"
    val = float(v) * 100.0
    return f"{val:+.2f}%" if signed else f"{val:.2f}%"


def _fmt_growth(v: object) -> str:
    if not _finite(v):
        return "-"
    return f"{float(v) * 100.0:+.2f}%"


def _fmt_ratio(v: object) -> str:
    if not _finite(v):
        return "-"
    return f"{float(v):.2f}x"


def _ratio_to_fraction(s: pd.Series, col: str) -> pd.Series:
    vals = pd.to_numeric(s, errors="coerce").dropna()
    if vals.empty:
        return vals
    if col.upper() in {"ROIC", "ROE", "ROA", "DY", "PAYOUT"}:
        med = float(vals.abs().median())
        if np.isfinite(med) and med > 1.0:
            vals = vals / 100.0
    return vals


def _valid_dy(v: object) -> bool:
    try:
        x = float(v)
        return np.isfinite(x) and 0 < x <= 0.50
    except Exception:
        return False


def _mean_last_years(df: pd.DataFrame, col: str, years: int = 5) -> float:
    if df is None or df.empty or col not in df.columns:
        return np.nan
    d = df.copy()
    if "Data" in d.columns:
        d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
        d = d.dropna(subset=["Data"]).sort_values("Data")
        d["Ano"] = d["Data"].dt.year
        vals = _ratio_to_fraction(d[col], col)
        d = d.loc[vals.index].copy()
        d[col] = vals
        by_year = d.groupby("Ano")[col].mean().dropna().tail(years)
        return float(by_year.mean()) if not by_year.empty else np.nan
    vals = _ratio_to_fraction(d[col], col).tail(years)
    return float(vals.mean()) if not vals.empty else np.nan


def _annual_series(df: pd.DataFrame, col: str) -> pd.Series:
    if df is None or df.empty or "Data" not in df.columns or col not in df.columns:
        return pd.Series(dtype=float)
    d = df[["Data", col]].copy()
    d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
    d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["Data", col])
    if d.empty:
        return pd.Series(dtype=float)
    d["Ano"] = d["Data"].dt.year
    return d.groupby("Ano")[col].sum().dropna().sort_index()


def _first_annual_series(df: pd.DataFrame, cols: tuple[str, ...]) -> pd.Series:
    for col in cols:
        s = _annual_series(df, col)
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def _growth_from_annual(s: pd.Series) -> float:
    vals = pd.to_numeric(s, errors="coerce").dropna()
    vals = vals[vals > 0].tail(6)
    if len(vals) < 3:
        return np.nan
    x = np.arange(len(vals), dtype=float)
    y = np.log(vals.astype(float).to_numpy())
    slope = float(np.polyfit(x, y, 1)[0])
    return float(np.exp(slope) - 1.0)


def _latest_debt_ratio(df: pd.DataFrame) -> tuple[float, str]:
    if df is None or df.empty or "Data" not in df.columns or "Divida_Liquida" not in df.columns:
        return np.nan, "Dívida Líq./EBITDA"
    denom = "EBITDA" if "EBITDA" in df.columns else ("EBIT" if "EBIT" in df.columns else "")
    if not denom:
        return np.nan, "Dívida Líq./EBITDA"
    d = df[["Data", "Divida_Liquida", denom]].copy()
    d["Data"] = pd.to_datetime(d["Data"], errors="coerce")
    d["Divida_Liquida"] = pd.to_numeric(d["Divida_Liquida"], errors="coerce")
    d[denom] = pd.to_numeric(d[denom], errors="coerce")
    d = d.dropna().sort_values("Data")
    if d.empty or float(d[denom].iloc[-1]) == 0:
        return np.nan, "Dívida Líq./EBITDA"
    label = "Dívida Líq./EBITDA" if denom == "EBITDA" else "Dívida Líq./EBIT"
    return float(d["Divida_Liquida"].iloc[-1] / d[denom].iloc[-1]), label


def _price_metrics(precos: pd.DataFrame, ticker: str) -> dict[str, float]:
    if precos is None or precos.empty or ticker not in precos.columns:
        return {"ret_12m": np.nan, "price_growth_5y": np.nan, "vol_12m": np.nan, "max_drop_5y": np.nan}
    s = pd.to_numeric(precos[ticker], errors="coerce").dropna()
    if s.empty:
        return {"ret_12m": np.nan, "price_growth_5y": np.nan, "vol_12m": np.nan, "max_drop_5y": np.nan}
    s.index = pd.to_datetime(s.index, errors="coerce")
    s = s[~s.index.isna()].sort_index()
    if len(s) < 2:
        return {"ret_12m": np.nan, "price_growth_5y": np.nan, "vol_12m": np.nan, "max_drop_5y": np.nan}

    ret_12m = np.nan
    janela_12 = s.tail(12)
    if len(janela_12) >= 2 and float(janela_12.iloc[0]) > 0:
        ret_12m = float(janela_12.iloc[-1] / janela_12.iloc[0] - 1.0)

    rets = s.pct_change().dropna().tail(12)
    vol_12m = float(rets.std() * np.sqrt(12)) if len(rets) >= 3 else np.nan

    janela_5 = s.tail(60)
    price_growth_5y = np.nan
    if len(janela_5) >= 24:
        vals = janela_5[janela_5 > 0]
        x = np.arange(len(vals), dtype=float)
        if len(vals) >= 24:
            slope = float(np.polyfit(x, np.log(vals.astype(float).to_numpy()), 1)[0])
            price_growth_5y = float(np.exp(slope * 12.0) - 1.0)

    max_drop_5y = np.nan
    if len(janela_5) >= 2:
        peak = janela_5.cummax()
        dd = janela_5 / peak - 1.0
        max_drop_5y = float(dd.min())

    return {
        "ret_12m": ret_12m,
        "price_growth_5y": price_growth_5y,
        "vol_12m": vol_12m,
        "max_drop_5y": max_drop_5y,
    }


def _quality_card(label: str, value: str, note: str, cls: str) -> str:
    return (
        f'<div class="cf-card {cls}">'
        f'<div class="cf-card-label">{label}</div>'
        f'<div class="cf-card-value">{value}</div>'
        f'<div class="cf-card-extra">{note}</div>'
        f'</div>'
    )


def _pos_class(v: object) -> str:
    if not _finite(v):
        return "cf-card-ratio"
    return "cf-card-good" if float(v) >= 0 else "cf-card-bad"


def _render_patch5_qualidade(proximos_uniq: list[dict], df_precos_all: pd.DataFrame) -> None:
    if not proximos_uniq:
        st.info("Sem líderes finais para exibir no Patch 5.")
        return

    st.markdown(
        """
        <style>
        .cf-header{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin:4px 0 22px;}
        .cf-title{font-size:2rem;font-weight:900;line-height:1.1;margin:0;color:#F8FAFC;}
        .cf-subtitle{font-size:.88rem;color:#C4CBD5;margin:10px 0 0;}
        .cf-pill{border:1px solid rgba(255,255,255,.14);background:rgba(255,255,255,.06);border-radius:999px;padding:8px 14px;color:#D6DCE6;font-size:.78rem;font-weight:700;white-space:nowrap;}
        .cf-rank{font-size:.86rem;color:#D6DCE6;margin:20px 0 2px;}
        .cf-name{font-size:1.35rem;font-weight:900;color:#F8FAFC;margin-bottom:10px;}
        .cf-card{border-radius:18px;padding:18px 20px;min-height:122px;margin-bottom:12px;border:1px solid rgba(255,255,255,.13);box-shadow:0 0 0 1px rgba(255,255,255,.04) inset;background:rgba(255,255,255,.045);}
        .cf-card-label{font-size:.70rem;letter-spacing:.12em;text-transform:uppercase;color:#D1D5DB;font-weight:800;margin-bottom:12px;}
        .cf-card-value{font-size:1.75rem;line-height:1;font-weight:900;color:#FFFFFF;margin-bottom:10px;}
        .cf-card-extra{font-size:.78rem;color:#D1D5DB;line-height:1.35;}
        .cf-card-income{background:rgba(37,99,235,.15);border-color:rgba(59,130,246,.35);}
        .cf-card-yield{background:rgba(180,113,18,.18);border-color:rgba(245,158,11,.35);}
        .cf-card-good{background:rgba(16,185,129,.15);border-color:rgba(34,197,94,.35);}
        .cf-card-bad{background:rgba(239,68,68,.13);border-color:rgba(248,113,113,.32);}
        .cf-card-ratio{background:rgba(148,163,184,.10);border-color:rgba(148,163,184,.24);}
        @media(max-width:700px){.cf-header{display:block}.cf-pill{display:inline-block;margin-top:12px}.cf-title{font-size:1.55rem}.cf-card-value{font-size:1.45rem}}
        </style>
        """,
        unsafe_allow_html=True,
    )

    tickers = list(dict.fromkeys([str(p.get("tk", "")).strip().upper() for p in proximos_uniq if p.get("tk")]))
    nomes = {str(p.get("tk", "")).strip().upper(): p.get("nome") or p.get("tk") for p in proximos_uniq}
    if not tickers:
        st.info("Patch 5 indisponível: tickers inválidos.")
        return

    with st.spinner("Carregando indicadores do Patch 5..."):
        mult_batch = _db.load_multiplos_historico_batch(tuple(tickers))
        dre_batch = _db.load_demonstracoes_batch(tuple(tickers))

    st.markdown(
        """
        <div class="cf-header">
          <div>
            <div class="cf-title">🏢 Patch 5 • Qualidade das Empresas</div>
            <div class="cf-subtitle">Indicadores a partir do <strong>Supabase</strong> (financeiros e múltiplos) com fallback para <strong>yfinance</strong> (preços).</div>
          </div>
          <div><span class="cf-pill">Janela: 5 anos (quando houver)</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    rows: list[dict] = []
    for tk in tickers:
        df_mult = mult_batch.get(tk, pd.DataFrame())
        df_fin = dre_batch.get(tk, pd.DataFrame())
        pm = _price_metrics(df_precos_all, tk)

        roic = _mean_last_years(df_mult, "ROIC")
        rec_growth = _growth_from_annual(_annual_series(df_fin, "Receita_Liquida"))
        lucro_growth = _growth_from_annual(_annual_series(df_fin, "Lucro_Liquido"))
        div_growth = _growth_from_annual(_first_annual_series(
            df_fin,
            (
                "Dividendos", "Dividendos_Pagos", "Dividendos_e_JCP",
                "JCP", "Proventos", "Dividendos_Distribuidos",
            ),
        ))
        debt_ratio, debt_label = _latest_debt_ratio(df_fin)

        # ── DY: prioridade 5 anos; fallback 12 meses ─────────────────────
        dy = np.nan
        dy_label = "DY médio (5a)"
        fonte_dividendos = "—"
        tk_up = tk.strip().upper().replace(".SA", "")

        # 1. Média 5 anos — banco de dados
        dy_db = _mean_last_years(df_mult, "DY")
        if _valid_dy(dy_db):
            dy, fonte_dividendos, dy_label = float(dy_db), "DB (média 5a)", "DY médio (5a)"

        # 2. Média até 5 anos calculada via yfinance
        #    dividendos anuais (yf) / preço de final de ano (matriz de preços)
        if not _valid_dy(dy):
            df_divs_yf = _yf_dividendos_anuais(tk)
            if not df_divs_yf.empty and not df_precos_all.empty and tk_up in df_precos_all.columns:
                dy_anos: list[float] = []
                for _, dr in df_divs_yf.tail(5).iterrows():
                    try:
                        ano_div  = int(pd.Timestamp(dr["Data"]).year)
                        div_anual = float(dr["Dividendos"])
                    except Exception:
                        continue
                    if div_anual <= 0 or div_anual > _DIV_POR_ACAO_MAX * 12:
                        continue
                    mask = df_precos_all.index.year == ano_div
                    px_s = df_precos_all.loc[mask, tk_up].dropna()
                    if px_s.empty:
                        continue
                    px = float(px_s.iloc[-1])
                    if px > 0:
                        dy_a = div_anual / px
                        if 0 < dy_a <= 1.0:
                            dy_anos.append(dy_a)
                if dy_anos:
                    n = len(dy_anos)
                    dy = float(np.mean(dy_anos))
                    fonte_dividendos = f"YF (média {n}a)"
                    dy_label = f"DY médio ({n}a)"

        # 3. yfinance .info (trailing 12m — menos preciso)
        if not _valid_dy(dy):
            dy_yf = _yf_multiplos_dividendos(tk).get("DY")
            if _valid_dy(dy_yf):
                dy, fonte_dividendos, dy_label = float(dy_yf), "YF .info", "DY trailing (12m)"

        # 4. Trailing 12m calculado: soma dividendos / último preço
        if not _valid_dy(dy):
            trail12 = _yf_trailing12m_divs(tk)
            if trail12 > 0:
                px_last = np.nan
                s_px = df_precos_all[tk_up].dropna() if (not df_precos_all.empty and tk_up in df_precos_all.columns) else pd.Series(dtype=float)
                if not s_px.empty:
                    px_last = float(s_px.iloc[-1])
                if np.isfinite(px_last) and px_last > 0:
                    dy_calc = trail12 / px_last
                    if _valid_dy(dy_calc):
                        dy, fonte_dividendos, dy_label = dy_calc, "YF (calculado)", "DY trailing (12m)"

        # 5. Último valor pontual do banco (sem média)
        if not _valid_dy(dy) and df_mult is not None and not df_mult.empty and "DY" in df_mult.columns:
            vals_dy = _ratio_to_fraction(pd.to_numeric(df_mult["DY"], errors="coerce").dropna(), "DY")
            if not vals_dy.empty:
                last_dy = float(vals_dy.iloc[-1])
                if _valid_dy(last_dy):
                    dy, fonte_dividendos, dy_label = last_dy, "DB (último)", "DY (último disp.)"

        if not _finite(div_growth):
            df_div_yf = _yf_dividendos_anuais(tk)
            if not df_div_yf.empty:
                div_growth_yf = _growth_from_annual(_annual_series(df_div_yf, "Dividendos"))
                if _finite(div_growth_yf):
                    div_growth = div_growth_yf
                    if fonte_dividendos == "—":
                        fonte_dividendos = "YF"

        rows.append({
            "ticker": tk,
            "nome": nomes.get(tk, tk),
            "roic": roic,
            "dy": dy,
            "dy_label": dy_label,
            "div_growth": div_growth,
            "fonte_dividendos": fonte_dividendos,
            "debt_ratio": debt_ratio,
            "debt_label": debt_label,
            "rec_growth": rec_growth,
            "lucro_growth": lucro_growth,
            **pm,
        })

    dfm = pd.DataFrame(rows)
    if dfm.empty:
        st.info("Sem dados suficientes para exibir o Patch 5.")
        return

    def _pct_rank(col: str, invert: bool = False) -> pd.Series:
        pct = pd.to_numeric(dfm[col], errors="coerce").rank(pct=True)
        return 1.0 - pct if invert else pct

    dfm["_ord"] = (
        _pct_rank("roic") * .18
        + _pct_rank("dy") * .14
        + _pct_rank("div_growth") * .10
        + _pct_rank("rec_growth") * .12
        + _pct_rank("lucro_growth") * .12
        + _pct_rank("ret_12m") * .10
        + _pct_rank("price_growth_5y") * .08
        + _pct_rank("debt_ratio", invert=True) * .10
        + _pct_rank("vol_12m", invert=True) * .08
        + _pct_rank("max_drop_5y") * .08
    ).fillna(-1.0)
    dfm = dfm.sort_values(["_ord", "ticker"], ascending=[False, True]).reset_index(drop=True)

    for idx, row in dfm.iterrows():
        rank = idx + 1
        medal = "🥇" if rank == 1 else ("🥈" if rank == 2 else ("🥉" if rank == 3 else "🏅"))
        st.markdown(
            f'<div class="cf-rank">{medal} <strong>#{rank}</strong></div>'
            f'<div class="cf-name">{row["ticker"]} — {row["nome"]}</div>',
            unsafe_allow_html=True,
        )

        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(_quality_card("ROIC médio (5a)", _fmt_pct(row["roic"]), "Eficiência do capital (média 5 anos).", "cf-card-income"), unsafe_allow_html=True)
        fonte_div = str(row.get("fonte_dividendos") or "—")
        dy_lbl    = str(row.get("dy_label") or "DY médio (5a)")
        c2.markdown(_quality_card(dy_lbl, _fmt_pct(row["dy"]), f"Dividend Yield. Fonte: {fonte_div}.", "cf-card-yield"), unsafe_allow_html=True)
        c3.markdown(_quality_card("Cresc. anual dividendos (5a)", _fmt_growth(row["div_growth"]), f"Tendência robusta dos dividendos. Fonte: {fonte_div}.", _pos_class(row["div_growth"])), unsafe_allow_html=True)
        c4.markdown(_quality_card(str(row["debt_label"]), _fmt_ratio(row["debt_ratio"]), "Último disponível no Supabase.", "cf-card-ratio"), unsafe_allow_html=True)

        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(_quality_card("Cresc. anual receita (5a)", _fmt_growth(row["rec_growth"]), "Taxa anualizada implícita da tendência robusta da receita.", _pos_class(row["rec_growth"])), unsafe_allow_html=True)
        c2.markdown(_quality_card("Cresc. anual lucro (5a)", _fmt_growth(row["lucro_growth"]), "Taxa anualizada implícita da tendência robusta do lucro.", _pos_class(row["lucro_growth"])), unsafe_allow_html=True)
        c3.markdown(_quality_card("Valorização do preço (12m)", _fmt_pct(row["ret_12m"], signed=True), "Retorno do preço em ~12 meses.", _pos_class(row["ret_12m"])), unsafe_allow_html=True)
        c4.markdown(_quality_card("Cresc. anual preço (5a)", _fmt_growth(row["price_growth_5y"]), "Taxa anualizada implícita da tendência robusta do preço.", _pos_class(row["price_growth_5y"])), unsafe_allow_html=True)

        c1, c2, c3 = st.columns(3)
        c1.markdown(_quality_card("Volatilidade (12m, a.a.)", _fmt_pct(row["vol_12m"]), "Desvio padrão anualizado do preço.", "cf-card-ratio"), unsafe_allow_html=True)
        c2.markdown(_quality_card("Máxima queda (5a)", _fmt_pct(row["max_drop_5y"], signed=True), "Pior queda do preço no período.", "cf-card-ratio"), unsafe_allow_html=True)
        c3.markdown(_quality_card("Fonte", "DB + YF", "Supabase (primário) + yfinance (fallback).", "cf-card-ratio"), unsafe_allow_html=True)

        st.markdown("<hr style='border:0;border-top:1px solid rgba(255,255,255,.08);margin: 8px 0 18px;'>", unsafe_allow_html=True)

    st.caption(
        "Notas: crescimento usa inclinação log-linear anualizada quando há dados suficientes. "
        "Preço, volatilidade e máxima queda usam a matriz de preços baixada para a criação do portfólio."
    )


# ══════════════════════════════════════════════════════════════════════════════
# RENDER PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

def render(show_header: bool = True) -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    if show_header:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;margin-bottom:4px;">'
            '<span style="font-size:2rem">🚀</span>'
            '<h1 style="font-size:2rem;font-weight:800;color:#E2E8F0;margin:0;">'
            'Criação de Portfólio B3</h1>'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown(
        '<p style="font-size:0.80rem;color:#9CA3AF;margin-bottom:20px;">'
        '<strong style="color:#CBD5E1;">Etapa 2 de 3 · Aplicação em escala.</strong> '
        'Roda a metodologia validada na aba <strong>Análise Avançada</strong> em '
        '<strong>todos os segmentos</strong> da B3 de uma vez, identifica as empresas '
        'vencedoras de cada segmento e consolida uma carteira inicial multissetorial. '
        'Usa scoring com publication lag = 1, reconstrução histórica e comparação vs '
        'Tesouro Selic e Equal-Weight do próprio segmento. A carteira salva aqui segue '
        'para a aba <strong>Avaliação de Portfólio</strong>, que a julga como conjunto.'
        '</p>',
        unsafe_allow_html=True,
    )

    # ── PARÂMETROS ────────────────────────────────────────────────────────────
    with st.expander("⚙️ Parâmetros", expanded=True):
        p1, p2, p3, p4 = st.columns(4)
        thr_selic   = p1.number_input(
            "Margem mín. vs Tesouro Selic (%)", 0.0, 500.0, 10.0, 5.0,
            key="pb3_thr_selic",
        )
        thr_ew      = p2.number_input(
            "Margem mín. vs Equal-Weight (%)", 0.0, 300.0, 0.0, 5.0,
            key="pb3_thr_ew",
        )
        uso_ew      = p3.selectbox(
            "Uso do Equal-Weight na seleção",
            ["Apenas diagnóstico", "Critério de seleção"],
            key="pb3_uso_ew",
        )
        max_anos_lid = p4.number_input(
            "Máx. anos desde última liderança", 1, 20, 5, 1,
            key="pb3_max_anos",
        )
        pa1, pa2, pa3, pa4 = st.columns(4)
        aporte       = pa1.number_input("Aporte mensal (R$)", 100.0, 50000.0, 1000.0, 100.0,
                                         key="pb3_aporte")
        ano_inicio   = pa2.number_input("Ano início da reconstrução", 2010, 2022, _ANO_INICIO_DEFAULT, 1,
                                         key="pb3_ano_ini")
        mostrar_audit = pa3.checkbox("Mostrar auditoria dos segmentos", value=True,
                                      key="pb3_audit")
        min_anos_dre = pa4.number_input("Histórico DRE mínimo", 4, 20, 10, 1,
                                        key="pb3_min_anos_dre")
        usar_status_recon = st.checkbox(
            "Cruzar Status Invest no saneamento atual",
            value=False,
            key="pb3_status_recon",
        )

    usar_ew_como_criterio = uso_ew == "Critério de seleção"

    rodar = st.button("🚀 Rodar Criação de Portfólio", type="primary", key="pb3_rodar")

    # ── DADOS BASE ────────────────────────────────────────────────────────────
    with st.spinner("Carregando dados do banco…"):
        df_set        = _so_acoes(_db.load_setores())
        selic_macro   = _db.load_selic_macro()
        macro_history = _db.load_macro_history()
        anos_hist     = _db.load_historico_anos()
        df_mult_todos = _db.load_multiplos_todos()

    if df_set.empty:
        st.warning("Banco não configurado. Configure `SUPABASE_DB_URL_B3`.")
        return

    taxa_selic_aa = (
        float(np.mean(list(selic_macro.values()))) if selic_macro else 0.1075
    )

    if not rodar and "pb3_resultados" not in st.session_state:
        st.info(
            "Configure os parâmetros acima e clique **🚀 Rodar Criação de Portfólio**.",
            icon="ℹ️",
        )
        return

    if rodar:
        all_tickers = tuple(sorted(df_set["ticker"].unique()))

        with st.spinner("Carregando histórico de múltiplos de todos os tickers…"):
            hist_batch_raw = _db.load_multiplos_historico_batch(all_tickers)

        with st.spinner("Saneando fundamentos com ranges, outliers e fallback web..."):
            # Política única de fontes (fix auditoria 2026-07): com market.*
            # ativo a fonte é limpa — web (Fundamentus/Status Invest) NÃO
            # sobrescreve o banco; passar fund_data={} faz a reconciliação
            # virar apenas saneamento por ranges (nulo fica nulo), alinhado
            # à regra da Análise Avançada ("vazio fica vazio").
            _mkt_ativo = False
            try:
                _mkt_ativo = bool(_db.market_active())
            except Exception:
                pass
            df_mult_recon, audit_recon, quality_summary = _recon.batch_multiplos_reconciliados(
                all_tickers,
                df_base=df_mult_todos,
                include_status=bool(usar_status_recon) and not _mkt_ativo,
                fund_data={} if _mkt_ativo else None,
            )
            zero_invalid = set(quality_summary.get("campos_zero_suspeito", []))
            hist_clean, hist_audit = _recon.clean_multiplos_history_batch(
                hist_batch_raw,
                zero_invalid_fields=zero_invalid,
            )
            # Fix auditoria 2026-07: o overlay do snapshot ATUAL sobre a
            # última linha histórica vale só para o ENTRY GUARD (decisão de
            # hoje). O backtest usa o histórico limpo SEM overlay — antes,
            # tickers com histórico desatualizado recebiam dados de hoje em
            # anos alcançáveis pelos cutoffs (look-ahead).
            hist_batch_guard = _overlay_current_reconciled_row(hist_clean, df_mult_recon)
            hist_batch = hist_clean
            entry_guard, df_entry_guard = _build_entry_guard(
                df_mult_recon, df_set, hist_batch_guard, anos_hist
            )

        with st.spinner("Carregando preços mensais ajustados…"):
            df_precos_all = _batch_yf_precos_mensais(all_tickers, period="10y")

        # Gamma/cap/soft calibrados (se existirem) ou defaults
        gamma = st.session_state.get("b3_av_gamma", _GAMMA_DEF)
        cap   = st.session_state.get("b3_av_cap",   _CAP_DEF)
        soft  = st.session_state.get("b3_av_soft",  _SOFT_DEF)

        resultados: list[dict] = []
        grupos = list(df_set.groupby(["SETOR", "SUBSETOR", "SEGMENTO"]))
        prog = st.progress(0, text="Processando segmentos…")

        for i, ((setor, subsetor, segmento), grupo) in enumerate(grupos):
            tickers_seg = [
                tk for tk in grupo["ticker"].tolist()
                if tk in hist_batch and (not anos_hist or anos_hist.get(tk, 0) >= int(min_anos_dre))
            ]
            if tickers_seg:
                # adjusted_close já é retorno total → dividendos=None (sem dupla contagem)
                res = _processar_segmento(
                    tickers_seg, hist_batch, df_precos_all,
                    str(setor), str(subsetor), str(segmento),
                    taxa_selic_aa, selic_macro, macro_history, float(aporte),
                    int(ano_inicio), gamma, cap, soft,
                    dividendos=None,
                )
                if res:
                    resultados.append(res)
            prog.progress((i + 1) / max(len(grupos), 1),
                          text=f"Segmento {i+1}/{len(grupos)}: {segmento}")

        prog.empty()
        st.session_state["pb3_resultados"] = resultados
        st.session_state["pb3_df_set"]     = df_set
        st.session_state["pb3_precos_all"] = df_precos_all
        st.session_state["pb3_quality_summary"] = quality_summary
        st.session_state["pb3_quality_audit"]   = audit_recon
        st.session_state["pb3_hist_audit"]      = hist_audit
        st.session_state["pb3_entry_guard"]     = entry_guard
        st.session_state["pb3_entry_guard_df"]  = df_entry_guard

    resultados = st.session_state.get("pb3_resultados", [])
    df_set     = st.session_state.get("pb3_df_set", df_set)
    df_precos_all = st.session_state.get("pb3_precos_all", pd.DataFrame())
    quality_summary = st.session_state.get("pb3_quality_summary", {})
    quality_audit = st.session_state.get("pb3_quality_audit", pd.DataFrame())
    hist_audit = st.session_state.get("pb3_hist_audit", pd.DataFrame())
    entry_guard = st.session_state.get("pb3_entry_guard", {})

    if not resultados:
        st.warning("Nenhum segmento retornou dados suficientes.")
        return

    _render_data_quality_box(quality_summary, quality_audit, hist_audit)

    # ── Saneamento persistente (cruza Fundamentus/Status Invest e grava no banco) ──
    try:
        from views.data_quality_panel import render_healing_panel
        _heal_tks = sorted({
            str(t).upper().replace(".SA", "")
            for r in resultados for t in (r.get("tickers") or [])
        })
        if _heal_tks:
            with st.expander("🩺 Sanear dados (Fundamentus + Status Invest → banco)",
                             expanded=False):
                render_healing_panel(_heal_tks, key_prefix="pb3_heal")
    except Exception as _exc_heal:  # nunca quebra a aba
        st.caption(f"Saneamento de dados indisponível: {_exc_heal}")

    # ── FILTROS DE APROVAÇÃO ──────────────────────────────────────────────────
    ano_atual = pd.Timestamp.now().year

    def _aprovado(res: dict) -> bool:
        if res.get("val_est_oos", 0.0) <= 0:
            return False
        m_selic = _margem_pct(res["val_est_oos"], res["val_selic_oos"])
        m_ew    = _margem_pct(res["val_est_oos"], res["val_ew_oos"])
        ultimo_lid_seg = max(res["ultimo_lid"].values()) if res["ultimo_lid"] else 0
        recente = (ano_atual - 1 - ultimo_lid_seg) <= max_anos_lid
        if not recente:
            return False
        if m_selic < thr_selic:
            return False
        if usar_ew_como_criterio and res["val_ew_oos"] > 0 and m_ew < thr_ew:
            return False
        return True

    aprovados  = [r for r in resultados if _aprovado(r)]
    reprovados = [r for r in resultados if not _aprovado(r)]

    # ── TABELA DE AUDITORIA ───────────────────────────────────────────────────
    _sec_hdr(f"📋 Auditoria de Segmentos — {len(resultados)} analisados · "
             f"{len(aprovados)} aprovados")
    st.caption(
        "A aprovação usa exclusivamente o holdout final de aproximadamente "
        "24 meses, com custos de compra. Segmentos sem ao menos 18 meses de "
        "validação ficam reprovados por dados insuficientes."
    )

    rows_tbl: list[dict] = []
    for res in resultados:
        m_s  = _margem_pct(res["val_est_oos"], res["val_selic_oos"])
        m_ew = _margem_pct(res["val_est_oos"], res["val_ew_oos"])
        ult  = max(res["ultimo_lid"].values()) if res["ultimo_lid"] else 0
        rows_tbl.append({
            "Setor":     res["setor"],
            "Subsetor":  res["subsetor"],
            "Segmento":  res["segmento"],
            "Status":    _status_seg(res["val_est_oos"], res["val_selic_oos"], res["val_ew_oos"],
                                     thr_selic, thr_ew),
            "vs Selic OOS (%)": round(m_s, 1),
            "vs EW OOS (%)":    round(m_ew, 1),
            "Patrimônio total": _fv(res["val_est"]),
            "Últ. liderança": ult or "—",
        })

    df_tbl = pd.DataFrame(rows_tbl)

    def _cor_status(v: str) -> str:
        if "✅" in v:    return "color: #00C896"
        if "⚠️" in v:   return "color: #F6C90E"
        return "color: #FC5C7D"

    st.dataframe(
        df_tbl.style.applymap(_cor_status, subset=["Status"]),
        use_container_width=True,
        height=min(500, 60 + 35 * len(df_tbl)),
    )

    # ── SEGMENTOS APROVADOS ───────────────────────────────────────────────────
    if aprovados and mostrar_audit:
        st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                    unsafe_allow_html=True)
        _sec_hdr(f"✅ Segmentos Aprovados ({len(aprovados)})")
        for res in sorted(aprovados,
                          key=lambda r: _margem_pct(r["val_est"], r["val_selic"]),
                          reverse=True):
            _bloco_segmento(res, df_set, thr_selic, thr_ew, max_anos_lid)

    # ── EMPRESAS LÍDERES PARA O PRÓXIMO ANO ──────────────────────────────────
    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    _sec_hdr(f"📋 Empresas líderes para o próximo ano ({ano_atual})")
    st.caption(f"Apenas segmentos aprovados · Para compra em {ano_atual}")

    proximos: list[dict] = []
    for res in aprovados:
        score_prox = res["score_proximo"]
        pesos_p    = res["pesos_prox"]
        part_hist  = res["participacao"]
        ano_ref    = int(res.get("ano_ref_score", ano_atual - 1))
        ranked_prox = sorted(score_prox.items(), key=lambda x: x[1], reverse=True)
        if not ranked_prox:
            continue

        ticker_lider = ranked_prox[0][0]
        ticker_maior = res.get("ticker_maior_part")
        selecionados = [ticker_lider]
        maior_info: tuple[bool, str, int | None, int | None] | None = None

        if ticker_maior and ticker_maior != ticker_lider:
            maior_info = _pode_incluir_maior_participacao(
                str(ticker_maior), res.get("ultimo_lid", {}), score_prox,
                ano_ref, int(max_anos_lid),
            )
            if maior_info[0]:
                selecionados.append(str(ticker_maior))

        for tk in selecionados:
            score = score_prox.get(tk, 0.0)
            peso  = pesos_p.get(tk, 0.0)
            entry = entry_guard.get(str(tk).upper().replace(".SA", ""), {}) if isinstance(entry_guard, dict) else {}
            has_entry = bool(entry)
            status_entrada = str(entry.get("status_entrada", ""))
            score_entrada = float(entry.get("score_entrada", 0.0) or 0.0)
            if float(peso or 0.0) <= 0.0:
                continue
            status_norm = status_entrada.lower()
            if has_entry and (status_norm.startswith("exclu") or score_entrada < 30.0):
                continue
            motivos = []
            if has_entry and status_norm.startswith("observ"):
                motivos.append(f"Score Entrada em observacao ({score_entrada:.1f})")
            if tk == ticker_lider:
                motivos.append(f"Líder no Score ({ano_ref})")
            maior_part = max(part_hist, key=part_hist.get) if part_hist else None
            if ticker_maior == tk:
                if maior_info and maior_info[1]:
                    motivos.append(maior_info[1])
                else:
                    motivos.append("Maior participação no segmento")
            nome_row = df_set[df_set["ticker"] == tk]
            nome = nome_row["nome_empresa"].iloc[0][:24] if not nome_row.empty else tk
            proximos.append({
                "tk": tk, "nome": nome, "score": score, "peso": peso,
                "score_entrada": score_entrada,
                "status_entrada": status_entrada,
                "motivos": motivos,
                "setor": res["setor"],
                "subsetor": res["subsetor"],
                "segmento": res["segmento"],
                "alpha_selic": _margem_pct(res["val_est_oos"], res["val_selic_oos"]),
                "alpha_ew": _margem_pct(res["val_est_oos"], res["val_ew_oos"]),
                "rank_score": _rank_ticker(score_prox, tk),
                "ano_lider": ano_ref if tk == ticker_lider else res.get("ultimo_lid", {}).get(tk),
            })

    # Orçamento explícito: cada segmento aprovado recebe a mesma fração da
    # carteira; dentro dele, os líderes selecionados dividem o orçamento pelos
    # pesos do score. Pesos internos de segmentos distintos não são comparáveis.
    _seg_groups: dict[tuple[str, str, str], list[dict]] = {}
    for item in proximos:
        key = (item["setor"], item["subsetor"], item["segmento"])
        _seg_groups.setdefault(key, []).append(item)
    _seg_budget = 1.0 / len(_seg_groups) if _seg_groups else 0.0
    for group_items in _seg_groups.values():
        local_total = sum(float(item.get("peso") or 0.0) for item in group_items)
        for item in group_items:
            item["peso"] = (
                _seg_budget * float(item.get("peso") or 0.0) / local_total
                if local_total > 0 else _seg_budget / len(group_items)
            )

    # Remove duplicatas (mesmo ticker em múltiplos segmentos), somando os
    # orçamentos atribuídos e preservando a melhor ficha/motivos.
    mapa_prox: dict[str, dict] = {}
    for p in sorted(proximos, key=lambda x: x["score"], reverse=True):
        tk = p["tk"]
        if tk not in mapa_prox:
            mapa_prox[tk] = p
            continue
        cur = mapa_prox[tk]
        combined_weight = float(cur.get("peso") or 0.0) + float(p.get("peso") or 0.0)
        combined_motivos = list(dict.fromkeys(
            (cur.get("motivos") or []) + (p.get("motivos") or [])
        ))
        if float(p.get("score", 0.0)) > float(cur.get("score", 0.0)):
            cur.update({k: v for k, v in p.items() if k != "motivos"})
        cur["peso"] = combined_weight
        cur["motivos"] = combined_motivos
    proximos_uniq = sorted(mapa_prox.values(), key=lambda x: x["score"], reverse=True)

    if proximos_uniq:
        for i in range(0, len(proximos_uniq), 3):
            cols_p = st.columns(3, gap="small")
            for j, item in enumerate(proximos_uniq[i:i+3]):
                mot_html = "".join(
                    f'<div class="pb3-lider-motivo">• {html.escape(str(m))}</div>'
                    for m in item["motivos"]
                ) or '<div class="pb3-lider-motivo">Líder do segmento</div>'
                _tk_html = html.escape(str(item["tk"]))
                _nome_html = html.escape(str(item["nome"]))
                _logo_html = html.escape(str(_logo_url(item["tk"])), quote=True)
                with cols_p[j]:
                    st.markdown(
                        f'<div class="pb3-lider-card">'
                        f'<img src="{_logo_html}" style="width:48px;height:48px;'
                        f'border-radius:10px;object-fit:contain;background:rgba(255,255,255,.06);'
                        f'padding:5px;" onerror="this.style.display=\'none\'">'
                        f'<div class="pb3-lider-ticker">({_tk_html})</div>'
                        f'<div class="pb3-emp-nome">{_nome_html}</div>'
                        f'{mot_html}'
                        f'<div class="pb3-lider-ano">Para compra em {ano_atual}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
    else:
        st.info("Nenhum líder identificado com os parâmetros atuais.")

    if proximos_uniq:
        st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                    unsafe_allow_html=True)
        _sec_hdr("💾 Salvar como portfólio padrão")
        st.caption(
            "Ao salvar, esta carteira passa a ser o modelo B3 ativo do usuário "
            "e aparece resumida no Dashboard Geral. Alocação: orçamento igual "
            "entre segmentos aprovados; dentro de cada segmento, pesos do score."
        )
        params_modelo = {
            "score_version": SCORE_VERSION,
            "model_schema_version": MODEL_SCHEMA_VERSION,
            "allocation_policy": "equal_approved_segment_budget",
            "ano_compra": ano_atual,
            "thr_selic": float(thr_selic),
            "thr_ew": float(thr_ew),
            "uso_equal_weight": uso_ew,
            "max_anos_lideranca": int(max_anos_lid),
            "aporte_mensal_simulado": float(aporte),
            "ano_inicio_backtest": int(ano_inicio),
            "min_anos_dre": int(min_anos_dre),
            "segmentos_analisados": len(resultados),
            "segmentos_aprovados": len(aprovados),
            "fundamentos_corrigidos_web": int(quality_summary.get("correcoes_web", 0) or 0),
            "fundamentos_invalidos": int(quality_summary.get("celulas_invalidas", 0) or 0),
            "status_invest_reconciliacao": bool(quality_summary.get("usa_status_invest", False)),
        }
        metrics_modelo = {
            "num_empresas": len(proximos_uniq),
            "alpha_selic_medio": float(np.mean([p.get("alpha_selic", 0.0) for p in proximos_uniq])),
            "alpha_ew_medio": float(np.mean([p.get("alpha_ew", 0.0) for p in proximos_uniq])),
            "score_medio": float(np.mean([p.get("score", 0.0) for p in proximos_uniq])),
            "score_entrada_medio": float(np.mean([p.get("score_entrada", 0.0) for p in proximos_uniq])),
        }
        c_save, c_info = st.columns([1, 3], gap="medium")
        with c_save:
            if st.button("Salvar portfólio padrão", type="primary", key="pb3_save_model"):
                try:
                    model_id = save_b3_portfolio_model(
                        proximos_uniq,
                        params=params_modelo,
                        metrics=metrics_modelo,
                        name=f"Portfolio B3 Modelo {ano_atual}",
                    )
                    st.success(f"Portfólio padrão salvo no banco. ID: {model_id[:8]}")
                except Exception as exc:
                    st.error(f"Não foi possível salvar o portfólio padrão: {exc}")
        with c_info:
            st.info(
                f"{len(proximos_uniq)} empresas selecionadas · "
                f"{len(aprovados)} segmentos aprovados · "
                f"score médio {metrics_modelo['score_medio']:.2f}"
            )

    # ── DISTRIBUIÇÃO SETORIAL ────────────────────────────────────────────────
    if proximos_uniq:
        st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                    unsafe_allow_html=True)
        _sec_hdr("🍕 Distribuição Setorial do Portfólio Sugerido")

        dist: dict[str, float] = {}
        for p in proximos_uniq:
            dist[p["setor"]] = dist.get(p["setor"], 0.0) + float(p.get("peso") or 0.0)
        df_dist = pd.DataFrame(
            {"Setor": list(dist.keys()), "Peso": list(dist.values())}
        )
        fig_pie = px.pie(
            df_dist, names="Setor", values="Peso",
            color_discrete_sequence=px.colors.qualitative.Set3,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label",
                              textfont_size=11)
        fig_pie.update_layout(**_plot_layout(420))
        fig_pie.update_layout(showlegend=False)
        st.plotly_chart(fig_pie, use_container_width=True,
                        config={"displayModeBar": False}, key="pb3_pie")

    _render_paineis_app1(resultados, proximos_uniq, df_precos_all)

    # ── DESEMPENHO PARCIAL ANO ATUAL ─────────────────────────────────────────
    if proximos_uniq and "pb3_precos_all" not in st.session_state:
        st.session_state["pb3_precos_all"] = st.session_state.get(
            "pb3_precos_all", pd.DataFrame()
        )

    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    _sec_hdr(f"📈 Desempenho parcial das selecionadas (ano atual: {ano_atual})")
    st.caption("Acompanhamento de aportes mensais de R$1.000 desde janeiro do ano atual.")

    if proximos_uniq:
        tks_prox = tuple(sorted({p["tk"] for p in proximos_uniq}))
        df_prec_prox = _batch_yf_precos_mensais(tks_prox, period="1y")

        if not df_prec_prox.empty:
            data_ini_ano = pd.Timestamp(ano_atual, 1, 1)
            df_ano = df_prec_prox[df_prec_prox.index >= data_ini_ano].copy()
            if not df_ano.empty:
                aporte_sim  = 1000.0
                taxa_m_sim  = (1 + taxa_selic_aa) ** (1 / 12) - 1
                # Portfolio sugerido: igual weight entre os proximos_uniq
                cotas_pf: dict[str, float]   = {tk: 0.0 for tk in tks_prox if tk in df_ano.columns}
                cotas_selic: float            = 0.0
                rows_perf: list[dict]         = []

                for dt, row in df_ano.iterrows():
                    cotas_selic = cotas_selic * (1 + taxa_m_sim) + aporte_sim
                    disp = [tk for tk in cotas_pf
                            if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
                    if disp:
                        for tk in disp:
                            cotas_pf[tk] += aporte_sim / len(disp) / float(row[tk])
                    val_pf = sum(
                        cotas_pf[tk] * float(row[tk])
                        for tk in cotas_pf
                        if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0
                    )
                    rows_perf.append({"Data": dt,
                                      "Estratégia de Aporte": val_pf,
                                      "Tesouro Selic": cotas_selic})

                df_perf = pd.DataFrame(rows_perf)
                if not df_perf.empty:
                    st.markdown(
                        f'<div style="font-weight:700;font-size:0.9rem;'
                        f'color:#E2E8F0;margin-bottom:8px;">'
                        f'Comparativo de desempenho parcial em {ano_atual}</div>',
                        unsafe_allow_html=True,
                    )
                    melt_p = df_perf.melt("Data", var_name="Carteira",
                                          value_name="Valor acumulado (R$)")
                    fig_perf = px.line(
                        melt_p, x="Data", y="Valor acumulado (R$)", color="Carteira",
                        color_discrete_map={
                            "Estratégia de Aporte": _COR_INF,
                            "Tesouro Selic":        _COR_ALT,
                        },
                    )
                    fig_perf.update_traces(line_width=2)
                    fig_perf.update_layout(**_plot_layout(360))
                    st.plotly_chart(fig_perf, use_container_width=True,
                                    config={"displayModeBar": False},
                                    key="pb3_perf_chart")
            else:
                st.caption("Dados insuficientes para o ano atual.")
        else:
            st.caption("Não foi possível baixar preços para as empresas selecionadas.")
    else:
        st.caption("Nenhuma empresa selecionada para mostrar desempenho.")

    # ── Metodologia e referências científicas ────────────────────────────────
    _render_metodologia_portfolio()


def _render_metodologia_portfolio() -> None:
    """Análises aplicadas para chegar às empresas do portfólio + referências.

    Lista apenas as técnicas efetivamente usadas por ESTA aba (engine
    PORTFOLIO), reforçando a credibilidade da seleção sem alegar métodos de
    outras abas.
    """
    try:
        from core import metodologia as _met
    except Exception:
        return

    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    _sec_hdr("🔬 Como chegamos a estas empresas — metodologia e referências")
    st.caption(
        f"A carteira sugerida não é fruto de opinião: resulta de "
        f"**{_met.total_metodos(_met.ENG_PF)} análises quantitativas** encadeadas, "
        "cada uma fundamentada em literatura acadêmica consolidada de finanças. "
        "Abaixo, o que cada etapa faz e o estudo que a embasa — para você confiar "
        "no porquê de cada ticker selecionado."
    )

    with st.expander("📚 Ver as análises aplicadas e suas referências científicas",
                     expanded=False):
        for etapa, metodos in _met.catalogo_por_etapa(_met.ENG_PF).items():
            st.markdown(f"#### {etapa}")
            linhas = [
                f"- **{m.nome}** — {m.o_que_faz}  \n"
                f"  <span style='color:#94A3B8;font-size:0.85em'>📖 {m.referencia}</span>"
                for m in metodos
            ]
            st.markdown("\n".join(linhas), unsafe_allow_html=True)
            st.markdown("")

        st.divider()
        st.markdown("#### 📑 Referências bibliográficas")
        st.caption("Estudos e autores que fundamentam as técnicas acima "
                   "(ordem alfabética):")
        refs = "\n".join(
            f"{i}. {r}" for i, r in enumerate(_met.referencias_ordenadas(_met.ENG_PF), 1)
        )
        st.markdown(refs)

        st.info(
            "⚠️ A robustez metodológica reforça a qualidade da triagem, mas não "
            "elimina o risco de mercado. Use como apoio à decisão, não como "
            "recomendação automática."
        )

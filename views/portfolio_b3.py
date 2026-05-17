"""
views/portfolio_b3.py — Criação de Portfólio B3
Roda o engine de scoring v2 em todos os segmentos, identifica líderes
históricos por ano (publication lag = 1), reconstrói o desempenho histórico
por segmento e monta uma carteira sugerida com empresas reais da B3.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

import core.b3_db as _db

# ── Importa engine compartilhado de empresas_b3 ───────────────────────────────
from views.empresas_b3 import (
    _GAMMA_DEF, _CAP_DEF, _SOFT_DEF,
    _COR_POS, _COR_NEG, _COR_ALT, _COR_INF, _COR_NEU,
    _apply_cap_soft, _apply_decay_penalty,
    _batch_yf_precos_mensais,
    _enrich_com_slopes,
    _fv, _fp, _logo_url,
    _get_pesos_setor,
    _plot_layout, _sec_hdr,
    _score_historico_ano,
    _select_n_heuristica,
    _weights_from_scores,
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
) -> tuple[float, float, float, dict[str, float]]:
    """
    Reconstrói a carteira por segmento usando líderes identificados por ano.
    Retorna (valor_estrategia, valor_selic, valor_ew).
    """
    if df_prec_seg.empty:
        return 0.0, 0.0, 0.0, {}

    all_tks = list(df_prec_seg.columns)
    cotas_est: dict[str, float] = {tk: 0.0 for tk in all_tks}
    cotas_ew:  dict[str, float] = {tk: 0.0 for tk in all_tks}
    selic_acum = 0.0
    ultimo_ano = -1
    pesos_est: dict[str, float] = {}

    for dt, row in df_prec_seg.iterrows():
        ano = dt.year
        selic_aa_ano = selic_macro.get(ano, taxa_selic_aa)
        taxa_m = (1 + selic_aa_ano) ** (1 / 12) - 1
        selic_acum = selic_acum * (1 + taxa_m) + aporte

        if ano != ultimo_ano:
            ultimo_ano = ano
            lids = lids_por_ano.get(ano, [])
            pesos_est = pesos_por_ano.get(ano, {tk: 1.0 / len(lids) for tk in lids} if lids else {})

        # Estratégia
        est_disp = [tk for tk in pesos_est if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        if est_disp:
            tw = sum(pesos_est.get(tk, 0.0) for tk in est_disp) or 1.0
            for tk in est_disp:
                cotas_est[tk] += aporte * pesos_est.get(tk, 0.0) / tw / float(row[tk])

        # EW benchmark
        ew_disp = [tk for tk in all_tks if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        if ew_disp:
            for tk in ew_disp:
                cotas_ew[tk] += aporte / len(ew_disp) / float(row[tk])

    def _val(cotas: dict[str, float], row: pd.Series) -> float:
        return sum(
            cotas[tk] * float(row[tk])
            for tk in all_tks
            if tk in cotas and pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0
        )

    last_row = df_prec_seg.iloc[-1]
    contrib_est = {
        tk: cotas_est[tk] * float(last_row[tk])
        for tk in all_tks
        if tk in cotas_est and pd.notna(last_row.get(tk)) and float(last_row.get(tk, 0) or 0) > 0
    }
    return _val(cotas_est, last_row), selic_acum, _val(cotas_ew, last_row), contrib_est


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
    aporte: float,
    ano_inicio: int,
    gamma: float,
    cap: float,
    soft: float,
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

    for ano in range(ano_inicio, ano_atual):
        score_map = _score_historico_ano(hist_batch, tickers, ano, pesos, tk_grupos, lag=1)
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
        n = _select_n_heuristica(scores_desc) if len(ranked) >= 2 else 1
        lids = [tk for tk, _ in ranked[:n] if tk in tickers]

        lids_por_ano[ano] = lids
        for tk in lids:
            lideres_rows.append({
                "Ano": ano, "ticker": tk,
                "SETOR": setor, "SUBSETOR": subsetor, "SEGMENTO": segmento,
            })
        if lids and len(lids) >= 2:
            w = _apply_cap_soft(_weights_from_scores(lids, score_map, gamma), cap, soft)
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
    score_proximo = _score_historico_ano(hist_batch, tickers, ano_atual, pesos, tk_grupos, lag=1)
    if not score_proximo:
        return None
    ano_ref_score = ano_atual - 1

    # Líderes para próximo ano
    ranked_prox = sorted(score_proximo.items(), key=lambda x: x[1], reverse=True)
    scores_prox = [s for _, s in ranked_prox[:3]]
    n_prox      = _select_n_heuristica(scores_prox) if len(ranked_prox) >= 2 else 1
    lids_prox   = [tk for tk, _ in ranked_prox[:n_prox] if tk in tickers]

    if lids_prox and len(lids_prox) >= 2:
        pesos_prox = _apply_cap_soft(_weights_from_scores(lids_prox, score_proximo, gamma), cap, soft)
    elif lids_prox:
        pesos_prox = {lids_prox[0]: 1.0}
    else:
        pesos_prox = {}

    # Reconstrução histórica
    tks_disp = [tk for tk in tickers if tk in df_precos_all.columns]
    val_est = val_selic = val_ew = 0.0
    contrib_est: dict[str, float] = {}
    if tks_disp and not df_precos_all.empty:
        df_prec_seg = df_precos_all[tks_disp].dropna(how="all")
        val_est, val_selic, val_ew, contrib_est = _simular_seg_backtest(
            df_prec_seg, lids_por_ano, pesos_por_ano,
            aporte, taxa_selic_aa, selic_macro,
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
        "n_anos": len(anos_com_score),
        "ano_inicio": min(anos_com_score),
        "ano_fim": max(anos_com_score),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Componentes UI
# ══════════════════════════════════════════════════════════════════════════════

def _margem_pct(val: float, ref: float) -> float:
    if ref <= 0:
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
    val_est, val_selic, val_ew = res["val_est"], res["val_selic"], res["val_ew"]
    m_selic = _margem_pct(val_est, val_selic)
    m_ew    = _margem_pct(val_est, val_ew)

    st.markdown(
        f'<div class="pb3-seg-hdr">{setor} › {sub} › {seg}</div>'
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

    with st.expander("🧩 Patch 5 — Desempenho das Empresas", expanded=False):
        if df_precos_all.empty:
            st.info("Preços indisponíveis para o painel de desempenho.")
        else:
            cols = [p["tk"] for p in proximos_uniq if p["tk"] in df_precos_all.columns]
            if not cols:
                st.info("Nenhuma empresa selecionada encontrada na matriz de preços.")
            else:
                prec = df_precos_all[cols].dropna(how="all").ffill()
                ret_12m = {}
                vol_12m = {}
                janela = prec.tail(12)
                for tk in cols:
                    s = pd.to_numeric(janela[tk], errors="coerce").dropna()
                    if len(s) >= 2 and float(s.iloc[0]) > 0:
                        ret_12m[tk] = (float(s.iloc[-1]) / float(s.iloc[0]) - 1) * 100
                        vol_12m[tk] = s.pct_change().dropna().std() * np.sqrt(12) * 100
                perf = pd.DataFrame({
                    "Ticker": list(ret_12m.keys()),
                    "Retorno 12m (%)": [round(v, 1) for v in ret_12m.values()],
                    "Volatilidade 12m (%)": [round(vol_12m.get(k, np.nan), 1) for k in ret_12m],
                })
                st.dataframe(perf, use_container_width=True, hide_index=True)


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
        'Ferramenta quantitativa para identificar empresas reais, diversificadas '
        'por segmento, que historicamente venceram Tesouro Selic e o Equal-Weight '
        'do próprio segmento. Usa scoring com publication lag = 1, reconstrução '
        'histórica e líderes para aplicação prática no próximo ciclo.'
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

    usar_ew_como_criterio = uso_ew == "Critério de seleção"

    rodar = st.button("🚀 Rodar Criação de Portfólio", type="primary", key="pb3_rodar")

    # ── DADOS BASE ────────────────────────────────────────────────────────────
    with st.spinner("Carregando dados do banco…"):
        df_set        = _db.load_setores()
        selic_macro   = _db.load_selic_macro()
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

        # Enriquece com slope_log por ticker
        # (reconstrói df temporário para enriquecer e desmonta de volta)
        if df_mult_todos.empty:
            hist_batch = hist_batch_raw
        else:
            df_enrich = _enrich_com_slopes(df_mult_todos, hist_batch_raw)
            # não precisamos do df_enrich aqui; o slope_log é calculado dentro de
            # _score_historico_ano via hist_batch — o enriquecimento no snapshot
            # é feito separadamente em _tab_avancada
            hist_batch = hist_batch_raw

        with st.spinner("Baixando preços mensais (pode demorar)…"):
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
                res = _processar_segmento(
                    tickers_seg, hist_batch, df_precos_all,
                    str(setor), str(subsetor), str(segmento),
                    taxa_selic_aa, selic_macro, float(aporte),
                    int(ano_inicio), gamma, cap, soft,
                )
                if res:
                    resultados.append(res)
            prog.progress((i + 1) / max(len(grupos), 1),
                          text=f"Segmento {i+1}/{len(grupos)}: {segmento}")

        prog.empty()
        st.session_state["pb3_resultados"] = resultados
        st.session_state["pb3_df_set"]     = df_set
        st.session_state["pb3_precos_all"] = df_precos_all

    resultados = st.session_state.get("pb3_resultados", [])
    df_set     = st.session_state.get("pb3_df_set", df_set)
    df_precos_all = st.session_state.get("pb3_precos_all", pd.DataFrame())

    if not resultados:
        st.warning("Nenhum segmento retornou dados suficientes.")
        return

    # ── FILTROS DE APROVAÇÃO ──────────────────────────────────────────────────
    ano_atual = pd.Timestamp.now().year

    def _aprovado(res: dict) -> bool:
        m_selic = _margem_pct(res["val_est"], res["val_selic"])
        m_ew    = _margem_pct(res["val_est"], res["val_ew"])
        ultimo_lid_seg = max(res["ultimo_lid"].values()) if res["ultimo_lid"] else 0
        recente = (ano_atual - 1 - ultimo_lid_seg) <= max_anos_lid
        if not recente:
            return False
        if m_selic < thr_selic:
            return False
        if usar_ew_como_criterio and res["val_ew"] > 0 and m_ew < thr_ew:
            return False
        return True

    aprovados  = [r for r in resultados if _aprovado(r)]
    reprovados = [r for r in resultados if not _aprovado(r)]

    # ── TABELA DE AUDITORIA ───────────────────────────────────────────────────
    _sec_hdr(f"📋 Auditoria de Segmentos — {len(resultados)} analisados · "
             f"{len(aprovados)} aprovados")

    rows_tbl: list[dict] = []
    for res in resultados:
        m_s  = _margem_pct(res["val_est"], res["val_selic"])
        m_ew = _margem_pct(res["val_est"], res["val_ew"])
        ult  = max(res["ultimo_lid"].values()) if res["ultimo_lid"] else 0
        rows_tbl.append({
            "Setor":     res["setor"],
            "Subsetor":  res["subsetor"],
            "Segmento":  res["segmento"],
            "Status":    _status_seg(res["val_est"], res["val_selic"], res["val_ew"],
                                     thr_selic, thr_ew),
            "vs Selic (%)":  round(m_s, 1),
            "vs EW (%)":     round(m_ew, 1),
            "Patrimônio":    _fv(res["val_est"]),
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
            motivos = []
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
                "motivos": motivos,
                "setor": res["setor"],
                "subsetor": res["subsetor"],
                "segmento": res["segmento"],
                "alpha_selic": _margem_pct(res["val_est"], res["val_selic"]),
                "alpha_ew": _margem_pct(res["val_est"], res["val_ew"]),
                "rank_score": _rank_ticker(score_prox, tk),
                "ano_lider": ano_ref if tk == ticker_lider else res.get("ultimo_lid", {}).get(tk),
            })

    # Remove duplicatas (mesmo ticker em múltiplos segmentos), preservando motivos.
    mapa_prox: dict[str, dict] = {}
    for p in sorted(proximos, key=lambda x: x["score"], reverse=True):
        tk = p["tk"]
        if tk not in mapa_prox:
            mapa_prox[tk] = p
            continue
        cur = mapa_prox[tk]
        cur["motivos"] = list(dict.fromkeys((cur.get("motivos") or []) + (p.get("motivos") or [])))
        if float(p.get("score", 0.0)) > float(cur.get("score", 0.0)):
            cur.update({k: v for k, v in p.items() if k != "motivos"})
    proximos_uniq = sorted(mapa_prox.values(), key=lambda x: x["score"], reverse=True)

    if proximos_uniq:
        for i in range(0, len(proximos_uniq), 3):
            cols_p = st.columns(3, gap="small")
            for j, item in enumerate(proximos_uniq[i:i+3]):
                mot_html = "".join(
                    f'<div class="pb3-lider-motivo">• {m}</div>'
                    for m in item["motivos"]
                ) or '<div class="pb3-lider-motivo">Líder do segmento</div>'
                with cols_p[j]:
                    st.markdown(
                        f'<div class="pb3-lider-card">'
                        f'<img src="{_logo_url(item["tk"])}" style="width:48px;height:48px;'
                        f'border-radius:10px;object-fit:contain;background:rgba(255,255,255,.06);'
                        f'padding:5px;" onerror="this.style.display=\'none\'">'
                        f'<div class="pb3-lider-ticker">({item["tk"]})</div>'
                        f'<div class="pb3-emp-nome">{item["nome"]}</div>'
                        f'{mot_html}'
                        f'<div class="pb3-lider-ano">Para compra em {ano_atual}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
    else:
        st.info("Nenhum líder identificado com os parâmetros atuais.")

    # ── DISTRIBUIÇÃO SETORIAL ────────────────────────────────────────────────
    if proximos_uniq:
        st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                    unsafe_allow_html=True)
        _sec_hdr("🍕 Distribuição Setorial do Portfólio Sugerido")

        dist: dict[str, int] = {}
        for p in proximos_uniq:
            dist[p["setor"]] = dist.get(p["setor"], 0) + 1
        df_dist = pd.DataFrame(
            {"Setor": list(dist.keys()), "Empresas": list(dist.values())}
        )
        fig_pie = px.pie(
            df_dist, names="Setor", values="Empresas",
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

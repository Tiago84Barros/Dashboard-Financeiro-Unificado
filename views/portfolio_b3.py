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
from core.dossie_b3 import avaliar_para_selecao, quali_gate_disponivel
from design.componentes import card_metrica

# ── Importa engine compartilhado de empresas_b3 ───────────────────────────────
from views.empresas_b3 import (
    _GAMMA_DEF, _CAP_DEF, _SOFT_DEF, _REBAL_MONTH,
    _COR_POS, _COR_NEG, _COR_ALT, _COR_INF, _COR_NEU,
    _DIV_POR_ACAO_MAX,
    _aplicar_cheapness,
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


_MIN_MARKET_CAP_COVERAGE = 0.80
_MIN_ADTV_COVERAGE = 0.70
_MIN_CORR_PAIR_COVERAGE = 0.60
_PREGOES_MES = 21.0


class MarketCapDataError(RuntimeError):
    """A fonte de valor de mercado não pôde ser consultada com segurança."""


class LiquidezDataError(RuntimeError):
    """A série de volume negociado não pôde ser consultada com segurança."""


def _ticker_key(value: object) -> str:
    return str(value or "").upper().replace(".SA", "").strip()


@st.cache_data(ttl=3600)
def _load_market_caps() -> dict[str, float]:
    """Carrega marketCap PIT e complementa lacunas com o snapshot corrente.

    Falhas são levantadas ao chamador para nunca serem confundidas com
    capitalização zero nem ficarem cacheadas como um mapa vazio.
    """
    from sqlalchemy import text
    from core.database import get_engine

    eng = get_engine()
    if eng is None:
        raise MarketCapDataError("conexão com o banco indisponível")

    queries = (
        text("""
            SELECT DISTINCT ON (ticker) ticker, (metric_value)::numeric AS v
            FROM market.calculated_metric_vintages
            WHERE metric_name = 'marketCap' AND metric_value IS NOT NULL
            ORDER BY ticker, year DESC, available_at DESC, recorded_at DESC
        """),
        text("""
            SELECT DISTINCT ON (ticker) ticker, (metric_value)::numeric AS v
            FROM market.calculated_metrics
            WHERE metric_name = 'marketCap' AND metric_value IS NOT NULL
            ORDER BY ticker, year DESC, updated_at DESC
        """),
    )
    values: dict[str, float] = {}
    errors: list[str] = []
    successful_queries = 0
    for query in queries:
        try:
            # Uma conexão por fonte mantém o fallback utilizável se a tabela PIT
            # estiver ausente ou com schema incompatível.
            with eng.connect() as conn:
                rows = conn.execute(query).fetchall()
            successful_queries += 1
            for row in rows:
                if row.v is None:
                    continue
                value = float(row.v)
                if np.isfinite(value) and value > 0:
                    # PIT tem precedência; o snapshot só completa ausências.
                    values.setdefault(_ticker_key(row.ticker), value)
        except Exception as exc:
            errors.append(type(exc).__name__)

    if successful_queries == 0:
        detail = ", ".join(errors) if errors else "fonte indisponível"
        raise MarketCapDataError(f"consultas de marketCap falharam ({detail})")
    return values


def _market_cap_coverage(
    tickers: pd.Series | list[str] | tuple[str, ...],
    market_caps: dict[str, float],
) -> tuple[int, int, float]:
    """Retorna (cobertos, total, proporção) para o universo normalizado."""
    universe = {_ticker_key(ticker) for ticker in tickers if _ticker_key(ticker)}
    covered = sum(
        1 for ticker in universe
        if ticker in market_caps
        and np.isfinite(float(market_caps[ticker]))
        and float(market_caps[ticker]) > 0
    )
    total = len(universe)
    return covered, total, (covered / total if total else 0.0)


@st.cache_data(ttl=3600)
def _load_adtv(meses: int = 6) -> dict[str, float]:
    """Volume financeiro DIÁRIO típico (R$/dia) por ticker.

    Mediana do volume financeiro MENSAL dos últimos ``meses`` ÷ 21 pregões.
    **Mediana e não média**: um único pregão atípico (leilão, notícia, negócio
    de bloco) infla a média e faz um papel ilíquido parecer líquido.

    Por que existe: o piso de "liquidez" da tela era o **valor de mercado**, que
    mede TAMANHO e não NEGOCIABILIDADE. São coisas distintas — BRAP3 e CEBR5
    passam folgadamente por um piso de R$ 1 bi de capitalização e mesmo assim
    não têm contraparte no book para uma ordem relevante. Espelha a regra que
    ``data_pipeline.market.fii.liquidez_diaria`` já aplica aos FIIs.

    A agregação por mês é agnóstica à granularidade da série: se as barras de
    ``market.historical_prices`` forem diárias, a soma do mês é o volume
    financeiro mensal; se já forem mensais, a soma é a própria barra.
    """
    from sqlalchemy import text
    from core.database import get_engine

    eng = get_engine()
    if eng is None:
        raise LiquidezDataError("conexão com o banco indisponível")

    query = text("""
        WITH mensal AS (
            SELECT ticker,
                   date_trunc('month', date) AS mes,
                   SUM(COALESCE(close, adjusted_close) * volume) AS financeiro
            FROM market.historical_prices
            WHERE volume IS NOT NULL AND volume > 0
              AND COALESCE(close, adjusted_close) IS NOT NULL
              AND date >= (CURRENT_DATE - make_interval(months => :m))
            GROUP BY 1, 2
        )
        SELECT ticker,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY financeiro) AS mediana
        FROM mensal
        GROUP BY ticker
    """)
    try:
        with eng.connect() as conn:
            rows = conn.execute(query, {"m": int(meses)}).fetchall()
    except Exception as exc:
        raise LiquidezDataError(
            f"consulta de volume negociado falhou ({type(exc).__name__})"
        ) from exc

    values: dict[str, float] = {}
    for row in rows:
        if row.mediana is None:
            continue
        diario = float(row.mediana) / _PREGOES_MES
        if np.isfinite(diario) and diario > 0:
            values[_ticker_key(row.ticker)] = diario
    return values


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
    details_out: dict | None = None,
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
    caixa_est = 0.0
    caixa_ew = 0.0
    prev_est = prev_ew = prev_selic = None
    monthly_returns: list[dict] = []

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
        caixa_est += aporte
        if est_disp:
            tw = sum(pesos_est.get(tk, 0.0) for tk in est_disp) or 1.0
            for tk in est_disp:
                gross = caixa_est * pesos_est.get(tk, 0.0) / tw
                net = max(gross - custo_compra(tk, gross, cost_cfg), 0.0)
                cotas_est[tk] += net / float(row[tk])
            caixa_est = 0.0

        # EW benchmark
        ew_disp = [tk for tk in all_tks if pd.notna(row.get(tk)) and float(row.get(tk, 0) or 0) > 0]
        caixa_ew += aporte
        for tk in ew_disp:
            last_prices[tk] = float(row[tk])
        if ew_disp:
            for tk in ew_disp:
                gross = caixa_ew / len(ew_disp)
                net = max(gross - custo_compra(tk, gross, cost_cfg), 0.0)
                cotas_ew[tk] += net / float(row[tk])
            caixa_ew = 0.0

        current_est = caixa_est + sum(
            cotas_est[tk] * last_prices[tk]
            for tk in all_tks if tk in last_prices
        )
        current_ew = caixa_ew + sum(
            cotas_ew[tk] * last_prices[tk]
            for tk in all_tks if tk in last_prices
        )
        if prev_est and prev_ew and prev_selic:
            monthly_returns.append({
                "Data": dt,
                "strategy": (current_est - aporte) / prev_est - 1.0,
                "selic": (selic_acum - aporte) / prev_selic - 1.0,
                "equal_weight": (current_ew - aporte) / prev_ew - 1.0,
            })
        prev_est, prev_ew, prev_selic = current_est, current_ew, selic_acum

    def _val(cotas: dict[str, float]) -> float:
        cash = caixa_est if cotas is cotas_est else caixa_ew
        return cash + sum(
            cotas[tk] * last_prices[tk]
            for tk in all_tks
            if tk in cotas and tk in last_prices
        )

    contrib_est = {
        tk: cotas_est[tk] * last_prices[tk]
        for tk in all_tks
        if tk in cotas_est and tk in last_prices
    }
    if details_out is not None:
        details_out["monthly_returns"] = monthly_returns
        details_out["pending_cash_strategy"] = float(caixa_est)
        details_out["pending_cash_equal_weight"] = float(caixa_ew)
    return _val(cotas_est), selic_acum, _val(cotas_ew), contrib_est


def _quali_avaliar_cached(tk: str) -> dict:
    """Parecer de seleção com cache de sessão (o LLM já tem cache diário)."""
    cache = st.session_state.setdefault("pb3_quali_cache", {})
    if tk not in cache:
        try:
            cache[tk] = avaliar_para_selecao(tk)
        except Exception as exc:  # fail-open: falha de avaliação nunca veta
            cache[tk] = {"classificacao": "aprovar_com_ressalvas",
                         "motivo": f"avaliação indisponível ({exc})",
                         "parecer": {}, "dossie": {}}
    return cache[tk]


def _entry_guard_exclui(entry_guard: dict, tk: str) -> bool:
    """Mesma regra de exclusão do Score de Entrada usada na montagem da carteira."""
    entry = (entry_guard.get(str(tk).upper().replace(".SA", ""), {})
             if isinstance(entry_guard, dict) else {})
    if not entry:
        return False
    status = str(entry.get("status_entrada", "")).lower()
    score_e = float(entry.get("score_entrada", 0.0) or 0.0)
    return status.startswith("exclu") or score_e < 30.0


def _aplicar_gate_qualitativo(
    selecionados: list[str],
    ranked_prox: list[tuple[str, float]],
    entry_guard: dict,
    pesos_p: dict[str, float],
    seg_label: str,
    log: dict,
    max_substitutos_avaliados: int = 2,
) -> list[str]:
    """
    Veto qualitativo com substituição pelo próximo do ranking do segmento.

    Roda DEPOIS do ranking estatístico e não altera score algum: um nome
    vetado sai com motivo registrado e o próximo elegível do MESMO segmento
    (na ordem do score) herda a vaga e o orçamento de peso. Veto é exceção
    grave e documentada no parecer; falha de LLM nunca veta (fail-open).
    """
    finais: list[str] = []
    for tk in selecionados:
        aval = _quali_avaliar_cached(tk)
        if aval["classificacao"] != "vetar":
            if aval["classificacao"] == "aprovar_com_ressalvas" and aval.get("motivo"):
                log["ressalvas"][tk] = str(aval["motivo"])
            finais.append(tk)
            continue
        log["vetados"].append({"tk": tk, "segmento": seg_label,
                               "motivo": str(aval.get("motivo", ""))})
        substituto = None
        avaliacoes = 0
        for cand, _sc in ranked_prox:
            cand = str(cand)
            if cand in finais or cand in selecionados:
                continue
            if _entry_guard_exclui(entry_guard, cand):
                continue
            aval_c = _quali_avaliar_cached(cand)
            avaliacoes += 1
            if aval_c["classificacao"] != "vetar":
                substituto = cand
                if (aval_c["classificacao"] == "aprovar_com_ressalvas"
                        and aval_c.get("motivo")):
                    log["ressalvas"][cand] = str(aval_c["motivo"])
                break
            log["vetados"].append({"tk": cand, "segmento": seg_label,
                                   "motivo": str(aval_c.get("motivo", ""))})
            if avaliacoes >= max_substitutos_avaliados:
                break
        if substituto:
            finais.append(substituto)
            pesos_p[substituto] = pesos_p.get(substituto) or pesos_p.get(tk, 0.0)
            log["substituicoes"].append({"entra": substituto, "sai": tk,
                                         "segmento": seg_label})
    return finais


def _aplicar_diversificacao_correlacao(
    items: list[dict],
    aprovados: list[dict],
    returns: pd.DataFrame,
    entry_guard: dict,
    threshold: float,
    max_substituicoes: int = 3,
) -> tuple[list[dict], list[dict]]:
    """
    Troca o mais fraco de um par muito correlacionado (rho >= threshold) pelo
    próximo do ranking do MESMO segmento — nunca troca entre segmentos
    distintos, isso quebraria a ideia de "líder do segmento".

    Roda DEPOIS da seleção estatística e do gate qualitativo, sobre a lista
    final já deduplicada entre segmentos. A cada iteração:
      1. mede a correlação da seleção atual;
      2. pega o par mais correlacionado acima do limiar;
      3. tenta substituir o mais fraco (menor score) por candidatos do
         PRÓPRIO segmento, na ordem do ranking, aceitando o primeiro que
         reduz a correlação média da carteira e passa no entry guard;
      4. se nenhum candidato do primeiro par ajuda, tenta o próximo par mais
         correlacionado na mesma rodada, para não travar num par sem saída.
    Para quando não há mais par acima do limiar, quando nenhum par tem
    substituto que ajude, ou após max_substituicoes trocas.
    """
    from core.b3_correlation_diversification import (
        average_pairwise_correlation, correlation_matrix, high_correlation_pairs,
    )

    items = [dict(it) for it in items]
    log: list[dict] = []
    res_by_segmento = {
        (r["setor"], r["subsetor"], r["segmento"]): r for r in aprovados
    }

    for _ in range(max_substituicoes):
        # Ordem canônica das colunas: a matriz de correlação é simétrica, então
        # ordenar não muda valor nenhum — mas fixa a enumeração dos pares, que
        # de outro modo herda a ordem de montagem da carteira.
        cols = sorted(it["tk"] for it in items if it["tk"] in returns.columns)
        if len(cols) < 2:
            break
        corr = correlation_matrix(returns[cols])
        pairs = high_correlation_pairs(corr, threshold)
        if not pairs:
            break
        baseline_avg = average_pairwise_correlation(corr)
        existing = {it["tk"] for it in items}

        troca = None  # (idx_do_fraco, cand_tk, cand_score, rho, seg_label)
        for tk_a, tk_b, rho in pairs:
            idx_a = next(i for i, it in enumerate(items) if it["tk"] == tk_a)
            idx_b = next(i for i, it in enumerate(items) if it["tk"] == tk_b)
            fraco_idx, forte_idx = (
                (idx_a, idx_b)
                if float(items[idx_a].get("score", 0.0)) <= float(items[idx_b].get("score", 0.0))
                else (idx_b, idx_a)
            )
            fraco = items[fraco_idx]
            seg_key = (fraco["setor"], fraco["subsetor"], fraco["segmento"])
            res = res_by_segmento.get(seg_key)
            if res is None:
                continue
            # Desempate pelo TICKER: sem chave secundária, empates de score
            # seguem a ordem do dict de origem, que varia com PYTHONHASHSEED
            # entre processos (medido em 29/07/2026: GOAU4 vs SHUL4).
            ranked = sorted(res["score_proximo"].items(),
                            key=lambda kv: (-float(kv[1]), str(kv[0])))
            for cand_tk, cand_score in ranked:
                cand_tk = str(cand_tk)
                if cand_tk in existing or cand_tk == fraco["tk"]:
                    continue
                if cand_tk not in returns.columns:
                    continue
                if _entry_guard_exclui(entry_guard, cand_tk):
                    continue
                trial_cols = [cand_tk if c == fraco["tk"] else c for c in cols]
                trial_corr = correlation_matrix(returns[trial_cols])
                trial_avg = average_pairwise_correlation(trial_corr)
                if trial_avg < baseline_avg - 1e-6:
                    troca = (fraco_idx, cand_tk, float(cand_score), rho,
                             f"{fraco['setor']} › {fraco['segmento']}")
                    break
            if troca:
                break

        if troca is None:
            break
        idx, cand_tk, cand_score, rho, seg_label = troca
        antigo = items[idx]
        novo = dict(antigo)
        novo["tk"] = cand_tk
        novo["score"] = cand_score
        novo["rank_score"] = _rank_ticker(res_by_segmento[
            (antigo["setor"], antigo["subsetor"], antigo["segmento"])
        ]["score_proximo"], cand_tk)
        novo["quali"] = None
        novo["motivos"] = list(dict.fromkeys(
            (antigo.get("motivos") or [])
            + [f"Entrou por diversificação no lugar de {antigo['tk']} (correlação {rho:.2f})"]
        ))
        items[idx] = novo
        log.append({
            "sai": antigo["tk"], "entra": cand_tk, "rho": rho, "segmento": seg_label,
        })

    return items, log


def _rank_ticker(score_map: dict[str, float], ticker: str) -> int | None:
    ranked = sorted(score_map.items(),
                 key=lambda x: (-float(x[1]), str(x[0])))
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
    janela_val_anos: int = 2,
    cheapness_weight: float = 0.0,
    walk_forward: bool = False,
    cap_adaptativo: bool = False,
) -> dict | None:
    """
    Roda o engine de scoring ano-a-ano para um segmento.
    Retorna dict com líderes, backtest e score para o próximo ano, ou None.
    """
    if len(tickers) < 1:
        return None

    pesos = _aplicar_cheapness(_get_pesos_setor(setor), cheapness_weight)
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

        ranked = sorted(score_map.items(),
                     key=lambda x: (-float(x[1]), str(x[0])))
        scores_desc = [s for _, s in ranked[:3]]
        from core.portfolio_constraints import minimum_assets_for_cap
        n = _select_n_heuristica(scores_desc) if len(ranked) >= 2 else 1
        # Cap adaptativo (mercado B3, raso): não força um mínimo de ativos que o
        # segmento não tem — relaxa o teto por ativo até ficar viável.
        _min_n = 1 if cap_adaptativo else minimum_assets_for_cap(cap)
        n = min(len(ranked), max(n, _min_n))
        lids = [tk for tk, _ in ranked[:n] if tk in tickers]

        lids_por_ano[ano] = lids
        for tk in lids:
            lideres_rows.append({
                "Ano": ano, "ticker": tk,
                "SETOR": setor, "SUBSETOR": subsetor, "SEGMENTO": segmento,
            })
        if lids and len(lids) >= 2:
            _cap_ef = max(cap, 1.0 / len(lids)) if cap_adaptativo else cap
            try:
                w = _apply_cap_soft(_weights_from_scores(lids, score_map, gamma), _cap_ef, soft)
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
        # Itera a LISTA (ordenada por score), não o set: iteração de conjunto
        # varia entre processos. Aqui os valores não dependem da ordem, mas a
        # ordem de inserção do dict sim — e dicts alimentam outras estruturas.
        for tk in dict.fromkeys(lids):
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
    # Ordenação TOTAL (score desc, ticker asc): empate não pode ser resolvido
    # pela ordem de inserção do dict — ela varia entre processos.
    ranked_prox = sorted(score_proximo.items(),
                         key=lambda x: (-float(x[1]), str(x[0])))
    scores_prox = [s for _, s in ranked_prox[:3]]
    from core.portfolio_constraints import minimum_assets_for_cap
    n_prox      = _select_n_heuristica(scores_prox) if len(ranked_prox) >= 2 else 1
    _min_n_prox = 1 if cap_adaptativo else minimum_assets_for_cap(cap)
    n_prox      = min(len(ranked_prox), max(n_prox, _min_n_prox))
    lids_prox   = [tk for tk, _ in ranked_prox[:n_prox] if tk in tickers]

    if lids_prox and len(lids_prox) >= 2:
        _cap_ef_prox = max(cap, 1.0 / len(lids_prox)) if cap_adaptativo else cap
        try:
            pesos_prox = _apply_cap_soft(
                _weights_from_scores(lids_prox, score_proximo, gamma), _cap_ef_prox, soft
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
    p_value_oos = 1.0
    n_months_oos = 0
    rank_ic_mean = float("nan")
    rank_ic_years = 0
    rank_ic_tstat = float("nan")
    p_value_ic = 1.0
    wf_hit_rate = float("nan")
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
        # Segmentos cujos tickers não têm preços na janela ficam com df_prec_seg
        # vazio (index.max() = NaT) — sem holdout OOS, mantém os defaults (não
        # aprova). Sem este guard, int(NaT.year) quebra com "float NaN to integer".
        _ultimo_dt = df_prec_seg.index.max()
        if df_prec_seg.empty or pd.isna(_ultimo_dt):
            df_validation = df_prec_seg.iloc[0:0]
        elif walk_forward:
            # Walk-forward: a validação é o HISTÓRICO INTEIRO point-in-time. Como
            # os líderes de cada ano usam só dados anteriores, os retornos ao longo
            # de todo o período já são out-of-sample — e cobrem VÁRIOS regimes, não
            # só os últimos 24m. Mais amplitude e robustez (menos dependente de uma
            # janela recente atípica).
            df_validation = df_prec_seg
        else:
            validation_start_year = int(_ultimo_dt.year) - int(janela_val_anos)
            df_validation = df_prec_seg[
                (df_prec_seg.index.year > validation_start_year)
                | (
                    (df_prec_seg.index.year == validation_start_year)
                    & (df_prec_seg.index.month >= _REBAL_MONTH)
                )
            ]
        if len(df_validation) >= 18:
            validation_details: dict = {}
            val_est_oos, val_selic_oos, val_ew_oos, _ = _simular_seg_backtest(
                df_validation, lids_por_ano, pesos_por_ano,
                aporte, taxa_selic_aa, selic_macro,
                dividendos=dividendos,
                cost_cfg=_segment_costs,
                details_out=validation_details,
            )
            monthly = pd.DataFrame(validation_details.get("monthly_returns") or [])
            if len(monthly) >= 12:
                # Habilidade de SELEÇÃO: excesso da estratégia sobre o Equal-Weight
                # do próprio segmento — NÃO sobre a Selic. Isso neutraliza o regime
                # macro: se o cenário derrubou o segmento inteiro, o EW caiu junto,
                # então só sobra o que a escolha dos líderes acrescentou. Bater a
                # Selic é decisão de timing do investidor, não de habilidade.
                excess = (
                    pd.to_numeric(monthly["strategy"], errors="coerce")
                    - pd.to_numeric(monthly["equal_weight"], errors="coerce")
                ).replace([np.inf, -np.inf], np.nan).dropna()
                n_months_oos = int(len(excess))
                if len(excess) >= 12 and float(excess.std(ddof=1) or 0.0) > 0:
                    from scipy.stats import ttest_1samp
                    test = ttest_1samp(excess, popmean=0.0, alternative="greater")
                    p_value_oos = (
                        float(test.pvalue) if np.isfinite(test.pvalue) else 1.0
                    )
                # Robustez multi-regime (walk-forward): em quantos ANOS a estratégia
                # bateu os pares (retorno composto do ano vs Equal-Weight)? Mostra se
                # a vantagem se repete em vários ciclos, nao so no agregado.
                if walk_forward and "Data" in monthly.columns:
                    _m = monthly.copy()
                    _m["_ano"] = pd.to_datetime(_m["Data"], errors="coerce").dt.year
                    _s = pd.to_numeric(_m["strategy"], errors="coerce")
                    _e = pd.to_numeric(_m["equal_weight"], errors="coerce")
                    _wins = _tot = 0
                    for _yr, _idx in _m.groupby("_ano").groups.items():
                        _rs = float((1.0 + _s.loc[_idx]).prod())
                        _re = float((1.0 + _e.loc[_idx]).prod())
                        if np.isfinite(_rs) and np.isfinite(_re):
                            _tot += 1
                            _wins += 1 if _rs > _re else 0
                    if _tot > 0:
                        wf_hit_rate = _wins / _tot

        # Evidência preditiva independente do patrimônio acumulado: score de
        # abril/N versus retorno abril/N→março/N+1. Segmentos pequenos demais
        # não recebem aprovação por ausência de poder estatístico.
        score_frame = pd.DataFrame(score_rows)
        ic_values: list[float] = []
        # Pares (ano, score, retorno) preservados para o teste no nível do
        # UNIVERSO — onde há amplitude real (N>300) em vez de 78 testes com
        # mediana de 3 empresas. Ver core/b3_pooled_evidence.py e auditoria §16.
        ic_pairs: list[tuple[int, float, float]] = []
        if not score_frame.empty:
            for year in sorted(score_frame["Ano"].unique()):
                scores_year = score_frame[score_frame["Ano"] == year].set_index(
                    "ticker"
                )["Score_Ajustado"]
                start_rows = df_prec_seg[
                    (df_prec_seg.index.year == int(year))
                    & (df_prec_seg.index.month >= _REBAL_MONTH)
                ]
                end_rows = df_prec_seg[
                    (df_prec_seg.index.year == int(year) + 1)
                    & (df_prec_seg.index.month < _REBAL_MONTH)
                ]
                if start_rows.empty or end_rows.empty:
                    continue
                returns_year = (
                    end_rows.iloc[-1] / start_rows.iloc[0] - 1.0
                ).replace([np.inf, -np.inf], np.nan)
                aligned = pd.concat(
                    [scores_year.rename("score"), returns_year.rename("return")],
                    axis=1,
                ).dropna()
                # O universo aproveita TODAS as empresas alinhadas, inclusive as
                # dos anos que o segmento sozinho descarta por amplitude.
                ic_pairs.extend(
                    (int(year), float(s), float(r))
                    for s, r in zip(aligned["score"], aligned["return"]))
                if len(aligned) < 5:
                    continue
                ic = aligned["score"].corr(aligned["return"], method="spearman")
                if pd.notna(ic) and np.isfinite(ic):
                    ic_values.append(float(ic))
        rank_ic_years = len(ic_values)
        if ic_values:
            rank_ic_mean = float(np.mean(ic_values))
            # Significância do SINAL (Rank-IC), não do retorno: t-test unilateral
            # sobre os ICs anuais (H0: IC médio = 0). É macro-neutro e de alta
            # amplitude (Grinold-Kahn) — muito mais robusto que o p-value do
            # retorno de 24m. p_value_ic é a chance de o poder preditivo ser sorte.
            if len(ic_values) >= 2:
                _ic = np.asarray(ic_values, dtype=float)
                _sd = float(_ic.std(ddof=1))
                if _sd > 0:
                    rank_ic_tstat = float(_ic.mean() / (_sd / np.sqrt(len(_ic))))
                    from scipy.stats import t as _tdist
                    p_value_ic = float(_tdist.sf(rank_ic_tstat, df=len(_ic) - 1))
                elif _ic.mean() > 0:
                    rank_ic_tstat = float("inf")
                    p_value_ic = 0.0

    total_lids = sum(len(v) for v in liderancas_hist.values())
    participacao = {
        tk: len(v) / total_lids
        for tk, v in liderancas_hist.items() if v
    } if total_lids > 0 else {}

    # Último ano que cada ticker liderou
    ultimo_lid: dict[str, int] = {
        tk: max(anos) for tk, anos in liderancas_hist.items() if anos
    }

    # Resiliência estrutural (Damodaran): a empresa gera ROIC acima do risco-livre
    # (Selic = piso do custo de capital) de forma CONSISTENTE através dos ciclos?
    # Mede o spread ROIC − Selic ano a ano nas empresas que lideraram o segmento.
    # roic_spread_mean = média do spread; roic_hit_rate = fração de anos com ROIC>Selic.
    roic_spread_mean = float("nan")
    roic_hit_rate = float("nan")
    _lideres_res = [tk for tk, anos in liderancas_hist.items() if anos] or list(tickers)
    _spreads: list[float] = []
    for tk in _lideres_res:
        df_h = hist_batch.get(tk)
        if df_h is None or df_h.empty or "ROIC" not in df_h.columns or "Data" not in df_h.columns:
            continue
        _yr = pd.to_datetime(df_h["Data"], errors="coerce").dt.year
        _rc = pd.to_numeric(df_h["ROIC"], errors="coerce")
        for _y, _r in zip(_yr, _rc):
            if pd.notna(_y) and pd.notna(_r):
                _sel = selic_macro.get(int(_y))
                if _sel is not None and np.isfinite(_r):
                    _spreads.append(float(_r) - float(_sel))
    if _spreads:
        _sp = np.asarray(_spreads, dtype=float)
        roic_spread_mean = float(_sp.mean())
        roic_hit_rate = float((_sp > 0).mean())

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
        "p_value_oos": p_value_oos,
        "n_months_oos": n_months_oos,
        "rank_ic_mean": rank_ic_mean,
        "rank_ic_years": rank_ic_years,
        "rank_ic_tstat": rank_ic_tstat,
        "p_value_ic": p_value_ic,
        # ICs anuais preservados para classificar o ESTADO DA EVIDÊNCIA
        # (a favor / contra / inconclusivo) e calcular o efeito mínimo
        # detectável — ver core/b3_evidence.py e auditoria §16.
        "rank_ic_values": list(ic_values),
        "ic_pairs": ic_pairs,
        "n_empresas_medio": (
            float(score_frame.groupby("Ano")["ticker"].nunique().mean())
            if not score_frame.empty and "Ano" in score_frame.columns else 0.0
        ),
        "roic_spread_mean": roic_spread_mean,
        "roic_hit_rate": roic_hit_rate,
        "wf_hit_rate": wf_hit_rate,
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


def _benjamini_hochberg(p_values: list[float]) -> list[float]:
    """Q-values BH para controlar falsos positivos entre segmentos."""
    if not p_values:
        return []
    clean = np.asarray([
        min(max(float(value), 0.0), 1.0) if np.isfinite(value) else 1.0
        for value in p_values
    ])
    order = np.argsort(clean)
    ranked = clean[order]
    m = len(clean)
    adjusted = ranked * m / np.arange(1, m + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1].clip(0.0, 1.0)
    out = np.empty(m, dtype=float)
    out[order] = adjusted
    return out.tolist()


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
            sub_val += f" · ({m_ew:+.1f}% vs Pesos Iguais)"
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
                df_ano = df_ano.sort_values(
                    ["Score_Ajustado", "ticker"], ascending=[False, True])
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
                "Alfa vs Selic (%)": round(float(p.get("alpha_selic", 0.0)), 1),
                "Alfa vs Pesos Iguais (%)": round(float(p.get("alpha_ew", 0.0)), 1),
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
# PERFIS PRÉ-CONFIGURADOS — protegem contra calibragem que degrada a carteira
# ══════════════════════════════════════════════════════════════════════════════

def _render_perfil_configuracao() -> None:
    """Perfis medidos + alerta quando a configuração tem custo conhecido.

    A aba tem ~20 parâmetros; alguns degradam a carteira sem sinal visível. Os
    perfis vêm da varredura automatizada sobre o universo real, não de gosto —
    cada um declara a evidência que o sustenta (core/b3_portfolio_presets.py).
    """
    from core.b3_portfolio_presets import (
        PERSONALIZADO, PRESETS, RECOMENDADO, avaliar_configuracao,
        identificar_perfil,
    )

    def _aplicar(nome: str):
        def _cb():
            for chave, valor in PRESETS[nome].valores.items():
                st.session_state[chave] = valor
        return _cb

    col_sel, col_btn = st.columns([3, 1])
    escolhido = col_sel.selectbox(
        "Perfil de configuração", list(PRESETS), key="pb3_perfil_escolha",
        help="Combinações cujo efeito foi MEDIDO no universo real. Aplicar um "
             "perfil sobrescreve os parâmetros abaixo; depois disso você pode "
             "ajustar o que quiser — o app avisa se a alteração tiver custo "
             "conhecido.",
    )
    col_btn.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    col_btn.button("Aplicar perfil", key="pb3_aplicar_perfil",
                   on_click=_aplicar(escolhido), use_container_width=True)

    preset = PRESETS[escolhido]
    st.caption(f"**{preset.nome}** — {preset.resumo}")
    with st.expander("Por que estes valores (evidência medida)", expanded=False):
        for evidencia in preset.evidencias:
            st.markdown(f"- {evidencia}")
        if preset.ressalva:
            st.warning(preset.ressalva, icon="⚠️")

    # Estado ATUAL dos controles (não o do perfil escolhido no seletor).
    atual = {chave: st.session_state.get(chave)
             for preset_item in PRESETS.values() for chave in preset_item.valores}
    ativo = identificar_perfil(atual)
    if ativo == PERSONALIZADO:
        st.caption(
            "Configuração atual: **personalizada** (não corresponde a nenhum "
            f"perfil). Para voltar ao padrão, aplique “{RECOMENDADO}”.")
    else:
        st.caption(f"Configuração atual: **{ativo}**.")

    for alerta in avaliar_configuracao(atual):
        st.warning(alerta, icon="⚠️")


# ══════════════════════════════════════════════════════════════════════════════
# SAÚDE DAS SELECIONADAS — cruzamento entre a rota de segmentos e a de valor
# ══════════════════════════════════════════════════════════════════════════════

def _render_saude_da_carteira(tickers: list[str], df_mult_todos: pd.DataFrame,
                              df_set: pd.DataFrame, taxa_selic_aa: float) -> None:
    """Alerta sobre as empresas que a carteira REALMENTE escolheu.

    Lacuna encontrada em 27/07/2026: a rota de segmentos aprovava um nome que a
    rota de valor — na mesma tela — classificava como armadilha potencial
    (payout de 318%, endividamento 3,2×), e nada avisava. Os gates existentes
    são de SEGMENTO (margem vs Selic, resiliência dos líderes históricos) ou de
    score; nenhum olhava solvência e dividendo da empresa escolhida.
    """
    from core.b3_holdings_health import ATENCAO, CRITICO, check_portfolio

    if not tickers or df_mult_todos is None or df_mult_todos.empty:
        return

    mapa_setor: dict[str, str] = {}
    if df_set is not None and not df_set.empty:
        _col_tk = ("ticker" if "ticker" in df_set.columns
                   else ("Ticker" if "Ticker" in df_set.columns else None))
        if _col_tk and "SETOR" in df_set.columns:
            mapa_setor = {str(r[_col_tk]).upper(): r["SETOR"]
                          for _, r in df_set.iterrows()}

    saude = check_portfolio(df_mult_todos, list(tickers), mapa_setor,
                            selic=float(taxa_selic_aa))
    if not saude.holdings:
        return

    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    _sec_hdr("🩺 Saúde das empresas selecionadas")
    st.caption(
        "Verificação no nível da EMPRESA escolhida — solvência, sustentabilidade "
        "do dividendo e retorno vs risco-livre — cruzada com o veredito da rota "
        "de valor. Os demais portões da carteira julgam o segmento, não o nome."
    )

    k1, k2, k3 = st.columns(3)
    with k1:
        card_metrica("Críticos", str(len(saude.criticos)))
    with k2:
        card_metrica("Atenção", str(len(saude.atencao)))
    with k3:
        card_metrica("Cíclicos na carteira", f"{saude.pct_ciclico:.0%}")

    for alerta in saude.alertas:
        st.warning(alerta, icon="⚠️")

    for holding in saude.holdings:
        if holding.nivel == CRITICO:
            st.error(f"**{holding.ticker}** — {holding.resumo}", icon="🚨")
        elif holding.nivel == ATENCAO:
            st.warning(f"**{holding.ticker}** — {holding.resumo}", icon="⚠️")

    if not saude.criticos and not saude.atencao and not saude.alertas:
        st.success("Nenhum alerta de solvência, dividendo ou concentração.",
                   icon="✅")
    else:
        st.caption(
            "Estes alertas NÃO removem ativos automaticamente — a decisão é sua. "
            "Eles existem para que a divergência entre as duas rotas apareça "
            "onde a decisão é tomada, e não escondida numa seção separada."
        )


# ══════════════════════════════════════════════════════════════════════════════
# EVIDÊNCIA NO UNIVERSO — o teste que tem amplitude
# ══════════════════════════════════════════════════════════════════════════════

def _render_evidencia_universo(resultados: list[dict]) -> None:
    """Um teste com N>300 no lugar de 78 testes com mediana de 3 empresas.

    O Rank-IC por segmento responde "este segmento tem sinal?" — pergunta que a
    amostra brasileira não sustenta. Aqui a pergunta é "o meu score ordena
    vencedores acima de perdedores no mercado?", que a mesma base responde com
    amplitude real. As estimativas por segmento aparecem encolhidas em direção
    ao universo, na medida da incerteza de cada uma.
    """
    from core.b3_evidence import A_FAVOR, CONTRA
    from core.b3_pooled_evidence import (
        SegmentSample, pooled_yearly_ics, shrink_segment_estimates,
        universe_evidence,
    )

    todos_pares: list[tuple] = []
    for res in resultados:
        todos_pares.extend(res.get("ic_pairs") or [])
    if not todos_pares:
        return

    ics_universo = pooled_yearly_ics(todos_pares)
    n_medio = (len(todos_pares) / len(ics_universo)) if ics_universo else 0.0
    evidencia = universe_evidence(ics_universo, n_medio_ativos=n_medio)

    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    _sec_hdr("🔬 Evidência no universo — o teste com amplitude")
    st.caption(
        "Testar cada segmento isoladamente é inviável na B3: a mediana é de 3 "
        "empresas por segmento. Este teste agrupa TODAS as empresas de cada ano "
        "num único Rank-IC — mesma base, amplitude real, sem multiplicidade a "
        "corrigir."
    )

    icone = {A_FAVOR: "✅", CONTRA: "❌"}.get(evidencia.estado, "🟡")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        card_metrica("Rank-IC do universo",
                     f"{evidencia.ic_medio:+.3f}"
                     if np.isfinite(evidencia.ic_medio) else "—")
    with c2:
        card_metrica("valor-p",
                     f"{evidencia.p_value:.3f}" if evidencia.p_value is not None else "—")
    with c3:
        card_metrica("Anos × empresas/ano",
                     f"{evidencia.anos} × ~{evidencia.n_medio_ativos:.0f}")
    with c4:
        card_metrica("Efeito mín. detectável",
                     f"{evidencia.efeito_minimo_detectavel:.3f}"
                     if evidencia.efeito_minimo_detectavel is not None else "—")
    st.markdown(f"{icone} {evidencia.explicacao}")

    amostras = [
        SegmentSample(
            key=f'{res["setor"]} · {res["segmento"]}',
            ic_values=tuple(res.get("rank_ic_values") or []),
            n_assets=float(res.get("n_empresas_medio") or 0.0),
        )
        for res in resultados
    ]
    encolhidas = shrink_segment_estimates(
        amostras,
        universe_mean=evidencia.ic_medio if np.isfinite(evidencia.ic_medio) else None,
    )
    if not encolhidas:
        return

    with st.expander(
        "📐 Estimativas por segmento após empréstimo de força (empirical Bayes)",
        expanded=False,
    ):
        st.caption(
            "Cada segmento é puxado em direção à média do universo conforme a "
            "própria incerteza. Segmento com 3 empresas e IC alto é quase "
            "inteiramente encolhido — é ruído; com muitas empresas, preserva o "
            "seu valor. Sem observação, adota a estimativa do universo — que é "
            "a melhor inferência disponível, não uma reprovação."
        )
        tabela = pd.DataFrame([{
            "Segmento": e.key,
            "IC bruto": round(e.ic_bruto, 3) if e.ic_bruto is not None else None,
            "IC encolhido": round(e.ic_encolhido, 3),
            "Peso próprio (%)": round(e.peso_proprio * 100, 0),
            "Anos medidos": e.anos,
        } for e in encolhidas])
        st.dataframe(
            tabela.sort_values("IC encolhido", ascending=False),
            hide_index=True, use_container_width=True,
            height=min(420, 60 + 35 * len(tabela)),
        )
        _sem_dado = sum(1 for e in encolhidas if e.ic_bruto is None)
        if _sem_dado:
            st.caption(
                f"{_sem_dado} segmento(s) sem observação própria receberam a "
                "estimativa do universo."
            )


# ══════════════════════════════════════════════════════════════════════════════
# ROTA DE VALOR — distorção de preço com disciplina de solvência
# ══════════════════════════════════════════════════════════════════════════════

def _render_rota_de_valor(df_mult_todos: pd.DataFrame, df_set: pd.DataFrame,
                          taxa_selic_aa: float) -> None:
    """Caminho paralelo ao dos segmentos, que não depende de amplitude.

    A rota por segmento pergunta "meu processo de escolha tem skill comprovado?"
    — pergunta que exige amplitude cross-seccional, e a B3 tem mediana de 3
    empresas por segmento. Esta seção responde outra: "está barata frente ao
    valor intrínseco E sobrevive para realizar esse valor?". Por isso continua
    útil justamente quando a rota de segmentos fica muda.
    """
    from core.b3_value_route import (
        ARMADILHA, OPORTUNIDADE, SEM_EVIDENCIA, SEM_MARGEM, ValuePolicy,
        blocked_by_missing_data, rank_value_opportunities, route_summary,
    )

    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    _sec_hdr("💎 Rota de valor — distorção com solvência")
    st.caption(
        "Independe de significância estatística e de amplitude: avalia empresa a "
        "empresa. O gate de solvência é o que separa distorção de armadilha de "
        "valor — ~20% das ações brasileiras perderam mais de 90% em 15 anos."
    )

    if df_mult_todos is None or df_mult_todos.empty:
        st.info("Sem fundamentos carregados para avaliar a rota de valor.")
        return

    col1, col2, col3 = st.columns(3)
    margem_min = col1.slider(
        "Margem de segurança mínima (%)", 0, 100, 20, 5, key="pb3_vr_margem",
        help="Desconto exigido frente ao valor intrínseco (média de Graham e "
             "Bazin disponíveis). Não é a margem vs Selic da rota de segmentos.",
    ) / 100.0
    max_div = col2.slider(
        "Endividamento máximo (dívida/PL)", 0.5, 5.0, 2.5, 0.5, key="pb3_vr_div")
    min_liq = col3.slider(
        "Liquidez corrente mínima", 0.5, 3.0, 1.0, 0.1, key="pb3_vr_liq")

    policy = ValuePolicy(margem_minima=margem_min, max_endividamento=max_div,
                         min_liquidez_corrente=min_liq)
    ranked = rank_value_opportunities(df_mult_todos, policy=policy,
                                      selic=float(taxa_selic_aa))
    resumo = route_summary(ranked)

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        card_metrica("Oportunidades", str(resumo[OPORTUNIDADE]))
    with k2:
        card_metrica("Armadilhas barradas", str(resumo[ARMADILHA]))
    with k3:
        card_metrica("Sem desconto", str(resumo[SEM_MARGEM]))
    with k4:
        card_metrica("Sem evidência", str(resumo[SEM_EVIDENCIA]))

    # load_setores devolve a coluna em MINÚSCULO ('ticker'); procurar 'Ticker'
    # fazia o merge falhar em silêncio e Setor/Segmento saíam como None.
    setores = pd.DataFrame(columns=["Ticker", "SETOR", "SEGMENTO"])
    if df_set is not None and not df_set.empty:
        _col_tk = ("ticker" if "ticker" in df_set.columns
                   else ("Ticker" if "Ticker" in df_set.columns else None))
        if _col_tk and {"SETOR", "SEGMENTO"}.issubset(df_set.columns):
            setores = (df_set[[_col_tk, "SETOR", "SEGMENTO"]]
                       .drop_duplicates(_col_tk)
                       .rename(columns={_col_tk: "Ticker"}))

    oportunidades = ranked[ranked["classificacao"] == OPORTUNIDADE]
    if oportunidades.empty:
        st.info(
            "Nenhuma empresa combina o desconto exigido com solvência preservada "
            "nos parâmetros atuais. Diferente da rota de segmentos, aqui a "
            "ausência é sobre PREÇO, não sobre evidência estatística."
        )
    else:
        show = oportunidades.merge(setores, on="Ticker", how="left")
        # A margem é FRAÇÃO (1,97 = 197%). Formatar direto como "%.0f%%" exibia
        # "2%" ao lado de uma explicação dizendo 197% — converter antes.
        show["margem_valor"] = show["margem_valor"] * 100.0
        cols = [c for c in ("Ticker", "SETOR", "SEGMENTO", "margem_valor",
                            "valor_score", "forca_solvencia", "explicacao")
                if c in show.columns]
        st.dataframe(
            show[cols].head(40).rename(columns={
                "SETOR": "Setor", "SEGMENTO": "Segmento",
                "margem_valor": "Margem de segurança",
                "valor_score": "Score de valor",
                "forca_solvencia": "Folga de solvência",
                "explicacao": "Por quê"}),
            hide_index=True, use_container_width=True,
            column_config={
                "Margem de segurança": st.column_config.NumberColumn(format="%.0f%%"),
            },
        )
        st.caption(
            "Score de valor = 60% desconto + 40% folga de solvência. A decisão de "
            "timing continua sua: a rota mostra o que está barato e sobrevive, "
            "não quando comprar."
        )

    armadilhas = ranked[ranked["classificacao"] == ARMADILHA]
    if not armadilhas.empty:
        with st.expander(
            f"🪤 Armadilhas de valor barradas ({len(armadilhas)}) — desconto sem solvência",
            expanded=False,
        ):
            st.caption(
                "Estas apareceriam como 'baratas' em qualquer filtro de múltiplos. "
                "O gate de solvência é justamente o que as separa das oportunidades."
            )
            trap = armadilhas.copy()
            trap["motivo"] = trap["falhas_solvencia"].apply(lambda v: "; ".join(v))
            trap["margem_valor"] = trap["margem_valor"] * 100.0   # fração → %
            st.dataframe(
                trap[["Ticker", "margem_valor", "motivo"]].head(40).rename(columns={
                    "margem_valor": "Desconto aparente", "motivo": "Reprovação"}),
                hide_index=True, use_container_width=True,
                column_config={
                    "Desconto aparente": st.column_config.NumberColumn(format="%.0f%%"),
                },
            )

    bloqueadas = blocked_by_missing_data(ranked, policy=policy)
    if not bloqueadas.empty:
        with st.expander(
            f"🕳️ Teses não julgadas por falta de dado ({len(bloqueadas)})",
            expanded=False,
        ):
            st.caption(
                "Têm desconto relevante, mas falta insumo crítico de solvência — "
                "não foram avaliadas e NÃO são recomendações. A lista mede o custo "
                "da cobertura de fundamentos e serve para priorizar a ingestão."
            )
            faltantes = bloqueadas.copy()
            faltantes["falta"] = faltantes["criticos_ausentes"].apply(
                lambda v: ", ".join(v))
            faltantes["margem_valor"] = faltantes["margem_valor"] * 100.0
            st.dataframe(
                faltantes[["Ticker", "margem_valor", "falta"]].head(40).rename(
                    columns={"margem_valor": "Desconto aparente",
                             "falta": "Dado ausente"}),
                hide_index=True, use_container_width=True,
                column_config={
                    "Desconto aparente": st.column_config.NumberColumn(format="%.0f%%"),
                },
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
        'Usa pontuação (score) com defasagem de publicação = 1 ano, reconstrução '
        'histórica e comparação vs Tesouro Selic e vs carteira de Pesos Iguais '
        '(que distribui igualmente entre as empresas do segmento). A carteira salva aqui segue '
        'para a aba <strong>Avaliação de Portfólio</strong>, que a julga como conjunto.'
        '</p>',
        unsafe_allow_html=True,
    )

    # ── PARÂMETROS ────────────────────────────────────────────────────────────
    with st.expander("⚙️ Parâmetros", expanded=True):
        _render_perfil_configuracao()
        p1, p2, p3, p4 = st.columns(4)
        thr_selic   = p1.number_input(
            "Margem mín. vs Selic · histórico (%)", 0.0, 500.0, 0.0, 5.0,
            key="pb3_thr_selic_hist",
            help="No modo 'Econômico (Brasil)' esta é a RÉGUA de aprovação: a "
                 "estratégia precisa bater a Selic por esta margem no histórico "
                 "(0 = empatar; 20 = ganhar 20% do risco-livre). Nos modos "
                 "estatísticos é apenas diagnóstico.",
        )
        thr_ew      = p2.number_input(
            "Margem mín. vs Pesos Iguais · validação ~24m (%)", 0.0, 300.0, 0.0, 5.0,
            key="pb3_thr_ew_oos24",
            help="Piso de MAGNITUDE do excesso sobre a carteira de Pesos Iguais na "
                 "janela de validação (fora da amostra), aplicado só quando o modo "
                 "ao lado = 'Exigir margem mínima'. A significância estatística vs "
                 "Pesos Iguais já é sempre exigida.",
        )
        uso_ew      = p3.selectbox(
            "Piso de magnitude vs Pesos Iguais",
            ["Apenas significância estatística", "Exigir margem mínima"],
            key="pb3_ew_floor_mode2",
            help="A aprovação SEMPRE exige significância estatística do excesso "
                 "sobre a carteira de Pesos Iguais (habilidade de seleção, neutra "
                 "ao cenário macro). Aqui você decide se, além disso, a margem "
                 "precisa superar o piso ao lado.",
        )
        # Definido aqui (e não depois do bloco de captions) porque as legendas
        # do modo econômico precisam informar se o segundo portão está ativo.
        usar_ew_como_criterio = uso_ew == "Exigir margem mínima"
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
        pb1, pb2, pb3, pb4 = st.columns(4)
        _janela_label = pb1.selectbox(
            "Janela de validação",
            ["~24 meses", "~36 meses", "Walk-forward (todos os ciclos)"],
            key="pb3_janela_val",
            help="Holdout fora da amostra. 24/36 meses = janela recente única. "
                 "Walk-forward = testa o excesso vs pares em TODO o histórico "
                 "point-in-time (vários regimes) — mais robusto, menos dependente "
                 "de um período recente atípico. É o padrão das casas quant.",
        )
        walk_forward = "Walk-forward" in _janela_label
        janela_val_anos = 3 if "36" in _janela_label else 2
        min_empresas_grupo = pb2.number_input(
            "Mín. empresas por grupo", 1, 15, 5, 1,
            key="pb3_min_empresas",
            help="Segmentos com menos empresas elegíveis que este mínimo são "
                 "agrupados no subsetor, para haver massa suficiente para medir o "
                 "poder preditivo (Rank-IC). Use 1 para desativar o agrupamento.",
        )
        pb3.number_input(
            "Teto por setor (%)", 20, 100, 100, 5,
            key="pb3_teto_setor",
            help="Peso máximo somado dos ativos de um MESMO setor B3. "
                 "Segmento não é setor: quatro segmentos podem viver em dois "
                 "setores e num único fator de risco (industrial/commodities). "
                 "FIIs e Empresas Americanas já tinham este teto; a carteira B3 "
                 "não. 100% = desligado. Se a configuração for impossível "
                 "(poucos setores para o teto), o app avisa em vez de violar "
                 "em silêncio.",
        )
        pb3.number_input(
            "Teto para setores cíclicos (%)", 30, 100, 100, 5,
            key="pb3_teto_ciclico",
            help="Peso máximo somado dos ativos de setores CÍCLICOS (materiais "
                 "básicos, bens industriais, consumo cíclico, petróleo e gás). "
                 "Setor e fator são coisas diferentes: limitar cada setor a 30% "
                 "pode deixar a carteira 82% cíclica se quase todos os setores "
                 "forem cíclicos. 100% = desligado. Se não houver nomes "
                 "não-cíclicos suficientes, o app avisa que o gargalo está na "
                 "seleção, não na ponderação.",
        )
        cheapness_pct = pb3.number_input(
            "Peso de barganha no score (%)", 0, 50, 0, 5,
            key="pb3_cheapness",
            help="Mistura múltiplos de preço (P/L, P/VP, EV/EBIT: menor = melhor) "
                 "ao score de qualidade. 0% = só qualidade (padrão). Ex.: 30% dá "
                 "30% de peso a 'estar barato' e 70% à qualidade — captura a tese "
                 "de comprar boas empresas na baixa.",
        )
        cheapness_weight = float(cheapness_pct) / 100.0
        _criterio = pb4.selectbox(
            "Critério de aprovação",
            ["Econômico (Brasil)", "Sinal fundamental (Rank-IC)", "Retorno de 24m (FDR)"],
            key="pb3_criterio_aprov2",
            help="Econômico (Brasil) — RECOMENDADO p/ o mercado local: o gate é a "
                 "margem vs Selic no histórico (critério econômico, robusto a "
                 "amostra pequena); a estatística vira GUARDA-CORPO (só reprova "
                 "sinal claramente anti-preditivo), não exige prova de "
                 "significância. Sinal fundamental (Rank-IC): exige significância "
                 "do sinal — precisa de amplitude, escassa na B3. Retorno de 24m "
                 "(FDR): exige significância do retorno recente — o mais frágil aqui.",
        )
        criterio_modo = (
            "economico" if _criterio.startswith("Econômico") else
            "sinal" if _criterio.startswith("Sinal") else "retorno"
        )
        usar_gate_sinal = criterio_modo == "sinal"
        rc1, rc2 = st.columns([1, 2])
        exigir_resiliencia = rc1.checkbox(
            "Exigir resiliência (ROIC > risco-livre)",
            value=False,
            key="pb3_resiliencia",
            help="Trilha de resiliência estrutural (Damodaran): além do sinal, "
                 "exige que as líderes do segmento gerem ROIC acima da Selic "
                 "(piso do custo de capital) de forma consistente através dos "
                 "ciclos. Filtro ADICIONAL — reduz aprovações, aumenta convicção. "
                 "EFEITO COLATERAL medido em 29/07/2026: o corte por ROIC "
                 "favorece setores de capital leve e penaliza os REGULADOS — só "
                 "20% das empresas de utilidade pública superam a Selic em 5 "
                 "p.p., contra 27% das cíclicas. Ligar este filtro com spread "
                 "alto tende a produzir carteira mais pró-cíclica.",
        )
        thr_roic_spread_pct = rc2.number_input(
            "Spread mín. ROIC − Selic (média, p.p.)", -20.0, 30.0, 0.0, 1.0,
            key="pb3_roic_spread", disabled=not exigir_resiliencia,
            help="Quanto o ROIC médio das líderes precisa superar a Selic, em "
                 "pontos percentuais. 0 = apenas empatar; positivo = criar valor "
                 "acima do risco-livre. Exige também ROIC > Selic na maioria dos anos.",
        )
        thr_roic_spread = float(thr_roic_spread_pct) / 100.0
        lc1, lc2 = st.columns(2)
        _liq_label = lc1.selectbox(
            "Tamanho mínimo (valor de mercado)",
            ["Sem filtro", "≥ R$ 1 bi", "≥ R$ 2 bi", "≥ R$ 5 bi"],
            index=1,
            key="pb3_min_mcap",
            help="Remove micro-caps e nomes sem valor de mercado "
                 "(ilíquidos/deslistados, ex.: EKTR3). Mede TAMANHO — não "
                 "negociabilidade: uma empresa de R$ 3 bi com free-float mínimo "
                 "passa aqui e mesmo assim é impossível de negociar. Para isso "
                 "existe o filtro de volume ao lado.",
        )
        _MCAP_MAP = {"Sem filtro": 0.0, "≥ R$ 1 bi": 1e9, "≥ R$ 2 bi": 2e9, "≥ R$ 5 bi": 5e9}
        min_mcap = _MCAP_MAP[_liq_label]
        _adtv_label = lc2.selectbox(
            "Liquidez mínima (volume negociado · R$/dia)",
            ["Sem filtro", "≥ R$ 500 mil", "≥ R$ 1 mi", "≥ R$ 5 mi", "≥ R$ 20 mi"],
            index=2,
            key="pb3_min_adtv",
            help="Negociabilidade REAL: mediana do volume financeiro diário dos "
                 "últimos 6 meses (mediana, não média — um leilão ou negócio de "
                 "bloco isolado não conta como liquidez). É o filtro que separa "
                 "'empresa grande' de 'ação que dá para comprar e vender sem "
                 "mover o preço'. Sem ele, o modelo coroa classes ilíquidas "
                 "(ex.: a ON de uma holding cuja PN concentra todo o giro).",
        )
        _ADTV_MAP = {
            "Sem filtro": 0.0, "≥ R$ 500 mil": 5e5, "≥ R$ 1 mi": 1e6,
            "≥ R$ 5 mi": 5e6, "≥ R$ 20 mi": 2e7,
        }
        min_adtv = _ADTV_MAP[_adtv_label]
        cap_adaptativo = st.checkbox(
            "Cap adaptativo (relaxa o teto por ativo quando faltam nomes)",
            value=True,
            key="pb3_cap_adaptativo",
            help="Mercado B3 é raso: muitos segmentos têm 2-3 empresas, e um cap "
                 "de 25% exige 4. Ligado, o teto por ativo é relaxado até ficar "
                 "viável (ex.: 2 nomes → até 50% cada), eliminando as 'restrições "
                 "inviáveis'. Desligado, usa o cap fixo (padrão de mercado grande).",
        )
        _quali_ok = quali_gate_disponivel()
        usar_gate_quali = st.checkbox(
            "Parecer qualitativo (LLM) na seleção — veto com substituição",
            value=_quali_ok,
            disabled=not _quali_ok,
            key="pb3_gate_quali",
            help="Para cada líder estatística, monta um DOSSIÊ determinístico "
                 "(fundamentos multi-ano, dividendo recorrente vs extraordinário, "
                 "valuation calculado dos dados brutos, eventos societários CVM e "
                 "red flags de qualidade de dados) e pede um parecer ao LLM. "
                 "Veto = exceção grave e documentada: o nome sai e o PRÓXIMO do "
                 "ranking DO MESMO segmento herda a vaga, com motivo registrado. "
                 "Aplicado DEPOIS da estatística — nunca altera score, FDR ou "
                 "Rank-IC; falha de LLM nunca veta (fail-open). Sem LLM "
                 "configurado, fica desativado.",
        )
        diversificar_corr = st.checkbox(
            "Diversificar por correlação (reduz peso / substitui ativos correlacionados)",
            value=True,
            key="pb3_diversificar_corr",
            help="Cada segmento escolhe seu líder olhando só para os PRÓPRIOS "
                 "fundamentos — nunca para o que os outros segmentos já "
                 "escolheram. Isso produz diversificação nominal: 5 segmentos "
                 "diferentes podem, na prática, compartilhar o mesmo fator de "
                 "risco (ex.: mineração + petroquímica + autopeças = mesmo "
                 "ciclo de commodities). Ligado, a carteira final passa por "
                 "dois ajustes: (1) o par mais correlacionado acima do limiar "
                 "tem o mais fraco substituído pelo próximo do ranking DO "
                 "MESMO segmento, se isso reduzir a correlação média; (2) os "
                 "pesos são reponderados por min-variance (Ledoit-Wolf) — "
                 "ativos que colaboram pouco para a diversificação perdem "
                 "peso mesmo sem serem substituídos. Nunca troca entre "
                 "segmentos distintos: só desempata dentro do próprio "
                 "ranking, como o gate qualitativo já faz.",
        )
        ccol1, ccol2 = st.columns(2)
        corr_threshold = ccol1.slider(
            "Limiar de correlação p/ substituição (ρ)", 0.40, 0.90, 0.65, 0.05,
            key="pb3_corr_threshold", disabled=not diversificar_corr,
            help="Pares com correlação (valor absoluto) acima deste limiar "
                 "disparam a tentativa de substituição do mais fraco dos dois.",
        )
        corr_alpha_pct = ccol2.slider(
            "Peso da nota fundamentalista no reajuste (%)", 0, 100, 60, 5,
            key="pb3_corr_alpha", disabled=not diversificar_corr,
            help="100% = pesos só pela nota/orçamento por segmento (ignora "
                 "correlação). 0% = pesos só por min-variance (ignora a nota, "
                 "minimiza risco). 60% é o meio-termo: dá mais peso a quem "
                 "pontuou melhor, mas encolhe quem carrega risco redundante.",
        )
        corr_alpha = float(corr_alpha_pct) / 100.0
        if criterio_modo == "economico":
            if usar_ew_como_criterio:
                st.caption(
                    "✅ O piso **vs Pesos Iguais** está ativo e é aplicado neste "
                    "modo sobre o histórico cheio — o segmento precisa passar nos "
                    "**dois** exames (Selic **e** 1/N)."
                )
            else:
                st.caption(
                    "⚠️ O piso **vs Pesos Iguais** está desligado: o segmento é "
                    "aprovado só por bater a Selic. Sem esse segundo exame, não há "
                    "garantia de que a seleção supere dividir igualmente entre as "
                    "empresas do próprio segmento."
                )
            st.caption(
                "**Como a aprovação funciona — modo Econômico (Brasil):** um "
                "segmento é aprovado quando **bate o Tesouro Selic no histórico** "
                "pela margem definida ao lado (critério **econômico**, robusto a "
                "amostra pequena e a mercado instável — a evidência acadêmica "
                "mostra que o discricionário/econômico vence nos ciclos adversos "
                "que dominam a B3). A **estatística** vira **guarda-corpo**: só "
                "**reprova** um segmento cujo sinal seja **claramente "
                "anti-preditivo** (Rank-IC < −0,05); ela **NÃO exige** prova de "
                "significância — que, na B3 (poucas empresas, história curta), "
                "reprovaria quase tudo. As colunas de valor-p e Rank-IC ficam como "
                "**diagnóstico**, não como portão."
            )
        else:
            st.caption(
                "**Como a aprovação funciona — modo estatístico:** um segmento é "
                "aprovado quando prova **habilidade de seleção** — supera a "
                "carteira de **Pesos Iguais** do próprio segmento com "
                "**significância estatística** (Benjamini-Hochberg, q ≤ 10%, + "
                "valor-p unilateral) na **janela de validação fora da amostra** — "
                "**e** tem **poder preditivo** consistente (≥ 2 anos de Rank-IC "
                "positivo). É **neutro ao cenário macro** (se o segmento todo caiu, "
                "os Pesos Iguais caíram junto). A **margem vs Selic** é "
                "**diagnóstico** e **não reprova**. *Obs.: exige amplitude "
                "estatística que a B3 raramente tem — se aprovar pouco, use o modo "
                "Econômico (Brasil).*"
            )
        st.caption(
            "📖 **Glossário:** *Pesos Iguais* = carteira que investe igualmente em "
            "todas as empresas do segmento (referência sem escolha). *Fora da "
            "amostra / janela de validação* = os ~24 meses finais, que o modelo não "
            "usou para se montar. *Significância / valor-p* = chance do resultado "
            "ser sorte (quanto menor, melhor). *Falsos positivos "
            "(Benjamini-Hochberg)* = ajuste por testar dezenas de segmentos de uma "
            "vez. *Poder preditivo (Rank-IC)* = o quanto a pontuação de qualidade "
            "acertou o retorno futuro."
        )

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

    # Filtro de LIQUIDEZ (negociabilidade): remove micro-caps e nomes sem valor de
    # mercado (deslistados/dado velho) ANTES de qualquer scoring — senão o modelo
    # coroa empresas boas no papel mas intocáveis na prática (BALM3, CAMB3, EKTR3).
    if min_mcap > 0:
        try:
            _mcaps = _load_market_caps()
        except MarketCapDataError as exc:
            st.error(
                "Não foi possível validar o piso de liquidez de R$ 1 bilhão. "
                "A seleção foi interrompida para não classificar dados ausentes "
                f"como empresas ilíquidas. Detalhe: {exc}."
            )
            return

        _n_antes = df_set["ticker"].nunique()
        _n_cobertos, _n_universo, _cobertura_mcap = _market_cap_coverage(
            df_set["ticker"], _mcaps
        )
        if _n_universo and _cobertura_mcap < _MIN_MARKET_CAP_COVERAGE:
            st.error(
                "Cobertura de valor de mercado insuficiente para aplicar o piso "
                f"de {_liq_label}: {_n_cobertos}/{_n_universo} empresas "
                f"({_cobertura_mcap:.1%}). A seleção foi interrompida; atualize "
                "a ingestão de `marketCap` antes de continuar."
            )
            return

        _keep = df_set["ticker"].map(
            lambda t: _mcaps.get(_ticker_key(t), 0.0) >= min_mcap
        )
        df_set = df_set[_keep].copy()
        _n_removidos = _n_antes - df_set["ticker"].nunique()
        if _n_removidos > 0:
            st.caption(
                f"🔒 Filtro de tamanho ({_liq_label}): {_n_removidos} empresa(s) "
                "removida(s) por valor de mercado abaixo do piso ou ausente "
                "(ilíquidas/deslistadas)."
            )

    # Filtro de NEGOCIABILIDADE (volume financeiro mediano diário). Distinto do
    # filtro de tamanho acima: valor de mercado alto com free-float mínimo
    # continua intocável na prática. Falha de consulta NUNCA é tratada como
    # iliquidez — o filtro é pulado com aviso explícito, porque classificar dado
    # ausente como "ilíquido" derrubaria o universo inteiro em silêncio.
    if min_adtv > 0:
        try:
            _adtvs = _load_adtv()
        except LiquidezDataError as exc:
            _adtvs = {}
            st.warning(
                f"⚠️ Filtro de liquidez ({_adtv_label}) **não aplicado**: não foi "
                f"possível consultar o volume negociado ({exc}). A seleção seguiu "
                "sem esse piso — trate os nomes finais como não verificados quanto "
                "à negociabilidade."
            )
        if _adtvs:
            _n_antes_adtv = df_set["ticker"].nunique()
            _cob_adtv, _tot_adtv, _ratio_adtv = _market_cap_coverage(
                df_set["ticker"], _adtvs
            )
            if _tot_adtv and _ratio_adtv < _MIN_ADTV_COVERAGE:
                st.warning(
                    f"⚠️ Filtro de liquidez ({_adtv_label}) **não aplicado**: "
                    f"cobertura de volume insuficiente ({_cob_adtv}/{_tot_adtv} = "
                    f"{_ratio_adtv:.1%}). Atualize a ingestão de `volume` em "
                    "`market.historical_prices`."
                )
            else:
                _removidos_adtv = sorted(
                    str(t) for t in df_set["ticker"].unique()
                    if _adtvs.get(_ticker_key(t), 0.0) < min_adtv
                )
                df_set = df_set[
                    df_set["ticker"].map(
                        lambda t: _adtvs.get(_ticker_key(t), 0.0) >= min_adtv
                    )
                ].copy()
                _n_rem_adtv = _n_antes_adtv - df_set["ticker"].nunique()
                if _n_rem_adtv > 0:
                    _amostra = ", ".join(_removidos_adtv[:12])
                    _reticencias = "…" if len(_removidos_adtv) > 12 else ""
                    st.caption(
                        f"💧 Filtro de liquidez ({_adtv_label}): {_n_rem_adtv} "
                        f"empresa(s) removida(s) por volume negociado abaixo do "
                        f"piso ou sem série de volume — {_amostra}{_reticencias}"
                    )

    if df_set.empty:
        st.error(
            "Nenhuma empresa sobreviveu aos filtros de tamanho e liquidez. "
            "Afrouxe os pisos acima."
        )
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

    # Gamma/cap/soft calibrados (ou defaults). Definidos FORA do `if rodar:`
    # porque as seções de exibição (líderes do próximo ano) usam `cap`/`gamma`/
    # `soft` a cada rerun, mesmo sem reprocessar — senão dá UnboundLocalError.
    gamma = st.session_state.get("b3_av_gamma", _GAMMA_DEF)
    cap   = st.session_state.get("b3_av_cap",   _CAP_DEF)
    soft  = st.session_state.get("b3_av_soft",  _SOFT_DEF)
    # Teto por setor: 100% = desligado. Lido aqui (fora do `if rodar:`) pelo
    # mesmo motivo dos demais — as seções de exibição usam a cada rerun.
    teto_setor = float(st.session_state.get("pb3_teto_setor", 100)) / 100.0
    teto_ciclico = float(st.session_state.get("pb3_teto_ciclico", 100)) / 100.0

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
            # Scraping descontinuado (2026-07): fonte única = market.* (brapi).
            # include_status=False e fund_data={} fazem a reconciliação virar só
            # saneamento por faixas canônicas (nulo fica nulo).
            df_mult_recon, audit_recon, quality_summary = _recon.batch_multiplos_reconciliados(
                all_tickers,
                df_base=df_mult_todos,
                include_status=False,
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

        resultados: list[dict] = []
        # Fallback de granularidade: segmentos com poucas empresas ELEGÍVEIS
        # (< min_empresas_grupo) são agrupados no subsetor, para haver massa para
        # medir o poder preditivo (Rank-IC). min_empresas_grupo=1 desativa.
        df_set = df_set.copy()
        df_set["_elig"] = df_set["ticker"].map(
            lambda tk: 1 if (tk in hist_batch and (not anos_hist or anos_hist.get(tk, 0) >= int(min_anos_dre))) else 0
        )
        if int(min_empresas_grupo) > 1:
            _elig_por_seg = df_set.groupby(["SETOR", "SUBSETOR", "SEGMENTO"])["_elig"].transform("sum")
            df_set["_SEG_EFF"] = df_set["SEGMENTO"].where(
                _elig_por_seg >= int(min_empresas_grupo),
                df_set["SUBSETOR"].astype(str) + " (agrupado)",
            )
        else:
            df_set["_SEG_EFF"] = df_set["SEGMENTO"]
        grupos = list(df_set.groupby(["SETOR", "SUBSETOR", "_SEG_EFF"]))
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
                    janela_val_anos=int(janela_val_anos),
                    cheapness_weight=float(cheapness_weight),
                    walk_forward=bool(walk_forward),
                    cap_adaptativo=bool(cap_adaptativo),
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
        # Evidencia persistida: a tela continua funcional se o banco estiver
        # indisponivel, mas cada execucao que puder ser gravada recebe manifesto,
        # parametros, versoes e qualidade PIT explicitamente declarada.
        try:
            from core.b3_validation import persist_validation_run
            from core.database import get_engine

            _eng_validation = get_engine()
            if _eng_validation is not None:
                _summary_validation = {
                    "segments_processed": len(resultados),
                    "segments_with_history": sum(
                        1 for r in resultados if int(r.get("meses", 0) or 0) > 0
                    ),
                    "quality_summary": quality_summary,
                    "price_rows": int(len(df_precos_all)),
                }
                _params_validation = {
                    "score_version": SCORE_VERSION,
                    "model_schema_version": MODEL_SCHEMA_VERSION,
                    "approval_mode": criterio_modo,
                    "validation_window": _janela_label,
                    "walk_forward": bool(walk_forward),
                    "start_year": int(ano_inicio),
                    "minimum_income_history_years": int(min_anos_dre),
                    "minimum_companies_group": int(min_empresas_grupo),
                    "cheapness_weight": float(cheapness_weight),
                    "minimum_market_cap": float(min_mcap),
                    "rebalance_month": int(_REBAL_MONTH),
                }
                _run_id = persist_validation_run(
                    engine=_eng_validation,
                    methodology_version=str(MODEL_SCHEMA_VERSION),
                    score_version=SCORE_VERSION,
                    validation_mode=("walk_forward" if walk_forward else _janela_label),
                    input_params=_params_validation,
                    result_summary=_summary_validation,
                    status="completed" if resultados else "blocked",
                    notes="Execucao interativa da Criacao de Portfolio B3.",
                )
                if _run_id:
                    st.session_state["pb3_validation_run_id"] = _run_id
                    st.caption(
                        f"Registro auditavel: `{_run_id[:8]}`. A execucao fica como "
                        "diligencia ate existirem PIT publicado e universo historico "
                        "completo de deslistadas."
                    )
        except Exception:
            # Persistencia e auditoria nunca podem impedir uma analise solicitada.
            pass

    resultados = st.session_state.get("pb3_resultados", [])
    df_set     = st.session_state.get("pb3_df_set", df_set)
    df_precos_all = st.session_state.get("pb3_precos_all", pd.DataFrame())
    quality_summary = st.session_state.get("pb3_quality_summary", {})
    quality_audit = st.session_state.get("pb3_quality_audit", pd.DataFrame())
    hist_audit = st.session_state.get("pb3_hist_audit", pd.DataFrame())
    entry_guard = st.session_state.get("pb3_entry_guard", {})

    # Resultados ficam em session_state e sobrevivem a deploys. Execuções feitas
    # antes do overhaul (holdout OOS + FDR) não têm as chaves _oos e quebrariam o
    # render. Detecta o schema antigo, descarta e pede novo "Rodar".
    if resultados and any("val_est_oos" not in r for r in resultados):
        for _k in (
            "pb3_resultados", "pb3_df_set", "pb3_precos_all",
            "pb3_quality_summary", "pb3_quality_audit", "pb3_hist_audit",
            "pb3_entry_guard", "pb3_entry_guard_df",
        ):
            st.session_state.pop(_k, None)
        st.info(
            "A metodologia foi atualizada (janela de validação fora da amostra + "
            "controle de falsos positivos + poder preditivo Rank-IC). "
            "Clique em **🚀 Rodar Criação de Portfólio** para recalcular com o novo modelo."
        )
        return

    if not resultados:
        st.warning("Nenhum segmento retornou dados suficientes.")
        return

    _render_data_quality_box(quality_summary, quality_audit, hist_audit)

    # Saneamento por scraping (Fundamentus/Status Invest) DESCONTINUADO (2026-07):
    # fundamentos vêm exclusivamente do market.* (brapi) — não há o que sanear
    # por fonte externa; ausência = N/D (rank neutro). O expander foi removido.

    # ── FILTROS DE APROVAÇÃO ──────────────────────────────────────────────────
    ano_atual = pd.Timestamp.now().year

    _q_values = _benjamini_hochberg([
        float(result.get("p_value_oos", 1.0)) for result in resultados
    ])
    for result, q_value in zip(resultados, _q_values):
        result["q_value_oos"] = q_value
        result["fdr_pass"] = q_value <= 0.10

    def _aprovado(res: dict) -> bool:
        ultimo_lid_seg = max(res["ultimo_lid"].values()) if res["ultimo_lid"] else 0
        recente = (ano_atual - 1 - ultimo_lid_seg) <= max_anos_lid

        if criterio_modo == "economico":
            # ── MODO BRASIL: critério ECONÔMICO primário, estatística como
            # guarda-corpo. Calibrado para mercado instável e pobre em dados,
            # onde exigir significância estatística reprova quase tudo (amplitude
            # escassa) e o quant perde a vantagem de "tempos normais" (Abis).
            if res.get("val_est", 0.0) <= 0:
                return False
            # Primário: bater a Selic no HISTÓRICO cheio pela margem (thr_selic).
            m_selic_full = _margem_pct(res.get("val_est", 0.0), res.get("val_selic", 0.0))
            if m_selic_full < thr_selic:
                return False
            # Segundo portão: bater também o ingênuo 1/N (Pesos Iguais). O
            # Livro do Sistema Quant (cap. 9) define a aprovação como DOIS
            # exames — "se a estratégia não bate a renda fixa por uma margem,
            # não vale o risco; se toda a inteligência não supera dividir
            # igualmente, a inteligência é decorativa" — e só aprova quem passa
            # nos dois. O modo econômico lia apenas o primeiro: o controle
            # "Margem mín. vs Pesos Iguais" ficava INERTE na tela, dando ao
            # usuário a impressão de um piso que nunca era testado. Usa a janela
            # cheia para casar com o teste vs Selic acima (mesma janela).
            if usar_ew_como_criterio and res.get("val_ew", 0.0) > 0:
                if _margem_pct(res.get("val_est", 0.0), res.get("val_ew", 0.0)) < thr_ew:
                    return False
            # Guarda-corpo estatístico: reprova só EVIDÊNCIA CONTRA — sinal
            # claramente anti-preditivo. NÃO exige prova positiva de significância.
            _ic = float(res.get("rank_ic_mean", float("nan")))
            if np.isfinite(_ic) and _ic < -0.05:
                return False
        else:
            # ── MODOS ESTATÍSTICOS: exigem poder preditivo POSITIVO + significância.
            if res.get("val_est_oos", 0.0) <= 0:
                return False
            if int(res.get("rank_ic_years", 0)) < 2:
                return False
            if not np.isfinite(float(res.get("rank_ic_mean", float("nan")))):
                return False
            if float(res.get("rank_ic_mean")) <= 0:
                return False
            if usar_gate_sinal:
                # Significância do SINAL (Rank-IC) — Grinold-Kahn, macro-neutra.
                if float(res.get("p_value_ic", 1.0)) >= 0.10:
                    return False
            else:
                # Significância do RETORNO de 24m + correção FDR.
                if not res.get("fdr_pass", False):
                    return False
            # Consistência vs pares (Equal-Weight), piso opcional thr_ew.
            m_ew = _margem_pct(res["val_est_oos"], res["val_ew_oos"])
            if usar_ew_como_criterio and res["val_ew_oos"] > 0 and m_ew < thr_ew:
                return False

        if not recente:
            return False
        # Trilha de resiliência estrutural (Damodaran, opcional em qualquer modo):
        # ROIC acima do risco-livre na média E na maioria dos anos.
        if exigir_resiliencia:
            _rs = float(res.get("roic_spread_mean", float("nan")))
            _hr = float(res.get("roic_hit_rate", float("nan")))
            if not np.isfinite(_rs) or _rs < thr_roic_spread:
                return False
            if np.isfinite(_hr) and _hr < 0.5:
                return False
        return True

    aprovados  = [r for r in resultados if _aprovado(r)]
    reprovados = [r for r in resultados if not _aprovado(r)]

    # ── CUSTO DO FILTRO DE RESILIÊNCIA ───────────────────────────────────────
    # Medido em 29/07/2026 rodando o motor sem navegador: com o filtro
    # DESLIGADO a carteira saiu com 10 nomes e 23% defensivos; ligado a 5 p.p.,
    # caiu para 6 nomes e 83% cíclicos. Não é bug — é o corte por ROIC pesando
    # contra setores regulados (só 20% das utilities superam a Selic em 5 p.p.,
    # contra 27% das cíclicas). O usuário precisa VER esse custo, não deduzi-lo.
    if exigir_resiliencia and resultados:
        from core.b3_holdings_health import classify_cycle

        def _sem_resiliencia(res: dict) -> bool:
            _rs = float(res.get("roic_spread_mean", float("nan")))
            _hr = float(res.get("roic_hit_rate", float("nan")))
            reprova_rs = (not np.isfinite(_rs)) or _rs < thr_roic_spread
            reprova_hr = np.isfinite(_hr) and _hr < 0.5
            return reprova_rs or reprova_hr

        _cortados = [r for r in reprovados if _sem_resiliencia(r)]
        if _cortados:
            _por_classe: dict[str, int] = {}
            for _res in _cortados:
                _classe = classify_cycle(_res.get("setor"))
                _por_classe[_classe] = _por_classe.get(_classe, 0) + 1
            _def = _por_classe.get("defensivo", 0)
            _cic = _por_classe.get("ciclico", 0)
            _detalhe = (f" — {_def} defensivo(s) e {_cic} cíclico(s)"
                        if (_def or _cic) else "")
            st.info(
                f"**Custo do filtro de resiliência**: {len(_cortados)} segmento(s) "
                f"reprovado(s) por ROIC abaixo de Selic + {thr_roic_spread:.0%}"
                f"{_detalhe}. O corte por ROIC penaliza setores REGULADOS "
                "(utilidade pública, saneamento), que têm retorno limitado por "
                "concessão — desligue o filtro para comparar a carteira sem ele.",
                icon="🔎")

    # ── TABELA DE AUDITORIA ───────────────────────────────────────────────────
    _sec_hdr(f"📋 Auditoria de Segmentos — {len(resultados)} analisados · "
             f"{len(aprovados)} aprovados")
    if criterio_modo == "economico":
        _modo_txt = (
            "**Critério: Econômico (Brasil).** A aprovação é ECONÔMICA e medida "
            "no **histórico cheio** — ou seja, **dentro da amostra** (*in-sample*): "
            "a estratégia precisa bater a Selic pela margem configurada e, se o "
            "piso vs Pesos Iguais estiver ligado, também o ingênuo 1/N. É robusto "
            "a amostra pequena e adequado a mercado instável, mas **não é prova "
            "fora da amostra**: parte da margem pode ser ajuste ao passado. A "
            "estatística vira **guarda-corpo** — só reprova sinal claramente "
            "anti-preditivo (Rank-IC < −0,05); **não exige** significância. "
            "⚠️ Para medir quanto do resultado é ajuste ao passado, rode também "
            "em **Sinal fundamental (Rank-IC)** e compare os aprovados."
        )
    elif usar_gate_sinal:
        _modo_txt = (
            "**Critério: Sinal fundamental (Rank-IC).** A aprovação exige que o poder "
            "preditivo (Rank-IC) seja estatisticamente significativo — **valor-p do "
            "sinal ≤ 10%** — medido em muitos anos × nomes (macro-neutro, alto poder). "
            "O valor-p do retorno de 24m vira **diagnóstico** e não reprova."
        )
    else:
        _modo_txt = (
            "**Critério: Retorno de 24m (FDR).** A aprovação exige **q-valor ≤ 10%** "
            "após o controle de falsos positivos de Benjamini-Hochberg sobre o retorno "
            "recente. Mais frágil: penaliza empresas boas num ciclo macro adverso."
        )
    _resil_txt = (
        " **+ Resiliência (Damodaran):** também exige ROIC das líderes acima da "
        "Selic (piso do custo de capital) — na média (spread mínimo configurado) e "
        "em pelo menos metade dos anos — para separar deterioração real de "
        "adversidade conjuntural."
        if exigir_resiliencia else ""
    )
    _wf_txt = (
        " **Validação: walk-forward.** As margens e o valor-p do retorno cobrem "
        "TODO o histórico point-in-time (vários regimes), não só os últimos meses — "
        "e a coluna 'Anos batendo pares' mostra em quantos anos a estratégia "
        "superou o Equal-Weight."
        if walk_forward else ""
    )
    _comum_txt = (
        " Todos os modos exigem liderança recente."
        if criterio_modo == "economico" else
        " Os modos estatísticos exigem ainda pelo menos 2 anos de poder preditivo "
        "(Rank-IC) com média positiva, bater os Pesos Iguais do segmento e "
        "liderança recente; segmentos sem retornos mensais úteis ficam reprovados "
        "por dados insuficientes."
    )
    st.caption(_modo_txt + _comum_txt + _resil_txt + _wf_txt)

    # Descasamento decisão × exibição (corrigido): o modo econômico APROVA pela
    # margem do histórico cheio (val_est vs val_selic), mas a tabela mostrava
    # apenas a margem da janela de validação — números diferentes dos que
    # decidiram, levando a ler "aprovado com 4%" quando o portão viu 22%. Agora
    # as duas janelas aparecem e o sufixo marca qual delas decidiu.
    _decide_full = criterio_modo == "economico"
    _sfx_hist = " ← DECIDE" if _decide_full else " (diagnóstico)"
    _sfx_val  = " (diagnóstico)" if _decide_full else " ← DECIDE"
    _col_selic_hist = f"vs Selic · histórico{_sfx_hist} (%)"
    _col_selic_val  = f"vs Selic · validação{_sfx_val} (%)"
    _col_ew_hist    = f"vs Pesos Iguais · histórico{_sfx_hist} (%)"
    _col_ew_val     = f"vs Pesos Iguais · validação{_sfx_val} (%)"

    # Estado da evidência por segmento (auditoria §16): "não pude medir" e
    # "medi e é ruim" deixam de ser o mesmo rótulo. Só evidência CONTRA é
    # reprovação estatística; inconclusivo é limite do dado, não do ativo.
    from core.b3_evidence import classify_evidence, evidence_label

    rows_tbl: list[dict] = []
    for res in resultados:
        m_s  = _margem_pct(res.get("val_est_oos", 0.0), res.get("val_selic_oos", 0.0))
        m_ew = _margem_pct(res.get("val_est_oos", 0.0), res.get("val_ew_oos", 0.0))
        m_s_full  = _margem_pct(res.get("val_est", 0.0), res.get("val_selic", 0.0))
        m_ew_full = _margem_pct(res.get("val_est", 0.0), res.get("val_ew", 0.0))
        ult  = max(res["ultimo_lid"].values()) if res["ultimo_lid"] else 0
        _verdict = classify_evidence(
            ic_values=res.get("rank_ic_values") or [],
            ic_mean=res.get("rank_ic_mean"),
            p_value=res.get("p_value_ic"),
        )
        # O portão econômico é aferido à parte para não confundir "reprovado por
        # não bater a Selic" com "reprovado pela estatística".
        _economico_ok = (
            res.get("val_est", 0.0) > 0
            and m_s_full >= thr_selic
            and (not usar_ew_como_criterio or res.get("val_ew", 0.0) <= 0
                 or m_ew_full >= thr_ew)
        )
        if _aprovado(res):
            _situacao = "✅ Aprovado"
        elif _verdict.bloqueante:
            _situacao = "❌ Reprovado (evidência contra)"
        elif not _economico_ok:
            _situacao = "❌ Reprovado (critério econômico)"
        else:
            # Passou no econômico e a estatística não teve como falar.
            _situacao = f"🟡 {evidence_label(_verdict)}"
        rows_tbl.append({
            "Setor":     res["setor"],
            "Subsetor":  res["subsetor"],
            "Segmento":  res["segmento"],
            "Situação": _situacao,
            "Estado da evidência": evidence_label(_verdict),
            "Efeito mín. detectável (Rank-IC)": (
                round(_verdict.efeito_minimo_detectavel, 3)
                if _verdict.efeito_minimo_detectavel is not None else None
            ),
            _col_selic_hist: round(m_s_full, 1),
            _col_ew_hist: round(m_ew_full, 1),
            _col_selic_val: round(m_s, 1),
            _col_ew_val: round(m_ew, 1),
            "valor-p (validação)": round(float(res.get("p_value_oos", 1.0)), 4),
            "q-valor (falsos positivos)": round(float(res.get("q_value_oos", 1.0)), 4),
            "Meses de validação": int(res.get("n_months_oos", 0)),
            "Poder preditivo médio (Rank-IC)": (
                round(float(res["rank_ic_mean"]), 3)
                if np.isfinite(float(res.get("rank_ic_mean", float("nan"))))
                else None
            ),
            "valor-p do sinal (Rank-IC)": round(float(res.get("p_value_ic", 1.0)), 4),
            "Anos batendo pares (%)": (
                round(float(res["wf_hit_rate"]) * 100, 0)
                if np.isfinite(float(res.get("wf_hit_rate", float("nan"))))
                else None
            ),
            "ROIC − Selic médio (p.p.)": (
                round(float(res["roic_spread_mean"]) * 100, 1)
                if np.isfinite(float(res.get("roic_spread_mean", float("nan"))))
                else None
            ),
            "Anos ROIC > Selic (%)": (
                round(float(res["roic_hit_rate"]) * 100, 0)
                if np.isfinite(float(res.get("roic_hit_rate", float("nan"))))
                else None
            ),
            "Anos com poder preditivo": int(res.get("rank_ic_years", 0)),
            "Patrimônio total": _fv(res["val_est"]),
            "Últ. liderança": ult or "—",
        })

    df_tbl = pd.DataFrame(rows_tbl)

    def _cor_status(v: str) -> str:
        if "✅" in v:    return "color: #00C896"
        # Inconclusivo é âmbar, não vermelho: não houve reprovação de mérito.
        if "⚠️" in v or "🟡" in v:   return "color: #F6C90E"
        return "color: #FC5C7D"

    st.dataframe(
        df_tbl.style.applymap(_cor_status, subset=["Situação"]),
        use_container_width=True,
        height=min(500, 60 + 35 * len(df_tbl)),
    )
    _n_inconclusivos = sum("🟡" in str(linha["Situação"]) for linha in rows_tbl)
    st.caption(
        "**Como ler a Situação.** ❌ *evidência contra* = o score ordenou ao "
        "contrário do retorno (reprovação de mérito). ❌ *critério econômico* = "
        "não bateu a Selic/Pesos Iguais pela margem. 🟡 *Inconclusivo* = passou "
        "no econômico e a estatística **não teve como falar** — amplitude ou "
        "significância insuficientes. Inconclusivo NÃO é reprovação: não "
        "rejeitar a hipótese nula não prova ausência de habilidade."
        + (f" Nesta rodada, {_n_inconclusivos} segmento(s) ficaram nesse estado."
           if _n_inconclusivos else "")
    )
    st.caption(
        "**Efeito mín. detectável (Rank-IC)** é o menor poder preditivo que o "
        "teste conseguiria enxergar com os dados disponíveis (80% de poder). "
        "Valor alto significa teste cego para efeitos moderados — com mediana de "
        "3 empresas por segmento na B3, é o caso frequente."
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

    # ── EVIDÊNCIA NO UNIVERSO (amplitude real, sem multiplicidade) ───────────
    _render_evidencia_universo(resultados)

    # ── ROTA DE VALOR (independe de habilidade cross-seccional) ──────────────
    _render_rota_de_valor(df_mult_todos, df_set, taxa_selic_aa)

    # ── EMPRESAS LÍDERES PARA O PRÓXIMO ANO ──────────────────────────────────
    st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                unsafe_allow_html=True)
    _sec_hdr(f"📋 Empresas líderes para o próximo ano ({ano_atual})")
    st.caption(f"Apenas segmentos aprovados · Para compra em {ano_atual}")

    # ── Gate qualitativo: pré-avalia as candidatas com barra de progresso ────
    # (as avaliações são cacheadas — reruns e substitutos reaproveitam)
    quali_log: dict = {"vetados": [], "substituicoes": [], "ressalvas": {}}
    _gate_ativo = bool(st.session_state.get("pb3_gate_quali")) and quali_gate_disponivel()
    if _gate_ativo and aprovados:
        _pend: list[str] = []
        for res in aprovados:
            _rp = sorted(res["score_proximo"].items(),
                         key=lambda x: (-float(x[1]), str(x[0])))
            if _rp:
                _pend.append(str(_rp[0][0]))
                _tm = res.get("ticker_maior_part")
                if _tm and str(_tm) != str(_rp[0][0]):
                    _pend.append(str(_tm))
        _pend = [t for t in dict.fromkeys(_pend)
                 if t not in st.session_state.get("pb3_quali_cache", {})]
        if _pend:
            _pg = st.progress(0, text="Parecer qualitativo (LLM) das líderes…")
            for _i, _tk in enumerate(_pend):
                _pg.progress(_i / len(_pend), text=f"Parecer qualitativo: {_tk}…")
                _quali_avaliar_cached(_tk)
            _pg.empty()

    proximos: list[dict] = []
    for res in aprovados:
        score_prox = res["score_proximo"]
        pesos_p    = dict(res["pesos_prox"])  # cópia: o gate pode realocar peso
        part_hist  = res["participacao"]
        ano_ref    = int(res.get("ano_ref_score", ano_atual - 1))
        ranked_prox = sorted(score_prox.items(),
                             key=lambda x: (-float(x[1]), str(x[0])))
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

        if _gate_ativo:
            selecionados = _aplicar_gate_qualitativo(
                selecionados, ranked_prox, entry_guard, pesos_p,
                f"{res['setor']} › {res['segmento']}", quali_log,
            )

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
            _aval_quali = (st.session_state.get("pb3_quali_cache", {}).get(tk)
                           if _gate_ativo else None)
            if _gate_ativo:
                _sub_de = next((s["sai"] for s in quali_log["substituicoes"]
                                if s["entra"] == tk), None)
                if _sub_de:
                    motivos.append(f"Entrou por veto qualitativo a {_sub_de}")
                if tk in quali_log["ressalvas"]:
                    motivos.append("Parecer LLM com ressalva")
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
                "alpha_selic": _margem_pct(res.get("val_est_oos", 0.0), res.get("val_selic_oos", 0.0)),
                "alpha_ew": _margem_pct(res.get("val_est_oos", 0.0), res.get("val_ew_oos", 0.0)),
                "rank_score": _rank_ticker(score_prox, tk),
                "ano_lider": ano_ref if tk == ticker_lider else res.get("ultimo_lid", {}).get(tk),
                "quali": ({"classificacao": _aval_quali.get("classificacao"),
                           "motivo": str(_aval_quali.get("motivo", ""))[:300]}
                          if _aval_quali else None),
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
    proximos_uniq = sorted(mapa_prox.values(),
                           key=lambda x: (-float(x.get("score") or 0.0), str(x["tk"])))

    # ── DIVERSIFICAÇÃO POR CORRELAÇÃO ────────────────────────────────────────
    # A seleção por segmento é decidida sem olhar para os OUTROS segmentos —
    # produz diversificação nominal (segmentos distintos, mesmo fator de risco
    # macro). Aqui, sobre a lista já deduplicada: (1) troca o mais fraco de um
    # par muito correlacionado pelo próximo do ranking do MESMO segmento, se
    # isso reduzir a correlação média; (2) repondera por min-variance
    # (Ledoit-Wolf), encolhendo quem carrega risco redundante mesmo sem sair
    # da carteira.
    from core.b3_correlation_diversification import (
        MIN_OBS_CORRELACAO,
        average_pairwise_correlation,
        correlation_coverage,
        correlation_matrix,
        diversification_index,
        high_correlation_pairs,
        monthly_returns_for,
    )

    corr_log: list[dict] = []
    corr_diag: dict = {"aplicado": False, "motivo_pulado": None}
    if diversificar_corr and proximos_uniq:
        _universo_tickers = [it["tk"] for it in proximos_uniq] + [
            str(tk) for res in aprovados for tk in res["score_proximo"]
        ]
        _returns_universo = monthly_returns_for(df_precos_all, _universo_tickers)
        _cols_antes = [it["tk"] for it in proximos_uniq if it["tk"] in _returns_universo.columns]
        if len(_cols_antes) < 2:
            corr_diag["motivo_pulado"] = "menos de 2 ativos com série de preço disponível"
        else:
            _ok_pares, _tot_pares = correlation_coverage(
                _returns_universo[_cols_antes], MIN_OBS_CORRELACAO
            )
            if _tot_pares and (_ok_pares / _tot_pares) < _MIN_CORR_PAIR_COVERAGE:
                corr_diag["motivo_pulado"] = (
                    f"sobreposição de preço insuficiente entre os ativos "
                    f"({_ok_pares}/{_tot_pares} pares com ≥{MIN_OBS_CORRELACAO} "
                    "meses em comum)"
                )
            else:
                _corr_antes = correlation_matrix(_returns_universo[_cols_antes])
                avg_antes = average_pairwise_correlation(_corr_antes)
                div_antes = diversification_index(
                    {it["tk"]: float(it.get("peso") or 0.0) for it in proximos_uniq}
                )

                proximos_uniq, corr_log = _aplicar_diversificacao_correlacao(
                    proximos_uniq, aprovados, _returns_universo, entry_guard,
                    threshold=corr_threshold,
                )

                _cols_dep = [it["tk"] for it in proximos_uniq if it["tk"] in _returns_universo.columns]
                _corr_depois = (
                    correlation_matrix(_returns_universo[_cols_dep])
                    if len(_cols_dep) >= 2 else pd.DataFrame()
                )
                avg_depois_subst = average_pairwise_correlation(_corr_depois)

                _mk_res = None
                _ret_final = (
                    _returns_universo[_cols_dep].dropna(how="any")
                    if len(_cols_dep) >= 2 else pd.DataFrame()
                )
                if len(_cols_dep) >= 2 and len(_ret_final) >= MIN_OBS_CORRELACAO:
                    try:
                        from core.markowitz import (
                            min_variance_capped, pesos_hibridos_score_markowitz,
                        )
                        _mk_res = min_variance_capped(
                            _cols_dep, _ret_final[_cols_dep].to_numpy(),
                            cap=cap, shrinkage=True,
                        )
                        _score_weights = {
                            it["tk"]: float(it.get("peso") or 0.0) for it in proximos_uniq
                        }
                        _blended = pesos_hibridos_score_markowitz(
                            _score_weights, _mk_res, alpha=corr_alpha, cap=cap,
                        )
                        for item in proximos_uniq:
                            if item["tk"] in _blended:
                                item["peso"] = _blended[item["tk"]]
                    except Exception as exc:
                        corr_diag["reponderacao_pulada"] = (
                            f"otimização min-variance falhou ({type(exc).__name__})"
                        )

                div_depois = diversification_index(
                    {it["tk"]: float(it.get("peso") or 0.0) for it in proximos_uniq}
                )
                corr_diag.update({
                    "aplicado": True,
                    "avg_corr_antes": avg_antes,
                    "avg_corr_apos_substituicao": avg_depois_subst,
                    "div_index_antes": div_antes,
                    "div_index_depois": div_depois,
                    "markowitz_method": _mk_res.method if _mk_res else None,
                    "markowitz_convergiu": _mk_res.converged if _mk_res else None,
                    "pares_alta_correlacao_restantes": (
                        high_correlation_pairs(_corr_depois, corr_threshold)
                        if len(_cols_dep) >= 2 else []
                    ),
                })

    from core.portfolio_constraints import (
        minimum_assets_for_cap,
        project_capped_simplex,
        project_dual_capped,
    )
    _required_global = minimum_assets_for_cap(cap)
    _portfolio_viavel = len(proximos_uniq) >= _required_global
    if proximos_uniq and _portfolio_viavel:
        if not corr_diag.get("aplicado"):
            # Comportamento original: pesos só do orçamento/score, projetados no cap.
            _projected = project_capped_simplex(
                {item["tk"]: float(item.get("peso") or 0.0) for item in proximos_uniq},
                cap,
            )
            for item in proximos_uniq:
                item["peso"] = _projected[item["tk"]]
        elif any(float(it.get("peso") or 0.0) > cap + 1e-6 for it in proximos_uniq):
            # A reponderação por correlação já aplica o cap internamente; isto é
            # só uma salvaguarda para quando ela foi pulada por dados insuficientes.
            _projected = project_capped_simplex(
                {item["tk"]: float(item.get("peso") or 0.0) for item in proximos_uniq},
                cap,
            )
            for item in proximos_uniq:
                item["peso"] = _projected[item["tk"]]

        # ── TETOS DE CONCENTRAÇÃO (setor + classe de ciclo) ───────────────
        # Segmento não é setor, e setor não é fator: quatro segmentos podem
        # viver em dois setores e num único fator de risco. Os dois tetos são
        # aplicados EM CONJUNTO — aplicá-los em sequência fazia o segundo
        # desfazer o primeiro em silêncio (a varredura automatizada de
        # 29/07/2026 flagrou Utilidade Pública a 25,2% com teto de 25%).
        if teto_setor < 1.0 or teto_ciclico < 1.0:
            from core.b3_holdings_health import classify_cycle
            _mapa_setor: dict[str, str] = {}
            if df_set is not None and not df_set.empty and "SETOR" in df_set.columns:
                _ctk = "ticker" if "ticker" in df_set.columns else "Ticker"
                if _ctk in df_set.columns:
                    _mapa_setor = {str(r[_ctk]).upper(): str(r["SETOR"] or "")
                                   for _, r in df_set.iterrows()}
            _pesos_ini = {item["tk"]: float(item.get("peso") or 0.0)
                          for item in proximos_uniq}
            _grupos = {item["tk"]: _mapa_setor.get(str(item["tk"]).upper(), "")
                       for item in proximos_uniq}
            _eh_ciclico = {
                item["tk"]: classify_cycle(
                    _mapa_setor.get(str(item["tk"]).upper(), "")) == "ciclico"
                for item in proximos_uniq
            }
            _pesos_fin, _avisos_tetos = project_dual_capped(
                _pesos_ini, _grupos, _eh_ciclico, cap,
                float(teto_setor), float(teto_ciclico),
            )
            for item in proximos_uniq:
                item["peso"] = _pesos_fin[item["tk"]]
            _n_ciclicos = sum(1 for v in _eh_ciclico.values() if v)
            for _aviso in _avisos_tetos:
                _sufixo = ("" if "classe" not in _aviso.lower() else
                           f" Há {_n_ciclicos} cíclico(s) de "
                           f"{len(proximos_uniq)} ativos — o gargalo está na "
                           "SELEÇÃO, não na ponderação.")
                st.warning(_aviso + _sufixo, icon="⚠️")
    elif proximos_uniq:
        st.error(
            f"Carteira não pode respeitar o cap global de {cap:.0%}: "
            f"há {len(proximos_uniq)} ativo(s), mas são necessários ao menos "
            f"{_required_global}. Os líderes são exibidos para auditoria, "
            "porém o salvamento fica bloqueado."
        )

    _constraint_warnings = sorted({
        warning
        for result in resultados
        for warning in (result.get("constraint_warnings") or [])
    })
    if _constraint_warnings:
        with st.expander(
            f"⚠️ Restrições inviáveis em {len(_constraint_warnings)} combinação(ões)",
            expanded=False,
        ):
            st.caption(
                "Esses casos usaram a carteira de Pesos Iguais apenas dentro do "
                "diagnóstico do segmento. O limite máximo por ativo é reaplicado à "
                "carteira global antes de salvar."
            )
            st.code("\n".join(_constraint_warnings[:100]))

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

    # ── SAÚDE DAS SELECIONADAS (cruza as duas rotas) ─────────────────────────
    _render_saude_da_carteira(
        [item["tk"] for item in proximos_uniq] if proximos_uniq else [],
        df_mult_todos, df_set, taxa_selic_aa,
    )

    # ── TRANSPARÊNCIA DO GATE QUALITATIVO ────────────────────────────────────
    if _gate_ativo:
        st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                    unsafe_allow_html=True)
        _sec_hdr("🧠 Gate qualitativo (parecer LLM) — vetos e substituições")
        st.caption(
            "Aplicado DEPOIS do ranking estatístico: o parecer lê o dossiê "
            "determinístico (fundamentos, dividendos recorrente vs extraordinário, "
            "eventos societários CVM e red flags de dados) e só veta exceção grave "
            "documentada. O substituto é o próximo do ranking do MESMO segmento. "
            "Nada aqui altera score, FDR ou Rank-IC; falha de LLM não veta."
        )
        if not (quali_log["vetados"] or quali_log["substituicoes"]
                or quali_log["ressalvas"]):
            st.markdown("✅ Nenhum veto ou ressalva — todas as líderes "
                        "estatísticas passaram no parecer qualitativo.")
        for v in quali_log["vetados"]:
            st.markdown(f"❌ **{v['tk']}** ({v['segmento']}) — vetada: "
                        f"{v['motivo'] or 'sem motivo declarado'}")
        for s in quali_log["substituicoes"]:
            st.markdown(f"🔁 **{s['entra']}** herda a vaga de **{s['sai']}** "
                        f"({s['segmento']})")
        if quali_log["ressalvas"]:
            with st.expander(
                f"⚠️ Aprovadas com ressalva ({len(quali_log['ressalvas'])})",
                expanded=False,
            ):
                for _tk, _mot in quali_log["ressalvas"].items():
                    st.markdown(f"• **{_tk}**: {_mot}")

    # ── TRANSPARÊNCIA DA DIVERSIFICAÇÃO POR CORRELAÇÃO ───────────────────────
    if diversificar_corr and proximos_uniq:
        st.markdown("<hr style='margin:24px 0;border-color:#1E2533;'>",
                    unsafe_allow_html=True)
        _sec_hdr("🔗 Diversificação por correlação")
        if corr_diag.get("motivo_pulado"):
            st.warning(
                f"⚠️ Diversificação por correlação **não aplicada**: "
                f"{corr_diag['motivo_pulado']}. A carteira segue com os pesos "
                "de orçamento por segmento, sem ajuste de correlação — trate "
                "como não verificada quanto a risco redundante entre segmentos."
            )
        elif corr_diag.get("aplicado"):
            st.caption(
                "Roda DEPOIS da estatística e do parecer qualitativo, sobre a "
                "lista final. Nunca troca ativos entre segmentos distintos — "
                "só desempata dentro do ranking do próprio segmento, como o "
                "gate qualitativo já faz."
            )
            _mcol1, _mcol2, _mcol3 = st.columns(3)
            _mcol1.metric(
                "Correlação média da seleção",
                f"{corr_diag['avg_corr_apos_substituicao']:.2f}",
                delta=f"{corr_diag['avg_corr_apos_substituicao'] - corr_diag['avg_corr_antes']:+.2f}",
                delta_color="inverse",
                help="Média dos pares de correlação (Pearson, retornos "
                     "mensais) entre os ativos finais. Antes da diversificação: "
                     f"{corr_diag['avg_corr_antes']:.2f}.",
            )
            _mcol2.metric(
                "Índice de diversificação (1−HHI)",
                f"{corr_diag['div_index_depois']:.2f}",
                delta=f"{corr_diag['div_index_depois'] - corr_diag['div_index_antes']:+.2f}",
                help="0 = concentrado num único ativo; próximo de 1 = peso "
                     "espalhado. Antes: "
                     f"{corr_diag['div_index_antes']:.2f}.",
            )
            _mk_txt = (
                f"{corr_diag['markowitz_method']} "
                f"({'convergiu' if corr_diag['markowitz_convergiu'] else 'não convergiu — projeção aproximada'})"
                if corr_diag.get("markowitz_method") else "não aplicada"
            )
            _mcol3.metric("Reponderação min-variance", _mk_txt)
            if corr_diag.get("reponderacao_pulada"):
                st.caption(f"⚠️ {corr_diag['reponderacao_pulada']} — pesos ficaram só na substituição.")
            if corr_log:
                for s in corr_log:
                    st.markdown(
                        f"🔁 **{s['entra']}** herda a vaga de **{s['sai']}** "
                        f"({s['segmento']}) — correlação {s['rho']:.2f}"
                    )
            else:
                st.markdown("✅ Nenhuma substituição necessária — nenhum par "
                            f"da seleção passou de ρ={corr_threshold:.2f}.")
            _pares_restantes = corr_diag.get("pares_alta_correlacao_restantes") or []
            if _pares_restantes:
                with st.expander(
                    f"⚠️ Pares ainda correlacionados após o ajuste ({len(_pares_restantes)})",
                    expanded=False,
                ):
                    st.caption(
                        "Sem substituto viável no próprio segmento (ou a troca não "
                        "reduzia a correlação média). O peso desses ativos já foi "
                        "reduzido pela reponderação min-variance, mas o risco "
                        "permanece — considere não comprar os dois juntos."
                    )
                    for tk_a, tk_b, rho in _pares_restantes:
                        st.markdown(f"• **{tk_a}** × **{tk_b}**: ρ = {rho:.2f}")

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
            "global_cap": float(cap),
            "segment_selection_fdr": 0.10,
            "validation_policy": "holdout_24m_bh_fdr",
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
            "qualitative_gate": bool(_gate_ativo),
            "quali_vetados": quali_log["vetados"],
            "quali_substituicoes": quali_log["substituicoes"],
            "correlation_diversification": bool(diversificar_corr),
            "correlation_threshold": float(corr_threshold),
            "correlation_score_alpha": float(corr_alpha),
            "correlation_substituicoes": corr_log,
        }
        metrics_modelo = {
            "num_empresas": len(proximos_uniq),
            "alpha_selic_medio": float(np.mean([p.get("alpha_selic", 0.0) for p in proximos_uniq])),
            "alpha_ew_medio": float(np.mean([p.get("alpha_ew", 0.0) for p in proximos_uniq])),
            "score_medio": float(np.mean([p.get("score", 0.0) for p in proximos_uniq])),
            "score_entrada_medio": float(np.mean([p.get("score_entrada", 0.0) for p in proximos_uniq])),
            "q_value_max_aprovados": float(max(
                (r.get("q_value_oos", 1.0) for r in aprovados),
                default=1.0,
            )),
            "correlacao_media_final": corr_diag.get("avg_corr_apos_substituicao"),
            "indice_diversificacao_final": corr_diag.get("div_index_depois"),
        }
        c_save, c_info = st.columns([1, 3], gap="medium")
        with c_save:
            if st.button(
                "Salvar portfólio padrão",
                type="primary",
                key="pb3_save_model",
                disabled=not _portfolio_viavel,
            ):
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

    # Carteira final exposta para auditoria automatizada (scripts/audit_portfolio_b3.py
    # varre configurações e verifica invariantes). Só leitura — nada aqui muda a
    # carteira; a exposição é o que permite testar o motor sem navegador.
    st.session_state["pb3_carteira_final"] = [
        {"tk": item.get("tk"), "peso": float(item.get("peso") or 0.0)}
        for item in (proximos_uniq or [])
    ]

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

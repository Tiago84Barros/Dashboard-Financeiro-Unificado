"""
core/resilience_score.py — Resiliência histórica, saúde financeira e
desconto de valuation relativo ao próprio histórico.

Três ajustes complementares ao sistema de score para capturar empresas
de qualidade temporariamente deprimidas por fatores macro/políticos —
problema estrutural em mercados emergentes como o Brasil.

────────────────────────────────────────────────────────────────────────
A — Bônus de reversão à média histórica (historical_reversion_adj)

   Problema: empresa com ROE de 18% que historicamente entregou 24%
   é penalizada no score atual como se fosse estruturalmente fraca,
   quando pode estar sofrendo um headwind temporário (câmbio, juro, ciclo).

   Solução: se o indicador atual está abaixo da própria média histórica
   MAS acima da mediana setorial, a empresa recebe um bônus proporcional
   ao gap, sinalando oportunidade de reversão à média.

   Condição de segurança: z < -2.0 (mais de 2σ abaixo da própria média)
   NÃO recebe bônus — pode ser deterioração estrutural, não temporária.

────────────────────────────────────────────────────────────────────────
B — Penalidade de saúde financeira (financial_health_penalty)

   Problema: o score mistura "vai sobreviver à crise?" com "vai crescer
   depois?". Empresa com Endividamento_Total > 85% e Margem_Liquida < 0
   pode ter ROE histórico alto — mas está em risco de sobrevivência.

   Solução: penalidade separada baseada em métricas de balanço, sem
   mexer nos pesos dos indicadores de qualidade/crescimento. Aplica-se
   antes do macro_adj para não double-count o ambiente de juros.

────────────────────────────────────────────────────────────────────────
C — Bônus de valuation vs histórico próprio (valuation_history_adj)

   Problema: P/VP = 1.2× pode ser barato para um banco que historicamente
   negocia a 2.0× ou caro para outro que negocia a 0.8×. O percentil
   intra-setor capta parcialmente isso, mas não usa o histórico próprio.

   Solução: se P/VP (ou EV/EBIT) está ≥ 0.5σ abaixo da própria média
   histórica E os fundamentais (ROE, Margem) não colapsaram (z > -2.0),
   a empresa recebe um bônus de "desconto implícito vs. valor intrínseco
   estimado". Captura o timing de compra que Graham chamaria de
   margem de segurança.

Referências:
   - De Bondt & Thaler (1985) "Does the Stock Market Overreact?", JF 40(3)
   - Lakonishok, Shleifer & Vishny (1994) "Contrarian Investment", JF 49(5)
   - Altman & Hotchkiss (2006) "Corporate Financial Distress and Bankruptcy"
   - Fama & French (1992) "The Cross-Section of Expected Stock Returns"
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── Constantes de calibração ──────────────────────────────────────────────

# Indicadores de qualidade usados para ajuste A e verificação de C
_QUAL_COLS = ("ROE", "ROIC", "Margem_Liquida", "Margem_Operacional")

# Indicadores de valuation usados para C (melhor quando baixo)
_VAL_COLS  = ("P/VP", "EV_EBIT")

# Zona de reversão: entre -0.3σ e -2.0σ abaixo da própria média
_Z_LOWER  = -2.0   # abaixo disso → possível deterioração estrutural
_Z_UPPER  = -0.3   # acima disso → não está realmente deprimido

# Zona de segurança para verificação de fundamentais em C
_Z_FUND_MIN = -2.0  # abaixo disso → fundamentais colapsaram → sem bônus


# ── A — Bônus de reversão à média histórica ───────────────────────────────

def historical_reversion_adj(
    df:            pd.DataFrame,
    df_hist_batch: dict[str, pd.DataFrame],
    group_col:     str | None,
    indicator_cols: tuple[str, ...] = _QUAL_COLS,
    max_bonus:     float = 0.08,
    min_hist_obs:  int   = 4,
) -> pd.Series:
    """
    Bônus [0, max_bonus] para empresas com indicadores temporariamente
    abaixo da própria média histórica mas acima da mediana setorial.

    Args:
      df:             DataFrame com colunas de indicadores e 'Ticker'
      df_hist_batch:  {ticker: DataFrame histórico}
      group_col:      coluna de agrupamento setorial
      indicator_cols: colunas a analisar
      max_bonus:      bônus máximo como fração do score médio (e.g. 0.08 = 8%)
      min_hist_obs:   mínimo de observações históricas para calcular z-score

    Returns:
      Series de bônus [0, max_bonus] indexada como df.
    """
    bonus = pd.Series(0.0, index=df.index)

    # Mediana setorial para cada coluna (referência "acima do grupo")
    sector_median: dict[str, pd.Series] = {}
    if group_col and group_col in df.columns:
        for col in indicator_cols:
            if col in df.columns:
                sector_median[col] = df.groupby(group_col)[col].transform("median")

    cols_avail = [c for c in indicator_cols if c in df.columns]
    if not cols_avail or not df_hist_batch:
        return bonus

    for i, row in df.iterrows():
        tk    = row.get("Ticker")
        df_h  = df_hist_batch.get(tk)
        if df_h is None or df_h.empty:
            continue

        col_bonuses: list[float] = []
        for col in cols_avail:
            if col not in df_h.columns:
                continue

            hist = pd.to_numeric(df_h[col], errors="coerce").dropna()
            if len(hist) < min_hist_obs:
                continue

            hist_mean = float(hist.mean())
            hist_std  = float(hist.std(ddof=1))
            if hist_std < 1e-6 or abs(hist_mean) < 1e-9:
                continue

            current = pd.to_numeric(row.get(col), errors="coerce")
            if pd.isna(current):
                continue

            z = (current - hist_mean) / hist_std

            # Zona de reversão: (-2.0, -0.3)
            if not (_Z_LOWER < z < _Z_UPPER):
                continue  # fora da zona — ou não está deprimido ou é deterioração

            # Verifica se está acima da mediana setorial (qualidade relativa ok)
            if col in sector_median:
                sect_med = sector_median[col].get(i, np.nan)
                if pd.notna(sect_med) and current < sect_med:
                    continue  # abaixo do próprio histórico E abaixo do setor → não é oportunidade

            # Bônus proporcional à profundidade do gap (0 em z=-0.3, 1 em z=-2.0)
            depth = min((-z - 0.3) / 1.7, 1.0)
            col_bonuses.append(depth * max_bonus)

        if col_bonuses:
            bonus.at[i] = float(np.mean(col_bonuses))

    return bonus.clip(0.0, max_bonus)


# ── B — Penalidade de saúde financeira ────────────────────────────────────

def financial_health_penalty(
    df: pd.DataFrame,
    max_penalty:             float = 0.30,
    lc_critical:             float = 0.50,   # Liquidez_Corrente crítica
    lc_warning:              float = 0.80,   # Liquidez_Corrente em alerta
    et_critical:             float = 0.85,   # Endividamento_Total crítico
    et_warning:              float = 0.70,   # Endividamento_Total em alerta
    margem_critica:          float = 0.0,    # Margem_Liquida negativa + ET alto = risco
) -> pd.Series:
    """
    Penalidade [0, max_penalty] baseada em risco de sobrevivência financeira.

    Separa "a empresa vai sobreviver à crise?" de "ela vai crescer depois?".
    Aplica-se como multiplicador: score_raw *= (1 - penalty).

    Gradação:
      - Liquidez_Corrente < 0.50  → penalidade alta (pode não honrar CP)
      - Liquidez_Corrente < 0.80  → penalidade moderada
      - Endividamento_Total > 0.85 → penalidade alta
      - Endividamento_Total > 0.70 → penalidade moderada
      - ET > 0.80 E Margem < 0    → penalidade extra (dupla fragilidade)

    Returns:
      Series de penalidades [0, max_penalty] indexada como df.
    """
    pen = pd.Series(0.0, index=df.index)

    lc_col = "Liquidez_Corrente"
    et_col = "Endividamento_Total"
    ml_col = "Margem_Liquida"

    has_lc = lc_col in df.columns
    has_et = et_col in df.columns
    has_ml = ml_col in df.columns

    if not has_lc and not has_et:
        return pen  # sem dados de balanço

    for i, row in df.iterrows():
        p = 0.0

        if has_lc:
            lc = pd.to_numeric(row.get(lc_col), errors="coerce")
            if pd.notna(lc):
                if lc < lc_critical:
                    p += 0.18
                elif lc < lc_warning:
                    p += 0.07

        if has_et:
            et = pd.to_numeric(row.get(et_col), errors="coerce")
            if pd.notna(et):
                if et > et_critical:
                    p += 0.18
                elif et > et_warning:
                    p += 0.07

                # Dupla fragilidade: dívida alta + margens negativas
                if et > 0.80 and has_ml:
                    ml = pd.to_numeric(row.get(ml_col), errors="coerce")
                    if pd.notna(ml) and ml < margem_critica:
                        p += 0.12  # risco de sobrevivência real

        pen.at[i] = min(p, max_penalty)

    return pen


# ── C — Bônus de valuation vs histórico próprio ───────────────────────────

def valuation_history_adj(
    df:            pd.DataFrame,
    df_hist_batch: dict[str, pd.DataFrame],
    val_cols:      tuple[str, ...] = _VAL_COLS,
    qual_cols:     tuple[str, ...] = ("ROE", "Margem_Liquida"),
    max_bonus:     float = 0.06,
    z_val_thresh:  float = -0.50,  # valuation deve estar ≥ 0.5σ abaixo da própria média
    z_qual_floor:  float = -2.00,  # fundamentais não podem ter colapsado
    min_hist_obs:  int   = 4,
) -> pd.Series:
    """
    Bônus [0, max_bonus] para empresas com valuation historicamente baixo
    sem deterioração de fundamentais.

    Captura o "timing de Graham": empresa de qualidade comprovada que
    o mercado está negociando abaixo do próprio histórico por razões
    temporárias (macro, política, setor em desgraça momentânea).

    Condições para bônus:
      1. P/VP (ou EV_EBIT) atual está ≤ z_val_thresh abaixo da própria média
      2. ROE e Margem_Liquida não colapsaram (z > z_qual_floor)
      3. Pelo menos min_hist_obs observações históricas disponíveis

    Returns:
      Series de bônus [0, max_bonus] indexada como df.
    """
    bonus = pd.Series(0.0, index=df.index)

    cols_val  = [c for c in val_cols  if c in df.columns]
    cols_qual = [c for c in qual_cols if c in df.columns]

    if not cols_val or not df_hist_batch:
        return bonus

    for i, row in df.iterrows():
        tk   = row.get("Ticker")
        df_h = df_hist_batch.get(tk)
        if df_h is None or df_h.empty:
            continue

        # ── Verifica se fundamentais estão íntegros (condição 2) ──────────
        fundamentals_ok = True
        for qc in cols_qual:
            if qc not in df_h.columns:
                continue
            hist_q = pd.to_numeric(df_h[qc], errors="coerce").dropna()
            if len(hist_q) < min_hist_obs:
                continue
            hm = float(hist_q.mean())
            hs = float(hist_q.std(ddof=1))
            if hs < 1e-6 or abs(hm) < 1e-9:
                continue
            curr_q = pd.to_numeric(row.get(qc), errors="coerce")
            if pd.isna(curr_q):
                continue
            z_q = (curr_q - hm) / hs
            if z_q < z_qual_floor:
                fundamentals_ok = False
                break

        if not fundamentals_ok:
            continue

        # ── Calcula bônus de valuation ─────────────────────────────────────
        col_bonuses: list[float] = []
        for vc in cols_val:
            if vc not in df_h.columns:
                continue
            hist_v = pd.to_numeric(df_h[vc], errors="coerce").dropna()
            # Remove outliers extremos do histórico de valuation
            hist_v = hist_v[
                (hist_v > hist_v.quantile(0.05)) &
                (hist_v < hist_v.quantile(0.95))
            ]
            if len(hist_v) < min_hist_obs:
                continue
            hm_v = float(hist_v.mean())
            hs_v = float(hist_v.std(ddof=1))
            if hs_v < 1e-6:
                continue
            curr_v = pd.to_numeric(row.get(vc), errors="coerce")
            if pd.isna(curr_v) or curr_v <= 0:
                continue
            z_v = (curr_v - hm_v) / hs_v
            # P/VP e EV_EBIT: menor = mais barato → z negativo = desconto
            if z_v >= z_val_thresh:
                continue  # não está com desconto significativo vs. próprio histórico

            # Bônus proporcional ao desconto (0 em z=thresh, max em z=-2.5)
            depth = min((-z_v + z_val_thresh) / 2.0, 1.0)
            col_bonuses.append(depth * max_bonus)

        if col_bonuses:
            bonus.at[i] = float(np.mean(col_bonuses))

    return bonus.clip(0.0, max_bonus)


# ── Diagnóstico / UI helper ───────────────────────────────────────────────

def resilience_summary(
    df:            pd.DataFrame,
    df_hist_batch: dict[str, pd.DataFrame] | None,
    group_col:     str | None,
) -> pd.DataFrame:
    """
    Retorna DataFrame com colunas de diagnóstico por ticker:
      hist_bonus_pct  — bônus de reversão histórica (%)
      health_penalty_pct — penalidade de saúde financeira (%)
      val_bonus_pct   — bônus de valuation vs histórico (%)
      net_adj_pct     — ajuste líquido ao score (pontos percentuais)
      saude_label     — rótulo semáforo: 🟢 Saudável / 🟡 Alerta / 🔴 Risco
    """
    result = df[["Ticker"]].copy()

    if df_hist_batch:
        hb = historical_reversion_adj(df, df_hist_batch, group_col)
        vb = valuation_history_adj(df, df_hist_batch)
    else:
        hb = pd.Series(0.0, index=df.index)
        vb = pd.Series(0.0, index=df.index)

    hp = financial_health_penalty(df)

    result["hist_bonus_pct"]    = (hb * 100).round(1)
    result["health_penalty_pct"] = (hp * 100).round(1)
    result["val_bonus_pct"]     = (vb * 100).round(1)
    result["net_adj_pct"]       = ((hb + vb - hp) * 100).round(1)

    def _label(row) -> str:
        pen = row["health_penalty_pct"]
        if pen >= 20:
            return "🔴 Risco"
        if pen >= 8:
            return "🟡 Alerta"
        return "🟢 Saudável"

    result["saude_label"] = result.apply(_label, axis=1)
    return result.reset_index(drop=True)

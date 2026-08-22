"""
core/valuation.py — Preço justo e sustentabilidade de dividendos.

Adiciona à seção de portfólio respostas a duas perguntas que o score
relativo (percentil intra-setor) não responde:

  1. "Esta ação está cara ou barata em termos ABSOLUTOS?"
     → Margem de segurança via Graham (lucro) e Bazin (dividendos).

  2. "O dividendo pago é SUSTENTÁVEL nos próximos anos?"
     → Payout ratio + cobertura por Fluxo de Caixa Operacional (FCO).

────────────────────────────────────────────────────────────────────────
PREÇO JUSTO — GRAHAM (foco em lucro/patrimônio)

  Número de Graham:  VJ = √(22.5 × LPA × VPA)
  onde 22.5 = 15 (P/L máx) × 1.5 (P/VP máx) — Graham (1949).

  Como só temos múltiplos (P/L e P/VP), e LPA = Preço/(P/L),
  VPA = Preço/(P/VP):

     VJ = Preço × √( 22.5 / (P/L × P/VP) )

  Margem de segurança (independe do preço absoluto):

     MS_graham = √( 22.5 / (P/L × P/VP) ) − 1

  Positiva → ação negocia abaixo do valor justo de Graham.
  Requer P/L > 0 e P/VP > 0 (empresa lucrativa com patrimônio positivo).

────────────────────────────────────────────────────────────────────────
PREÇO JUSTO — BAZIN (foco em dividendos / o "Décio Bazin")

  Preço-teto = Dividendo_por_ação / yield_desejado
  Com yield_desejado = 6% (padrão Bazin para o investidor de renda).

  Como Dividendo_por_ação = DY × Preço:

     Preço-teto = DY × Preço / 0.06

  Margem de segurança:

     MS_bazin = DY / yield_desejado − 1

  Positiva → o dividend yield atual supera o yield-alvo (ação "barata"
  para quem investe em renda).

────────────────────────────────────────────────────────────────────────
SUSTENTABILIDADE DO DIVIDENDO

  Não basta um DY alto — ele precisa ser pagável de forma recorrente.
  Sinais de alerta:
    • Payout > 100%  → distribui mais do que lucra (insustentável)
    • Payout > 80%   → pouca margem para reinvestir/absorver choque
    • FCO negativo   → dividendo pago com dívida ou caixa, não geração
    • DY muito alto (> 15%) com payout alto → possível "dividend trap"

Referências:
  - Graham, B. (1949) "The Intelligent Investor"
  - Bazin, D. (s/d) "Faça Fortuna com Ações, Antes que Seja Tarde"
  - Damodaran, A. — sustentabilidade de payout e FCFE
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Constantes Graham/Bazin
_GRAHAM_K        = 22.5    # 15 × 1.5
_BAZIN_YIELD_DEF = 0.06    # yield-alvo padrão do método Bazin (6%)

# Limiares de sustentabilidade de dividendo
_PAYOUT_FOLGA      = 0.60   # abaixo → muito sustentável
_PAYOUT_OK         = 0.80   # abaixo → sustentável
_PAYOUT_APERTADO   = 1.00   # abaixo → apertado; acima → insustentável
_DY_TRAP           = 0.15   # DY acima disso + payout alto → suspeita de trap


def _norm_fraction(v, max_decimal: float) -> float:
    """Normaliza valor para fração decimal.

    O banco armazena DY/Payout em decimal (0.08 = 8%), mas o fallback web
    pode trazer escala percentual (8.5). Se |v| excede o máximo plausível
    em decimal, assume que veio em percentual e divide por 100.
    """
    v = pd.to_numeric(v, errors="coerce")
    if pd.isna(v):
        return float("nan")
    return float(v / 100.0 if abs(v) > max_decimal else v)


# ──────────────────────────────────────────────────────────────────────────
# Margem de segurança (preço justo relativo, sem preço absoluto)
# ──────────────────────────────────────────────────────────────────────────

def graham_margin(pl: float, pvp: float) -> float:
    """
    Margem de segurança de Graham: √(22.5/(P/L·P/VP)) − 1.

    > 0 → preço abaixo do valor justo de Graham (desconto).
    NaN se P/L ou P/VP ausentes/não positivos (Graham não se aplica).
    """
    pl  = pd.to_numeric(pl,  errors="coerce")
    pvp = pd.to_numeric(pvp, errors="coerce")
    if pd.isna(pl) or pd.isna(pvp) or pl <= 0 or pvp <= 0:
        return float("nan")
    return float(np.sqrt(_GRAHAM_K / (pl * pvp)) - 1.0)


def bazin_margin(dy: float, yield_alvo: float = _BAZIN_YIELD_DEF) -> float:
    """
    Margem de segurança de Bazin: DY/yield_alvo − 1.

    > 0 → DY atual supera o yield-alvo (barata para renda).
    NaN se DY ausente/negativo. DY esperado em fração (0.08 = 8%).
    """
    dy = pd.to_numeric(dy, errors="coerce")
    if pd.isna(dy) or dy < 0:
        return float("nan")
    if yield_alvo <= 0:
        return float("nan")
    return float(dy / yield_alvo - 1.0)


def graham_fair_price(price: float, pl: float, pvp: float) -> float:
    """Preço justo absoluto de Graham = Preço × √(22.5/(P/L·P/VP))."""
    ms = graham_margin(pl, pvp)
    price = pd.to_numeric(price, errors="coerce")
    if pd.isna(ms) or pd.isna(price) or price <= 0:
        return float("nan")
    return float(price * (1.0 + ms))


def bazin_fair_price(price: float, dy: float,
                     yield_alvo: float = _BAZIN_YIELD_DEF) -> float:
    """Preço-teto de Bazin = (DY × Preço) / yield_alvo."""
    ms = bazin_margin(dy, yield_alvo)
    price = pd.to_numeric(price, errors="coerce")
    if pd.isna(ms) or pd.isna(price) or price <= 0:
        return float("nan")
    return float(price * (1.0 + ms))


# ──────────────────────────────────────────────────────────────────────────
# Sustentabilidade do dividendo
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class DividendHealth:
    """Diagnóstico de sustentabilidade do dividendo."""
    score:  float    # 0-100 (maior = mais sustentável)
    label:  str      # 🟢 / 🟡 / 🔴 + texto
    motivo: str      # razão principal


def dividend_sustainability(
    payout:  float,
    dy:      float,
    p_fco:   float | None = None,
) -> DividendHealth:
    """
    Avalia se o dividendo é sustentável.

    Args:
      payout: payout ratio (fração; 0.7 = 70% do lucro distribuído)
      dy:     dividend yield (fração; 0.08 = 8%)
      p_fco:  Preço/FCO. Positivo → FCO positivo (gera caixa). Negativo
              → FCO negativo (dividendo não vem de geração operacional).

    Returns:
      DividendHealth com score 0-100, rótulo semáforo e motivo.
    """
    payout = pd.to_numeric(payout, errors="coerce")
    dy     = pd.to_numeric(dy,     errors="coerce")
    p_fco  = pd.to_numeric(p_fco,  errors="coerce") if p_fco is not None else float("nan")

    # Sem dado de payout: não há como avaliar sustentabilidade
    if pd.isna(payout):
        return DividendHealth(50.0, "⚪ Sem dado", "Payout indisponível")

    # Payout negativo → empresa teve prejuízo mas distribuiu (reservas/dívida)
    if payout < 0:
        return DividendHealth(
            15.0, "🔴 Insustentável",
            "Payout negativo: distribuiu com prejuízo no exercício",
        )

    # Payout acima de 100% → distribui mais do que lucra
    if payout > _PAYOUT_APERTADO:
        score = max(10.0, 35.0 - (payout - 1.0) * 50.0)
        return DividendHealth(
            float(score), "🔴 Insustentável",
            f"Payout {payout*100:.0f}% > 100% — distribui mais do que lucra",
        )

    # FCO negativo é red flag mesmo com payout ok
    fco_neg = (not pd.isna(p_fco)) and p_fco < 0
    if fco_neg:
        return DividendHealth(
            30.0, "🔴 Frágil",
            "FCO negativo — dividendo não sustentado por geração de caixa",
        )

    # Possível dividend trap: DY altíssimo + payout apertado
    if (not pd.isna(dy)) and dy > _DY_TRAP and payout > _PAYOUT_OK:
        return DividendHealth(
            35.0, "🟡 Atenção (trap?)",
            f"DY {dy*100:.0f}% muito alto + payout {payout*100:.0f}% — "
            "possível dividend trap",
        )

    # Faixas saudáveis
    if payout <= _PAYOUT_FOLGA:
        return DividendHealth(
            90.0, "🟢 Sustentável",
            f"Payout {payout*100:.0f}% com folga para reinvestir",
        )
    if payout <= _PAYOUT_OK:
        return DividendHealth(
            75.0, "🟢 Sustentável",
            f"Payout {payout*100:.0f}% saudável",
        )
    # entre 80% e 100%
    return DividendHealth(
        55.0, "🟡 Apertado",
        f"Payout {payout*100:.0f}% — pouca margem para choques",
    )


# ──────────────────────────────────────────────────────────────────────────
# Tabela consolidada para UI
# ──────────────────────────────────────────────────────────────────────────

def valuation_table(
    df: pd.DataFrame,
    bazin_yield_alvo: float = _BAZIN_YIELD_DEF,
) -> pd.DataFrame:
    """
    Constrói DataFrame de valuation por ticker a partir dos múltiplos.

    Espera colunas: Ticker, P/L, P/VP, DY, Payout, P_FCO (as ausentes
    viram NaN). DY e Payout em fração (0.08 = 8%).

    Returns:
      DataFrame com: Ticker, MS Graham (%), MS Bazin (%), Veredito Preço,
      Sustentab. Dividendo, Score Sust., Motivo.
    """
    rows = []
    for _, r in df.iterrows():
        tk    = r.get("Ticker")
        pl    = r.get("P/L")
        pvp   = r.get("P/VP")
        dy    = _norm_fraction(r.get("DY"),     max_decimal=1.0)   # DY>100% impossível
        payt  = _norm_fraction(r.get("Payout"), max_decimal=5.0)   # payout pode até 500%
        pfco  = r.get("P_FCO")

        ms_g = graham_margin(pl, pvp)
        ms_b = bazin_margin(dy, bazin_yield_alvo)
        health = dividend_sustainability(payt, dy, pfco)

        # Veredito de preço combinando Graham + Bazin
        sinais = [m for m in (ms_g, ms_b) if not pd.isna(m)]
        if not sinais:
            veredito = "⚪ Sem dado"
        else:
            media = float(np.mean(sinais))
            if media >= 0.30:
                veredito = "🟢 Desconto forte"
            elif media >= 0.05:
                veredito = "🟢 Desconto"
            elif media >= -0.15:
                veredito = "🟡 Preço justo"
            else:
                veredito = "🔴 Caro"

        rows.append({
            "Ticker":               tk,
            "MS Graham (%)":        round(ms_g * 100, 1) if not pd.isna(ms_g) else np.nan,
            "MS Bazin (%)":         round(ms_b * 100, 1) if not pd.isna(ms_b) else np.nan,
            "Veredito Preço":       veredito,
            "Sustentab. Dividendo": health.label,
            "Score Sust.":          round(health.score, 0),
            "Motivo":               health.motivo,
        })

    return pd.DataFrame(rows)

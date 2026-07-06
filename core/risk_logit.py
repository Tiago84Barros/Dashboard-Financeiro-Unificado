"""
core/risk_logit.py -- índice heurístico de risco financeiro para o score B3.

Os coeficientes padrão são definidos por regra de negócio, não ajustados em
uma base rotulada de distress. A transformação logística produz um índice
limitado em [0, 1], mas ele não deve ser interpretado como probabilidade
empírica de falência até que o modelo seja calibrado e validado.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import exp
from typing import Mapping

import pandas as pd


@dataclass(frozen=True)
class RiskLogitModel:
    """Calibratable logistic model.

    Coefficients operate on bounded risk features in [0, 1]. The intercept is
    set so a company with neutral inputs lands near a low single-digit distress
    probability, while stacked red flags rise non-linearly.
    """

    intercept: float = -3.75
    negative_roe: float = 1.55
    high_debt: float = 1.45
    negative_margin: float = 1.20
    low_liquidity: float = 1.00
    expensive_pvp: float = 0.45
    poor_cash_quality: float = 0.75
    weak_roic: float = 0.65


DEFAULT_MODEL = RiskLogitModel()


def _bounded_feature(series: pd.Series, lower: float, upper: float) -> pd.Series:
    """Map a numeric series from [lower, upper] into [0, 1]."""
    values = pd.to_numeric(series, errors="coerce")
    if upper <= lower:
        return pd.Series(0.0, index=series.index)
    return ((values - lower) / (upper - lower)).clip(0.0, 1.0).fillna(0.0)


def distress_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build bounded distress features from canonical B3 indicators."""
    features = pd.DataFrame(index=df.index)

    roe = pd.to_numeric(df.get("ROE"), errors="coerce") if "ROE" in df else pd.Series(index=df.index, dtype=float)
    features["negative_roe"] = ((-roe).clip(0.0, 0.30) / 0.30).fillna(0.0)

    debt = pd.to_numeric(df.get("Endividamento_Total"), errors="coerce") if "Endividamento_Total" in df else pd.Series(index=df.index, dtype=float)
    features["high_debt"] = _bounded_feature(debt, 3.0, 12.0)

    margin = pd.to_numeric(df.get("Margem_Liquida"), errors="coerce") if "Margem_Liquida" in df else pd.Series(index=df.index, dtype=float)
    features["negative_margin"] = ((-margin).clip(0.0, 0.25) / 0.25).fillna(0.0)

    liquidity = pd.to_numeric(df.get("Liquidez_Corrente"), errors="coerce") if "Liquidez_Corrente" in df else pd.Series(index=df.index, dtype=float)
    features["low_liquidity"] = ((0.8 - liquidity).clip(0.0, 0.8) / 0.8).fillna(0.0)

    pvp = pd.to_numeric(df.get("P/VP"), errors="coerce") if "P/VP" in df else pd.Series(index=df.index, dtype=float)
    features["expensive_pvp"] = _bounded_feature(pvp, 8.0, 30.0)

    pfco = pd.to_numeric(df.get("P_FCO"), errors="coerce") if "P_FCO" in df else pd.Series(index=df.index, dtype=float)
    features["poor_cash_quality"] = _bounded_feature(pfco, 30.0, 120.0)

    roic = pd.to_numeric(df.get("ROIC"), errors="coerce") if "ROIC" in df else pd.Series(index=df.index, dtype=float)
    features["weak_roic"] = ((0.02 - roic).clip(0.0, 0.22) / 0.22).fillna(0.0)

    return features.clip(0.0, 1.0)


def predict_distress_probability(
    df: pd.DataFrame,
    model: RiskLogitModel = DEFAULT_MODEL,
) -> pd.Series:
    """Retorna índice logístico heurístico em [0, 1] (não calibrado)."""
    features = distress_features(df)
    coefs: Mapping[str, float] = {
        "negative_roe": model.negative_roe,
        "high_debt": model.high_debt,
        "negative_margin": model.negative_margin,
        "low_liquidity": model.low_liquidity,
        "expensive_pvp": model.expensive_pvp,
        "poor_cash_quality": model.poor_cash_quality,
        "weak_roic": model.weak_roic,
    }
    z = pd.Series(model.intercept, index=df.index, dtype=float)
    for name, coef in coefs.items():
        z = z + features[name] * coef
    return z.map(lambda x: 1.0 / (1.0 + exp(-float(x)))).clip(0.0, 1.0)


def risk_penalty_from_probability(probability: pd.Series, max_penalty: float = 20.0) -> pd.Series:
    """Convert distress probability to score penalty with smooth saturation."""
    p = pd.to_numeric(probability, errors="coerce").clip(0.0, 1.0).fillna(0.0)
    penalty = max_penalty * (p ** 0.70)
    return penalty.clip(0.0, max_penalty).round(1)


def distress_risk_score(df: pd.DataFrame) -> pd.DataFrame:
    """Return risk probability, penalty and dominant driver per row."""
    features = distress_features(df)
    prob = predict_distress_probability(df)
    penalty = risk_penalty_from_probability(prob)
    drivers = features.idxmax(axis=1).where(features.max(axis=1) > 0, "none")
    return pd.DataFrame({
        "risk_probability": (prob * 100.0).round(1),
        "r_penalty": penalty,
        "risk_driver": drivers,
        "risk_model_calibrated": False,
    }, index=df.index)

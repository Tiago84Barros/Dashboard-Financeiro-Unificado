"""Cálculos puros do laboratório de Análise Avançada das empresas dos EUA."""
from __future__ import annotations

import numpy as np
import pandas as pd


TRACK_COLUMNS = {
    "quality": "score_quality",
    "growth": "score_growth",
    "solidity": "score_solidity",
    "capital_efficiency": "score_capital_efficiency",
    "valuation": "score_valuation",
    "shareholder": "score_shareholder",
}

DEFAULT_WEIGHTS = {
    "quality": .22,
    "growth": .18,
    "solidity": .15,
    "capital_efficiency": .15,
    "valuation": .18,
    "shareholder": .12,
}


def normalize_weights(weights: dict[str, float] | None = None) -> dict[str, float]:
    raw = dict(DEFAULT_WEIGHTS)
    if weights:
        raw.update({key: max(float(value), 0.0) for key, value in weights.items()
                    if key in raw})
    total = sum(raw.values())
    return ({key: value / total for key, value in raw.items()}
            if total > 0 else dict(DEFAULT_WEIGHTS))


def _percentile(values: pd.Series, higher: bool = True) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(50.0, index=numeric.index)
    if len(valid) >= 4:
        numeric = numeric.clip(valid.quantile(.05), valid.quantile(.95))
    score = numeric.rank(pct=True, method="average") * 100
    if not higher:
        score = 100 - score
    return score.fillna(50.0).clip(0, 100)


def build_entry_scores(scored: pd.DataFrame,
                       weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Repondera as trilhas e calcula score de entrada + penalidades de risco."""
    if scored is None or scored.empty:
        return pd.DataFrame()
    out = scored.copy().reset_index(drop=True)
    w = normalize_weights(weights)

    def series(column: str, default: float = 50.0) -> pd.Series:
        if column not in out:
            return pd.Series(default, index=out.index, dtype=float)
        return pd.to_numeric(out[column], errors="coerce").fillna(default)

    components = []
    for track, column in TRACK_COLUMNS.items():
        values = series(column)
        components.append(values * w[track])
    out["score_base_adv"] = sum(components).clip(0, 100).round(1)

    cash_parts = []
    if "fcf_margin" in out:
        cash_parts.append(_percentile(out["fcf_margin"], True))
    if "cash_conversion" in out:
        cash_parts.append(_percentile(out["cash_conversion"], True))
    if "fcf_yield" in out:
        cash_parts.append(_percentile(out["fcf_yield"], True))
    out["cash_quality"] = (
        pd.concat(cash_parts, axis=1).mean(axis=1).round(1)
        if cash_parts else 50.0
    )

    penalty = pd.Series(0.0, index=out.index)
    risk_driver = pd.Series("sem alerta crítico", index=out.index, dtype=object)

    def penalize(column: str, mask, points: float, label: str) -> None:
        nonlocal penalty, risk_driver
        if column not in out:
            return
        active = mask(pd.to_numeric(out[column], errors="coerce")).fillna(False)
        penalty.loc[active] += points
        risk_driver.loc[active] = np.where(
            risk_driver.loc[active].eq("sem alerta crítico"), label,
            risk_driver.loc[active].astype(str) + "; " + label,
        )

    penalize("net_debt_ebitda", lambda s: s > 4, 10, "dívida líquida/EBITDA elevada")
    penalize("current_ratio", lambda s: s < .8, 7, "liquidez corrente baixa")
    penalize("net_margin", lambda s: s < 0, 8, "margem líquida negativa")
    penalize("fcf_yield", lambda s: s < 0, 5, "fluxo de caixa livre negativo")
    penalize("interest_coverage", lambda s: s < 1.5, 8, "baixa cobertura de juros")

    # ── Motores avançados que existiam mas nunca chegavam à carteira ────────
    # Altman e Piotroski eram calculados para TODAS as empresas e gravados na
    # vitrine, porém só apareciam na análise individual. 597 empresas ativas
    # (21%) estão na zona de aflição do Altman e isso não tocava a seleção.
    #
    # Peso 8 é deliberado: sozinho NÃO exclui (corte em 10), mas somado a outro
    # alerta exclui. O Z-Score foi calibrado em indústrias de 1968 e classifica
    # mal empresas asset-light; tratá-lo como veto isolado reprovaria boas
    # empresas de tecnologia. Bancos, seguradoras e REITs ficam de fora — o
    # modelo não se aplica a esses balanços.
    _setor = out["sector"].astype(str) if "sector" in out else pd.Series("", index=out.index)
    _altman_aplicavel = ~_setor.isin(["Financial Services", "Real Estate"])
    if "z_zone" in out:
        _aflicao = out["z_zone"].astype(str).eq("aflição") & _altman_aplicavel
        penalty.loc[_aflicao] += 8
        risk_driver.loc[_aflicao] = np.where(
            risk_driver.loc[_aflicao].eq("sem alerta crítico"),
            "Altman Z em zona de aflição",
            risk_driver.loc[_aflicao].astype(str) + "; Altman Z em zona de aflição")
    # Payout acima de 1,5× o lucro não se sustenta (mesma calibração do B3,
    # onde UNIP6 distribuía 318%). REITs ficam de fora: distribuem FFO por
    # exigência legal e a depreciação deprime o lucro contábil — payout > 1 ali
    # é estrutural, não alerta.
    if "payout_ratio" in out:
        _reit = (out["is_reit"].astype(bool) if "is_reit" in out
                 else pd.Series(False, index=out.index))
        _payout_alto = (
            (pd.to_numeric(out["payout_ratio"], errors="coerce") > 1.5)
            & ~_reit & ~_setor.isin(["Real Estate"])
        ).fillna(False)
        penalty.loc[_payout_alto] += 7
        risk_driver.loc[_payout_alto] = np.where(
            risk_driver.loc[_payout_alto].eq("sem alerta crítico"),
            "payout acima de 1,5× o lucro",
            risk_driver.loc[_payout_alto].astype(str) + "; payout acima de 1,5× o lucro")

    # Piotroski ≤ 3 de 9 é fraqueza fundamentalista ampla (cobertura ~100%).
    # Só conta quando houve critérios suficientes avaliados — ausência não pune.
    if "f_score" in out:
        _f = pd.to_numeric(out["f_score"], errors="coerce")
        _aval = (pd.to_numeric(out["f_evaluable"], errors="coerce")
                 if "f_evaluable" in out else pd.Series(9, index=out.index))
        _fraco = (_f <= 3) & (_aval >= 6)
        _fraco = _fraco.fillna(False)
        penalty.loc[_fraco] += 6
        risk_driver.loc[_fraco] = np.where(
            risk_driver.loc[_fraco].eq("sem alerta crítico"),
            "Piotroski fraco (≤3 de 9)",
            risk_driver.loc[_fraco].astype(str) + "; Piotroski fraco (≤3 de 9)")
    out["risk_penalty"] = penalty.clip(0, 25).round(1)
    out["risk_driver"] = risk_driver

    quality = series("score_quality")
    growth = series("score_growth")
    out["entry_score"] = (
        out["score_base_adv"] * .60 + quality * .20 + growth * .10
        + out["cash_quality"] * .10 - out["risk_penalty"]
    ).clip(0, 100).round(1)
    out["entry_status"] = np.select(
        [(out["risk_penalty"] >= 10) | (out["entry_score"] < 30),
         out["entry_score"] >= 60],
        ["Excluída", "Aprovada"], default="Observação",
    )
    return out.sort_values(["entry_score", "score_base_adv"], ascending=False).reset_index(drop=True)


def factor_contributions(row: pd.Series,
                         weights: dict[str, float] | None = None) -> pd.DataFrame:
    """Contribuição aditiva de cada trilha em relação ao ponto neutro (50)."""
    w = normalize_weights(weights)
    labels = {
        "quality": "Qualidade", "growth": "Crescimento", "solidity": "Solidez",
        "capital_efficiency": "Eficiência de capital", "valuation": "Avaliação",
        "shareholder": "Retorno ao acionista",
    }
    records = []
    for track, column in TRACK_COLUMNS.items():
        value = float(row.get(column, 50) or 50)
        records.append({"Trilha": labels[track], "Pontuação": value,
                        "Peso (%)": w[track] * 100,
                        "Contribuição": (value - 50) * w[track]})
    return pd.DataFrame(records)


def bootstrap_track_score(row: pd.Series, weights: dict[str, float] | None = None,
                          n: int = 1000, seed: int = 42) -> dict:
    """Bootstrap das trilhas observadas para medir sensibilidade da nota."""
    w = normalize_weights(weights)
    values, probabilities = [], []
    for track, column in TRACK_COLUMNS.items():
        value = row.get(column)
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            values.append(value)
            probabilities.append(w[track])
    if not values:
        return {"mean": None, "p05": None, "p95": None, "std": None, "n": 0}
    probs = np.asarray(probabilities, dtype=float)
    probs /= probs.sum()
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(max(int(n), 100), len(values)),
                         replace=True, p=probs).mean(axis=1)
    return {"mean": float(samples.mean()), "p05": float(np.quantile(samples, .05)),
            "p95": float(np.quantile(samples, .95)), "std": float(samples.std()),
            "n": int(len(samples))}

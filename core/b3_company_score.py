"""Score em seis trilhas para o painel individual de empresas da B3.

O módulo é deliberadamente puro: recebe um corte transversal já reconciliado,
faz winsorização + percentil entre pares e devolve score/cobertura. A tela cuida
apenas da busca de dados e da apresentação.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTOR_TRACKS: dict[str, list[tuple[str, bool]]] = {
    "quality": [
        ("ROE", True), ("ROA", True), ("Margem_Liquida", True),
        ("Margem_Operacional", True),
    ],
    "growth": [
        ("ROE_slope_log", True), ("ROIC_slope_log", True),
        ("Margem_Liquida_slope_log", True),
        ("Margem_Operacional_slope_log", True),
    ],
    "solidity": [
        ("Endividamento_Total", False), ("Liquidez_Corrente", True),
    ],
    # Só ROIC, como no motor americano. ROE estava aqui E em quality, o que
    # lhe dava peso 0,22/4 + 0,15/2 = 0,13 — mais que qualquer outra métrica
    # isolada, sem que a metodologia em lugar nenhum dissesse isso. Além da
    # contagem dupla, ROE é alavancado: dívida infla o retorno sobre o
    # patrimônio sem melhorar a eficiência do capital, que é justamente o que
    # ROIC mede. Manter os dois na mesma trilha premiava alavancagem duas
    # vezes (achado A-102).
    "capital_efficiency": [("ROIC", True)],
    # Ranqueados pelo YIELD recíproco (1/múltiplo), por isso "maior é melhor".
    # Ver _numeric_metric e _RECIPROCO (achado A-101).
    "valuation": [
        ("P/L", True), ("P/VP", True), ("EV_EBIT", True), ("P_FCO", True),
    ],
    "shareholder": [("DY", True), ("Payout", True)],
}

DEFAULT_TRACK_WEIGHTS: dict[str, float] = {
    "quality": 0.22,
    "growth": 0.18,
    "solidity": 0.15,
    "capital_efficiency": 0.15,
    "valuation": 0.18,
    "shareholder": 0.12,
}

TRACK_LABELS: dict[str, str] = {
    "score_quality": "Qualidade",
    "score_growth": "Crescimento",
    "score_solidity": "Solidez",
    "score_capital_efficiency": "Efic. Capital",
    "score_valuation": "Avaliação",
    "score_shareholder": "Retorno ao acionista",
}

# Múltiplos convertidos em yield antes de ranquear. Descartar o múltiplo
# negativo (o que se fazia antes) transformava prejuízo em ausência, e ausência
# vale o neutro 0,5 — a deficitária terminava na mediana da trilha, ou seja,
# mais barata que metade do universo. O recíproco é monótono através do zero:
# lucro/preço negativo ranqueia abaixo de qualquer lucro/preço positivo.
_RECIPROCO = {"P/L", "P/VP", "EV_EBIT", "P_FCO"}
_NEUTRAL = 0.5

# A fonte da B3 apaga o múltiplo negativo em vez de gravá-lo, então o prejuízo
# chega aqui como AUSÊNCIA — e ausência vale o neutro. Medido na vitrine em
# 23/08/2026: das 79 empresas que deram prejuízo, 76 estavam sem P/L, e as
# deficitárias terminavam a trilha com 53,4 contra 48,8 do resto. Ou seja,
# ranqueadas como mais baratas que quem deu lucro — o mesmo defeito do A-101,
# por outra porta, e que o recíproco sozinho não alcança porque o número
# negativo nunca chega ao ranqueador.
#
# A coluna de margem sobrevive ao apagamento e diz o SINAL, ainda que não diga a
# magnitude. Sinal basta para ordenar: rendimento negativo vai ao fundo da
# trilha. Não há proxy confiável para P/VP (ROE negativo tanto pode ser prejuízo
# com patrimônio positivo quanto o contrário) nem para P_FCO, então esses dois
# seguem virando ausência — limitação conhecida (achado A-105).
_SINAL_DO_DENOMINADOR = {"P/L": "Margem_Liquida", "EV_EBIT": "Margem_Operacional"}


def _piso_para_prejuizo_apagado(df: pd.DataFrame, metric: str,
                                yields: pd.Series) -> pd.Series:
    """Põe no fundo da trilha o yield que a fonte apagou por ser negativo."""
    coluna = _SINAL_DO_DENOMINADOR.get(metric)
    if coluna is None or coluna not in df.columns:
        return yields
    negativo = pd.to_numeric(df[coluna], errors="coerce") < 0
    apagado = yields.isna() & negativo
    if not apagado.any():
        return yields
    validos = yields.replace([np.inf, -np.inf], np.nan).dropna()
    # Magnitude é desconhecida; só a posição é conhecida. Um valor abaixo do
    # mínimo observado empata todos no piso depois da winsorização, que é
    # exatamente o que a evidência sustenta — nem mais, nem menos.
    piso = (float(validos.min()) if not validos.empty else 0.0) - 1.0
    return yields.mask(apagado, piso)


def _numeric_metric(df: pd.DataFrame, metric: str) -> pd.Series:
    values = (pd.to_numeric(df[metric], errors="coerce")
              if metric in df.columns else pd.Series(np.nan, index=df.index))
    if metric in _RECIPROCO:
        # Múltiplo zero não informa preço; vira ausência em vez de infinito.
        values = 1.0 / values.where(values != 0)
        values = _piso_para_prejuizo_apagado(df, metric, values)
    return values.replace([np.inf, -np.inf], np.nan)


def _winsorized_percentile(values: pd.Series, *, higher_is_better: bool) -> pd.Series:
    valid = values.dropna()
    if valid.empty:
        return pd.Series(_NEUTRAL, index=values.index, dtype=float)
    if len(valid) >= 4:
        lo, hi = valid.quantile(0.05), valid.quantile(0.95)
        values = values.clip(lower=lo, upper=hi)
    ranked = values.rank(pct=True, method="average")
    if not higher_is_better:
        ranked = 1.0 - ranked
    return ranked.fillna(_NEUTRAL).clip(0.0, 1.0)


def score_cross_section(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula score e cobertura das seis trilhas para cada ticker.

    O DataFrame deve estar previamente limitado ao grupo comparável (segmento,
    subsetor, setor ou universo). Ausência recebe 50 pontos e é explicitada na
    cobertura; portanto um dado faltante nunca é confundido com desempenho ruim.
    """
    if df is None or df.empty or "Ticker" not in df.columns:
        return pd.DataFrame(columns=["Ticker", "score", "coverage"])

    base = df.copy().reset_index(drop=True)
    ranked: dict[str, pd.Series] = {}
    observed: dict[str, pd.Series] = {}
    all_metrics = {metric for track in FACTOR_TRACKS.values() for metric, _ in track}
    for metric in all_metrics:
        values = _numeric_metric(base, metric)
        direction = next(
            higher for track in FACTOR_TRACKS.values()
            for name, higher in track if name == metric
        )
        ranked[metric] = _winsorized_percentile(values, higher_is_better=direction)
        observed[metric] = values.notna()

    result = base.copy()
    track_scores: dict[str, pd.Series] = {}
    for track, metrics in FACTOR_TRACKS.items():
        names = [name for name, _ in metrics]
        score = pd.concat([ranked[name] for name in names], axis=1).mean(axis=1)
        coverage = pd.concat([observed[name] for name in names], axis=1).mean(axis=1)
        # Trilha esparsa não pode produzir convicção extrema. Antes, uma trilha
        # apurada sobre uma única métrica de duas rendia os mesmos 90 pontos que
        # uma apurada sobre as duas, e a diferença ficava só na coluna de
        # cobertura — que a tela mostra ao lado, não dentro da nota. A nota é
        # encolhida para o neutro conforme a raiz da cobertura observada, o mesmo
        # mecanismo que o motor americano já usava (achado A-103). Cobertura
        # cheia não muda nada; cobertura zero devolve o neutro, como antes.
        score = _NEUTRAL + (score - _NEUTRAL) * coverage.pow(0.5)
        track_scores[track] = score
        result[f"score_{track}"] = (score * 100).round(1)
        result[f"coverage_{track}"] = (coverage * 100).round(0)

    result["score"] = (
        sum(track_scores[name] * weight for name, weight in DEFAULT_TRACK_WEIGHTS.items())
        * 100
    ).round(1)
    result["coverage"] = (
        pd.concat([observed[name] for name in sorted(all_metrics)], axis=1)
        .mean(axis=1) * 100
    ).round(0)
    return result.sort_values("score", ascending=False).reset_index(drop=True)


def classification(score: object) -> tuple[str, str]:
    """Rótulo e tipo visual compatíveis com ``badge_status``."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "Sem classificação", "neutro"
    if not np.isfinite(value):
        return "Sem classificação", "neutro"
    if value >= 75:
        return "Excelente", "sucesso"
    if value >= 65:
        return "Forte", "info"
    if value >= 50:
        return "Neutra", "neutro"
    if value >= 35:
        return "Fraca", "alerta"
    return "Crítica", "erro"

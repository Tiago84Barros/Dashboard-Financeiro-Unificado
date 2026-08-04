"""Regime macroeconômico dos EUA para a inteligência de Empresas Americanas.

O módulo é puro: recebe observações já disponíveis e devolve um diagnóstico
determinístico. A interface não consulta a rede; valores oficiais devem entrar
pelo pipeline/warehouse (FRED/BEA/BLS/Federal Reserve) ou por uma simulação
explicitamente identificada como tal.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import isfinite


# Procedência de cada observação macro. Existe porque o relatório institucional
# NÃO pode apresentar premissa como fato: um analista que escreve "com o Fed em
# 4,25%" a partir de um literal de código está afirmando algo que não verificou.
FONTE_PREMISSA = "premissa"      # valor de partida do código ou digitado na tela
FONTE_OBSERVADO = "observado"    # série oficial ingerida no warehouse (FRED)


@dataclass(frozen=True)
class USMacroSnapshot:
    """Fotografia macro. Os defaults são PREMISSAS, não leitura de mercado.

    ``fonte`` e ``as_of`` viajam com o dado até o relatório. Quando a ingestão
    do FRED preenche ``market_us.macro_observations``, a leitura passa a
    ``observado`` com a data da série — e só então o texto pode afirmar o valor
    em vez de condicioná-lo.
    """
    fed_funds: float = 4.25
    cpi_yoy: float = 2.5
    real_gdp_yoy: float = 2.0
    unemployment: float = 4.2
    yield_curve_10y_2y: float = 0.25
    high_yield_spread: float = 3.5
    fonte: str = FONTE_PREMISSA
    as_of: str | None = None


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# Campos numéricos do snapshot — os demais são procedência, não indicador.
_CAMPOS_NUMERICOS = (
    "fed_funds", "cpi_yoy", "real_gdp_yoy",
    "unemployment", "yield_curve_10y_2y", "high_yield_spread",
)


def evaluate_macro(snapshot: USMacroSnapshot | dict) -> dict:
    """Classifica o regime e calcula impulsos setoriais em escala -10..+10.

    A procedência (``fonte``/``as_of``) atravessa intacta: quem escreve o
    relatório precisa saber se o número foi observado ou presumido.
    """
    values = asdict(snapshot) if isinstance(snapshot, USMacroSnapshot) else dict(snapshot)
    defaults = asdict(USMacroSnapshot())
    clean = {}
    for key in _CAMPOS_NUMERICOS:
        default = defaults[key]
        try:
            value = float(values.get(key, default))
            clean[key] = value if isfinite(value) else default
        except (TypeError, ValueError):
            clean[key] = default
    fonte = str(values.get("fonte") or FONTE_PREMISSA)
    as_of = values.get("as_of")

    inflation = _clip((3.0 - clean["cpi_yoy"]) * 1.8, -4, 4)
    growth = _clip((clean["real_gdp_yoy"] - 1.5) * 1.6, -4, 4)
    labor = _clip((4.8 - clean["unemployment"]) * 1.2, -3, 3)
    curve = _clip(clean["yield_curve_10y_2y"] * 1.5, -3, 3)
    credit = _clip((4.5 - clean["high_yield_spread"]) * 1.1, -4, 4)
    rates = _clip((4.0 - clean["fed_funds"]) * 1.2, -4, 4)
    score = round(_clip(50 + 2.0 * (inflation + growth + labor + curve + credit + rates), 0, 100), 1)

    if score >= 65:
        regime, tone = "Expansão / apetite a risco", "favorável"
    elif score >= 45:
        regime, tone = "Transição / neutro", "neutro"
    else:
        regime, tone = "Desaceleração / aversão a risco", "adverso"

    sector_impacts = {
        "Technology": _clip(growth + rates + credit, -10, 10),
        "Communication Services": _clip(growth + rates, -10, 10),
        "Consumer Cyclical": _clip(growth + labor + credit, -10, 10),
        "Financial Services": _clip(curve + growth + credit - max(rates, 0), -10, 10),
        "Real Estate": _clip(1.5 * rates + credit, -10, 10),
        "Industrials": _clip(growth + labor, -10, 10),
        "Energy": _clip(growth + inflation * -0.4, -10, 10),
        "Healthcare": _clip(1.5 - abs(growth) * 0.2, -10, 10),
        "Consumer Defensive": _clip(1.5 - growth * 0.2, -10, 10),
        "Utilities": _clip(rates + 1.0, -10, 10),
    }
    return {
        "score": score,
        "regime": regime,
        "tone": tone,
        "fonte": fonte,
        "as_of": as_of,
        "observado": fonte == FONTE_OBSERVADO,
        "inputs": clean,
        "drivers": {
            "Inflação": round(inflation, 2), "Crescimento": round(growth, 2),
            "Mercado de trabalho": round(labor, 2), "Curva de juros": round(curve, 2),
            "Crédito": round(credit, 2), "Política monetária": round(rates, 2),
        },
        "sector_impacts": {k: round(v, 2) for k, v in sector_impacts.items()},
    }

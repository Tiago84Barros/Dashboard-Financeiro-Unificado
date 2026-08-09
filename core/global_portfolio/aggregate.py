"""Quadro unificado das posicoes das tres carteiras.

Peso global = alvo da classe x peso do ativo dentro do modelo. O peso e
adimensional, entao NAO ha conversao cambial aqui: o valor em reais, quando o
usuario informa o total, e total_brl x peso_global.

Coberto por tests/test_global_aggregate.py.
"""
from __future__ import annotations

import pandas as pd

from core.global_portfolio.taxonomy import setor_canonico
from core.portfolio.registry import get_spec

COLUNAS: tuple[str, ...] = (
    "asset_class", "symbol", "name", "sector_raw", "sector", "segment",
    "currency", "country", "weight_class", "weight_global", "valor_brl",
    "payload",
)


def _peso_do_modelo(payload: dict) -> float:
    bruto = (payload or {}).get("metrics", {}).get("weight")
    try:
        peso = float(bruto)
    except (TypeError, ValueError):
        return 0.0
    return peso if peso > 0 else 0.0


def montar_posicoes(snapshots_por_classe: dict[str, dict[str, dict]],
                    alvos: dict[str, float],
                    *, total_brl: float | None = None) -> pd.DataFrame:
    """Une as tres carteiras num unico quadro de posicoes com peso global."""
    linhas: list[dict] = []

    for classe in sorted(snapshots_por_classe):
        snaps = snapshots_por_classe[classe] or {}
        if not snaps:
            continue

        spec = get_spec(classe)
        alvo = float(alvos.get(classe, 0.0) or 0.0)

        pesos = {sym: _peso_do_modelo(p) for sym, p in snaps.items()}
        total_classe = sum(pesos.values())

        for symbol in sorted(snaps):
            payload = snaps[symbol]
            identidade = (payload or {}).get("identity", {})
            # Renormaliza dentro da classe: arredondamento no salvamento pode
            # deixar a soma em 0,98 e distorceria o peso global.
            peso_classe = (pesos[symbol] / total_classe) if total_classe > 0 else 0.0
            peso_global = alvo * peso_classe

            linhas.append({
                "asset_class": classe,
                "symbol": symbol,
                "name": identidade.get("name") or symbol,
                "sector_raw": identidade.get("sector"),
                "sector": setor_canonico(classe, identidade.get("sector"),
                                         identidade.get("segment")),
                "segment": identidade.get("segment"),
                "currency": spec.currency,
                "country": spec.country,
                "weight_class": peso_classe,
                "weight_global": peso_global,
                "valor_brl": (total_brl * peso_global) if total_brl is not None else None,
                "payload": payload,
            })

    if not linhas:
        return pd.DataFrame(columns=list(COLUNAS))

    df = pd.DataFrame(linhas, columns=list(COLUNAS))
    return (df.sort_values(["weight_global", "asset_class", "symbol"],
                           ascending=[False, True, True])
              .reset_index(drop=True))

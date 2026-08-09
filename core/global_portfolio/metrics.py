"""Metricas agregadas do patrimonio, sempre acompanhadas da cobertura.

Duas decisoes metodologicas deliberadas:

1. Valuation agregado por EARNINGS YIELD ponderado, invertido no fim. A media
   aritmetica ponderada de P/L e matematicamente incorreta: uma empresa com
   lucro quase zero tem P/L enorme e domina a media.

2. Qualidade NAO e agregada num numero unico. Score B3, entry_score americano e
   score FII vem de metodologias e escalas diferentes. Normalizar por percentil
   dentro das proprias posicoes daria uma media proxima de 0,5 por construcao,
   sem significado. A qualidade e reportada por classe.

Coberto por tests/test_global_metrics.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from core.global_portfolio.fields import valor as campo_valor

COBERTURA_MINIMA = 0.60

# Nome do score de qualidade dentro de payload["metrics"], por classe.
_SCORE_POR_CLASSE: dict[str, str] = {
    "b3": "score",
    "us": "entry_score",
    "fii": "score",
}


@dataclass(frozen=True)
class MetricaAgregada:
    """Valor agregado mais a fracao do patrimonio que sustentou o calculo.

    `cobertura` aqui e o peso das linhas efetivamente usadas no calculo do
    `valor` (ex.: exclui P/L negativo) — mais estrito que a funcao livre
    `cobertura()`, que so exige a presenca do campo. Ver docstring de
    `cobertura()` para o contraste.
    """

    valor: float | None
    cobertura: float
    n_ativos: int

    @property
    def confiavel(self) -> bool:
        return self.valor is not None and self.cobertura >= COBERTURA_MINIMA


_VAZIA = MetricaAgregada(valor=None, cobertura=0.0, n_ativos=0)


def _peso_seguro(bruto) -> float:
    """Converte peso para float defensivamente.

    Ausente, nao numerico ou nao finito (NaN/inf) vira 0.0 — nunca propaga
    para as somas. `float(x or 0.0)` sozinho nao basta: `float('nan') or 0.0`
    avalia para NaN (NaN e "truthy"), e NaN <= 0 e False, entao um peso NaN
    passaria pelo filtro de peso > 0 e contaminaria toda soma a jusante.
    """
    try:
        v = float(bruto) if bruto is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    return v if math.isfinite(v) else 0.0


def _peso_total(df: pd.DataFrame) -> float:
    """Soma do peso global do frame, robusta a coluna ausente ou a valores NaN.

    Se a coluna `weight_global` nao existir (frame incompleto), o acesso
    direto `df["weight_global"]` levantaria KeyError antes mesmo de chegar
    a qualquer guarda de `total <= 0` — retornar 0.0 aqui preserva a
    garantia de que nenhuma funcao publica deste modulo levanta.
    """
    if "weight_global" not in df.columns:
        return 0.0
    return float(pd.to_numeric(df["weight_global"], errors="coerce").fillna(0.0).sum())


def _pares(df: pd.DataFrame, campo: str) -> list[tuple[float, float]]:
    """(peso, valor) das linhas que possuem o campo canonico."""
    saida: list[tuple[float, float]] = []
    for linha in df.to_dict(orient="records"):
        v = campo_valor(linha.get("payload") or {}, linha.get("asset_class"), campo)
        if v is None:
            continue
        peso = _peso_seguro(linha.get("weight_global"))
        if peso <= 0:
            continue
        saida.append((peso, v))
    return saida


def cobertura(df: pd.DataFrame, campo: str) -> float:
    """Fracao do peso total cujas linhas possuem o campo (presenca, nao usabilidade).

    Difere de `MetricaAgregada.cobertura`: esta funcao conta qualquer linha em
    que o campo canonico resolve para um valor (via `campo_valor`), mesmo que
    esse valor seja depois descartado pela funcao de agregacao (ex.: P/L
    negativo em `valuation_agregado`). `MetricaAgregada.cobertura` conta so o
    peso das linhas efetivamente usadas no calculo. As duas podem divergir —
    ver `tests/test_global_metrics.py::test_cobertura_e_a_fracao_de_peso_com_o_dado`
    vs `test_valuation_ignora_pl_nao_positivo_e_reduz_cobertura`.
    """
    if df.empty:
        return 0.0
    total = _peso_total(df)
    if total <= 0:
        return 0.0
    return sum(peso for peso, _ in _pares(df, campo)) / total


def valuation_agregado(df: pd.DataFrame, campo: str = "pe") -> MetricaAgregada:
    """Multiplo agregado via earnings yield ponderado, invertido no fim."""
    if df.empty:
        return _VAZIA

    total = _peso_total(df)
    # Multiplo nao positivo (prejuizo) nao tem inverso interpretavel: sai do calculo.
    usaveis = [(peso, v) for peso, v in _pares(df, campo) if v > 0]
    if not usaveis or total <= 0:
        return _VAZIA

    peso_usado = sum(peso for peso, _ in usaveis)
    # yield_ponderado e soma de termos estritamente positivos (peso > 0, v > 0)
    # dividida por peso_usado > 0: nunca pode ser <= 0, entao nao ha guarda
    # para esse caso (seria um ramo morto, deducoes de "e se" impossivel).
    yield_ponderado = sum(peso * (1.0 / v) for peso, v in usaveis) / peso_usado

    return MetricaAgregada(
        valor=1.0 / yield_ponderado,
        cobertura=peso_usado / total,
        n_ativos=len(usaveis),
    )


def dy_consolidado(df: pd.DataFrame) -> MetricaAgregada:
    """DY do patrimonio: media ponderada aritmetica.

    Aqui a aritmetica esta correta — o DY e razao sobre preco e os pesos
    tambem sao sobre preco, entao a soma ponderada e o rendimento real.
    """
    if df.empty:
        return _VAZIA

    total = _peso_total(df)
    usaveis = _pares(df, "dy")
    if not usaveis or total <= 0:
        return _VAZIA

    peso_usado = sum(peso for peso, _ in usaveis)
    return MetricaAgregada(
        valor=sum(peso * v for peso, v in usaveis) / peso_usado,
        cobertura=peso_usado / total,
        n_ativos=len(usaveis),
    )


def qualidade_por_classe(df: pd.DataFrame) -> dict[str, MetricaAgregada]:
    """Score medio ponderado DENTRO de cada classe. Nunca agregado entre classes."""
    if df.empty:
        return {}

    saida: dict[str, MetricaAgregada] = {}
    for classe in sorted(df["asset_class"].dropna().unique()):
        recorte = df[df["asset_class"] == classe]
        chave = _SCORE_POR_CLASSE.get(str(classe))
        total = _peso_total(recorte)

        usaveis: list[tuple[float, float]] = []
        if chave:
            for linha in recorte.to_dict(orient="records"):
                bruto = (linha.get("payload") or {}).get("metrics", {}).get(chave)
                peso = _peso_seguro(linha.get("weight_global"))
                if bruto is None or isinstance(bruto, bool) or peso <= 0:
                    continue
                try:
                    usaveis.append((peso, float(bruto)))
                except (TypeError, ValueError):
                    continue

        if not usaveis or total <= 0:
            saida[str(classe)] = _VAZIA
            continue

        peso_usado = sum(peso for peso, _ in usaveis)
        saida[str(classe)] = MetricaAgregada(
            valor=sum(peso * v for peso, v in usaveis) / peso_usado,
            cobertura=peso_usado / total,
            n_ativos=len(usaveis),
        )
    return saida

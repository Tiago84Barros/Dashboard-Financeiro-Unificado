"""Retornos mensais dos ativos da carteira, com cobertura explicita.

Os precos americanos NAO existem no Supabase: so a vitrine de scores
(market_us.company_snapshots) foi publicada; market_us.prices_daily e
prices_monthly ficaram no armazem local. Por isso todo ativo da classe `us`
entra direto em simbolos_sem_serie — nao e falha, e ausencia de dado, e a
interface precisa dizer isso em vez de exibir um numero parcial como se fosse
do patrimonio inteiro.

Coberto por tests/test_global_returns.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MIN_OBS = 18                      # mesmo piso de core/b3_correlation_diversification
_CLASSES_COM_PRECO = ("b3", "fii")


@dataclass(frozen=True)
class Cobertura:
    """Quanto do patrimonio a serie de precos alcanca."""

    simbolos_com_serie: tuple[str, ...]
    simbolos_sem_serie: tuple[str, ...]
    peso_coberto: float
    meses: int


_VAZIA = Cobertura((), (), 0.0, 0)


def _default_loader(tickers: tuple[str, ...]) -> pd.DataFrame:
    from core.market_read import load_precos_mensais
    return load_precos_mensais(tickers)


def retornos_mensais(df_posicoes: pd.DataFrame,
                     *, loader=None) -> tuple[pd.DataFrame, Cobertura]:
    """Retornos mensais dos ativos com serie, mais o relatorio de cobertura."""
    if df_posicoes is None or df_posicoes.empty:
        return pd.DataFrame(), _VAZIA

    loader = loader or _default_loader
    linhas = df_posicoes.to_dict(orient="records")
    peso_total = sum(float(l.get("weight_global") or 0.0) for l in linhas)

    candidatos = sorted({
        str(l["symbol"]) for l in linhas
        if str(l.get("asset_class") or "").lower() in _CLASSES_COM_PRECO
    })
    sem_preco = sorted({
        str(l["symbol"]) for l in linhas
        if str(l.get("asset_class") or "").lower() not in _CLASSES_COM_PRECO
    })

    precos = loader(tuple(candidatos)) if candidatos else pd.DataFrame()
    if not isinstance(precos, pd.DataFrame) or precos.empty:
        return pd.DataFrame(), Cobertura((), tuple(sorted(sem_preco + candidatos)), 0.0, 0)

    retornos = precos.sort_index().pct_change().dropna(how="all")
    # Serie curta nao sustenta correlacao: sai e conta como nao coberta.
    validos = sorted(c for c in retornos.columns if retornos[c].count() >= MIN_OBS)
    descartados = [c for c in retornos.columns if c not in validos]
    retornos = retornos[validos] if validos else pd.DataFrame()

    faltantes = sorted(set(sem_preco) | set(descartados)
                       | (set(candidatos) - set(retornos.columns)))
    peso_ok = sum(float(l.get("weight_global") or 0.0) for l in linhas
                  if str(l["symbol"]) in set(retornos.columns))

    return retornos, Cobertura(
        simbolos_com_serie=tuple(retornos.columns),
        simbolos_sem_serie=tuple(faltantes),
        peso_coberto=(peso_ok / peso_total) if peso_total > 0 else 0.0,
        meses=len(retornos),
    )

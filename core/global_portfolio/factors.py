"""Exposicao do patrimonio a fatores de risco, por regressao.

Os proxies sao ETFs negociados na B3, todos em reais e com serie mensal na
mesma tabela dos ativos — nao ha dependencia externa nem mistura de moeda.

Duas honestidades que a saida carrega sempre: o erro-padrao e o numero de
observacoes. Beta estimado com 24 pontos mensais nao e a mesma coisa que beta
estimado com 120, e quem le precisa poder distinguir.

Coberto por tests/test_global_factors.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Fator canonico -> ticker do proxy na B3.
PROXIES: dict[str, str] = {
    "global_dolar": "IVVB11",     # S&P 500 em BRL: mercado americano + cambio
    "inflacao": "IMAB11",         # IPCA+
    "juros_nominais": "IRFM11",   # prefixado
    "mercado_br": "BOVA11",
    "ouro": "GOLD11",
    "small_cap": "SMAL11",        # entra como spread SMAL11 - BOVA11
}

ROTULOS_FATOR: dict[str, str] = {
    "global_dolar": "Global / Dólar",
    "inflacao": "Inflação (IPCA+)",
    "juros_nominais": "Juros nominais",
    "mercado_br": "Mercado brasileiro",
    "ouro": "Ouro",
    "small_cap": "Small caps",
}

MIN_OBS_REGRESSAO = 24


@dataclass(frozen=True)
class Exposicao:
    """Beta a um fator, com o que permite julgar a estimativa."""

    fator: str
    beta: float
    erro_padrao: float
    r2: float
    n_obs: int

    @property
    def significativo(self) -> bool:
        """Dois erros-padrao — aproximacao de 95%."""
        return bool(self.erro_padrao > 0 and abs(self.beta) > 2 * self.erro_padrao)


def _default_loader(tickers: tuple[str, ...]) -> pd.DataFrame:
    from core.market_read import load_precos_mensais
    return load_precos_mensais(tickers)


def series_de_fatores(*, loader=None) -> pd.DataFrame:
    """Retornos mensais dos proxies, ja como fatores canonicos."""
    loader = loader or _default_loader
    precos = loader(tuple(sorted(set(PROXIES.values()))))
    if not isinstance(precos, pd.DataFrame) or precos.empty:
        return pd.DataFrame()

    # fill_method=None: um preco ausente vira NaN no retorno, nunca um 0%
    # fabricado (o default 'pad' do pandas preenche o preco antes de
    # diferenciar e transforma o gap em "calmaria" que nao existiu) — mesmo
    # cuidado de core.global_portfolio.returns.retornos_mensais.
    retornos = precos.sort_index().pct_change(fill_method=None).dropna(how="all")
    saida = pd.DataFrame(index=retornos.index)
    for fator in sorted(PROXIES):
        ticker = PROXIES[fator]
        if ticker in retornos.columns:
            saida[fator] = retornos[ticker]

    # Small cap e premio sobre o mercado, nao o retorno do indice inteiro:
    # sem o spread, small_cap e mercado_br seriam quase colineares.
    if "small_cap" in saida.columns and "mercado_br" in saida.columns:
        saida["small_cap"] = saida["small_cap"] - saida["mercado_br"]

    return saida.dropna(how="all")


def _regredir(y: pd.Series, X: pd.DataFrame) -> list[Exposicao]:
    comum = y.dropna().index.intersection(X.dropna().index)
    if len(comum) < MIN_OBS_REGRESSAO:
        return []

    yv = y.loc[comum].to_numpy(dtype=float)
    Xv = X.loc[comum].to_numpy(dtype=float)
    A = np.column_stack([np.ones(len(comum)), Xv])

    coef, *_ = np.linalg.lstsq(A, yv, rcond=None)
    residuo = yv - A @ coef
    gl = len(comum) - A.shape[1]
    if gl <= 0:
        return []

    var_res = float(residuo @ residuo) / gl
    try:
        cov = var_res * np.linalg.inv(A.T @ A)
    except np.linalg.LinAlgError:
        return []
    erros = np.sqrt(np.clip(np.diag(cov), 0.0, None))

    sq_tot = float(((yv - yv.mean()) ** 2).sum())
    r2 = float(1.0 - (residuo @ residuo) / sq_tot) if sq_tot > 0 else 0.0

    saida = [
        Exposicao(fator=str(nome), beta=float(coef[i + 1]),
                  erro_padrao=float(erros[i + 1]), r2=r2, n_obs=len(comum))
        for i, nome in enumerate(X.columns)
    ]
    return sorted(saida, key=lambda e: (-abs(e.beta), e.fator))


def betas_do_ativo(retornos_ativo: pd.Series, fatores: pd.DataFrame) -> list[Exposicao]:
    """Betas de um ativo contra os fatores."""
    if not isinstance(fatores, pd.DataFrame) or fatores.empty:
        return []
    return _regredir(pd.Series(retornos_ativo).dropna(), fatores)


def exposicao_do_portfolio(retornos: pd.DataFrame, pesos: dict,
                           fatores: pd.DataFrame) -> list[Exposicao]:
    """Betas do portfolio sintetico, ponderado pelos pesos informados.

    Ponderacao renormalizada mes a mes pelos ativos com retorno disponivel:
    numa carteira com ativos de historicos bem diferentes (blue chip com 20
    anos ao lado de FII recem-listado), um produto matricial ingenuo faria
    QUALQUER ativo sem retorno no mes anular a carteira inteira naquele mes
    — mesmo com os outros 19 ativos presentes. Retorno ausente nao e retorno
    zero (mesmo principio de core.global_portfolio.returns.Cobertura); o
    peso do que falta e redistribuido entre o que sobrou, nao descartado.
    """
    if (not isinstance(retornos, pd.DataFrame) or retornos.empty
            or not isinstance(fatores, pd.DataFrame) or fatores.empty):
        return []
    w = pd.Series({c: float(pesos.get(c, 0.0)) for c in retornos.columns})
    if w.sum() <= 0:
        return []

    disponivel = retornos.notna()
    pesos_mes = disponivel.mul(w, axis=1)
    total_mes = pesos_mes.sum(axis=1)
    pesos_norm = pesos_mes.div(total_mes.replace(0.0, np.nan), axis=0)
    carteira = (retornos.fillna(0.0) * pesos_norm).sum(axis=1)
    carteira[total_mes <= 0] = np.nan

    return _regredir(carteira, fatores)

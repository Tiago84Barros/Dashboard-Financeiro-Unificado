"""
core/b3_safras.py — a carteira de cada safra e o que ela rendeu.

O motor de scoring ja monta `lids_por_ano` e `pesos_por_ano` por segmento,
point-in-time (score com lag=1, dados ate N-1). Este modulo agrega isso
entre segmentos com o mesmo orcamento que a tela usa e mede o retorno da
janela de vigencia de cada safra.

`carteiras_por_safra` recebe a lista de resultados JA FILTRADA pela
chamadora. E assim que a medicao do vies de universo funciona: a mesma
funcao roda com os segmentos aprovados e com todos, e a distancia entre as
duas curvas e o tamanho do vies.

Modulo puro: sem streamlit, sem banco. Coberto por tests/test_b3_safras.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.b3_vigencia import ano_base_do_score, janela_de_vigencia, safra_completa


@dataclass(frozen=True)
class SafraCarteira:
    """A carteira que o motor teria montado numa safra, e seu universo."""
    safra: int
    ano_base: int
    inicio: pd.Timestamp
    fim: pd.Timestamp
    completa: bool
    pesos: dict[str, float]
    universo: tuple[str, ...]
    segmentos: int


def carteiras_por_safra(resultados: list[dict], *,
                        hoje: pd.Timestamp | None = None
                        ) -> list[SafraCarteira]:
    """Agrega os lideres por segmento na carteira consolidada de cada safra.

    Orcamento igual por segmento que tem lider na safra; dentro dele, os
    pesos do score. Ticker em mais de um segmento soma os orcamentos --
    mesma regra de views/portfolio_b3.py:3593-3624.
    """
    anos: set[int] = set()
    for res in resultados or []:
        anos.update(int(a) for a in (res.get("lids_por_ano") or {}))

    saida: list[SafraCarteira] = []
    for safra in sorted(anos):
        contribuintes = [
            res for res in resultados
            if (res.get("lids_por_ano") or {}).get(safra)
        ]
        if not contribuintes:
            continue
        orcamento = 1.0 / len(contribuintes)

        pesos: dict[str, float] = {}
        universo: list[str] = []
        for res in contribuintes:
            lids = list((res.get("lids_por_ano") or {})[safra])
            internos = dict((res.get("pesos_por_ano") or {}).get(safra) or {})
            total = sum(float(v) for v in internos.values())
            if total <= 0:
                internos = {tk: 1.0 / len(lids) for tk in lids}
                total = 1.0
            for tk in lids:
                fatia = orcamento * float(internos.get(tk, 0.0)) / total
                pesos[tk] = pesos.get(tk, 0.0) + fatia
            universo.extend(str(t).upper() for t in (res.get("tickers") or []))

        inicio, fim = janela_de_vigencia(safra)
        saida.append(SafraCarteira(
            safra=safra,
            ano_base=ano_base_do_score(safra),
            inicio=inicio,
            fim=fim,
            completa=safra_completa(safra, hoje=hoje),
            pesos=pesos,
            # dict.fromkeys preserva a ordem de insercao; sorted seria uma
            # ordem alfabetica que apaga a estrutura por segmento.
            universo=tuple(dict.fromkeys(universo)),
            segmentos=len(contribuintes),
        ))
    return saida


def _preco_nas_pontas(serie: pd.Series, inicio: pd.Timestamp,
                      fim: pd.Timestamp) -> tuple[float, float] | None:
    """Primeiro e ultimo preco valido DENTRO da janela, ou None."""
    dentro = serie[(serie.index >= inicio) & (serie.index <= fim)].dropna()
    dentro = dentro[dentro > 0]
    if len(dentro) < 2:
        return None
    return float(dentro.iloc[0]), float(dentro.iloc[-1])


def _retorno_selic(inicio: pd.Timestamp, fim: pd.Timestamp,
                   selic_por_ano: dict[int, float],
                   taxa_selic_aa: float) -> float:
    """Composto mes a mes, com a taxa do ano de cada mes -- a janela cruza
    dois anos civis e usar a taxa de um so deles distorce o benchmark."""
    acumulado = 1.0
    for mes in pd.date_range(inicio, fim, freq="MS"):
        taxa_aa = float(selic_por_ano.get(int(mes.year), taxa_selic_aa) or 0.0)
        acumulado *= (1.0 + taxa_aa) ** (1.0 / 12.0)
    return acumulado - 1.0


def retorno_da_safra(carteira: SafraCarteira, df_precos: pd.DataFrame, *,
                     selic_por_ano: dict[int, float],
                     taxa_selic_aa: float) -> dict:
    """Retorno buy-and-hold da janela de vigencia, contra Selic e equal-weight.

    Ticker sem duas cotacoes validas na janela rende ZERO e seu peso e
    reportado em `peso_ausente`. Nao redistribuimos a fatia entre os
    sobreviventes: isso faria a carteira render o que os sobreviventes
    renderam, que e exatamente o vies que a medicao existe para evitar.
    """
    inicio, fim = carteira.inicio, carteira.fim

    retorno_est = 0.0
    peso_ausente = 0.0
    for tk, peso in carteira.pesos.items():
        pontas = (_preco_nas_pontas(df_precos[tk], inicio, fim)
                  if tk in df_precos.columns else None)
        if pontas is None:
            peso_ausente += float(peso)
            continue
        p0, p1 = pontas
        retorno_est += float(peso) * (p1 / p0 - 1.0)

    retornos_ew: list[float] = []
    for tk in carteira.universo:
        pontas = (_preco_nas_pontas(df_precos[tk], inicio, fim)
                  if tk in df_precos.columns else None)
        if pontas is not None:
            p0, p1 = pontas
            retornos_ew.append(p1 / p0 - 1.0)
    retorno_ew = float(np.mean(retornos_ew)) if retornos_ew else float("nan")

    retorno_selic = _retorno_selic(inicio, fim, selic_por_ano or {},
                                   taxa_selic_aa)
    return {
        "retorno_estrategia": retorno_est,
        "retorno_equal_weight": retorno_ew,
        "retorno_selic": retorno_selic,
        "excesso_selic": retorno_est - retorno_selic,
        "excesso_equal_weight": (retorno_est - retorno_ew
                                 if np.isfinite(retorno_ew) else float("nan")),
        "peso_ausente": peso_ausente,
        "n_universo": len(retornos_ew),
    }


def tabela_de_safras(resultados: list[dict], df_precos: pd.DataFrame, *,
                     selic_por_ano: dict[int, float],
                     taxa_selic_aa: float,
                     hoje: pd.Timestamp | None = None) -> pd.DataFrame:
    """Uma linha por safra. `attrs['safras_completas']` lista as que podem
    entrar em media -- a safra vigente tem janela aberta e fica de fora."""
    linhas: list[dict] = []
    completas: list[int] = []
    for carteira in carteiras_por_safra(resultados, hoje=hoje):
        metricas = retorno_da_safra(carteira, df_precos,
                                    selic_por_ano=selic_por_ano,
                                    taxa_selic_aa=taxa_selic_aa)
        maiores = sorted(carteira.pesos.items(),
                         key=lambda kv: (-kv[1], kv[0]))[:5]
        linhas.append({
            "Safra": carteira.safra,
            "Exercício-base": carteira.ano_base,
            "Janela": f"{carteira.inicio:%m/%Y} a {carteira.fim:%m/%Y}",
            "Completa": carteira.completa,
            "Segmentos": carteira.segmentos,
            "Ativos": len(carteira.pesos),
            "Maiores posições": ", ".join(f"{tk} {p:.0%}" for tk, p in maiores),
            "Estratégia (%)": round(metricas["retorno_estrategia"] * 100, 1),
            "Equal-weight (%)": round(metricas["retorno_equal_weight"] * 100, 1),
            "Selic (%)": round(metricas["retorno_selic"] * 100, 1),
            "Excesso s/ Selic (pp)": round(metricas["excesso_selic"] * 100, 1),
            "Peso sem preço (%)": round(metricas["peso_ausente"] * 100, 1),
        })
        if carteira.completa:
            completas.append(carteira.safra)

    tabela = pd.DataFrame(linhas)
    tabela.attrs["safras_completas"] = completas
    return tabela

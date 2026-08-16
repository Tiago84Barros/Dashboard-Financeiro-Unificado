"""Retornos mensais dos ativos da carteira, com cobertura explicita.

A serie mensal dos EUA agora e publicada no Supabase (market_us.prices_monthly,
via scripts/publish_us_prices_monthly.py) e lida no mesmo formato do analogo da
B3 por core.us_read.load_precos_mensais_us. Por isso `us` entra em
_CLASSES_COM_PRECO junto de `b3` e `fii`: os tres tem preco e concorrem a
cobertura pelo mesmo caminho. O loader padrao busca cada classe na sua fonte
(b3/fii em core.market_read, us em core.us_read) e junta os quadros alinhando
pelo indice de data — um simbolo so e pedido a fonte da sua propria classe.

Coberto por tests/test_global_returns.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import pandas as pd

MIN_OBS = 18                      # mesmo piso de core/b3_correlation_diversification
_CLASSES_COM_PRECO = ("b3", "fii", "us")


@dataclass(frozen=True)
class Cobertura:
    """Quanto do patrimonio a serie de precos alcanca."""

    simbolos_com_serie: tuple[str, ...]
    simbolos_sem_serie: tuple[str, ...]
    peso_coberto: float
    meses: int


_VAZIA = Cobertura((), (), 0.0, 0)


def _default_loader(tickers: tuple[str, ...],
                    *, classes_por_simbolo: dict[str, str] | None = None) -> pd.DataFrame:
    """Busca precos mensais em cada fonte pela classe do simbolo e junta por data.

    `classes_por_simbolo` chega via functools.partial (montado em
    retornos_mensais a partir de df_posicoes) para nao alterar a assinatura
    que os testes injetam: um loader customizado continua recebendo so
    `tickers`. Cada simbolo e pedido a UMA fonte - a da sua propria classe -
    nunca as duas com a lista inteira, o que so infla a consulta sem mudar o
    resultado. O join e por `pd.concat(..., axis=1)`, que alinha pelo indice
    (mes) em vez de empilhar por posicao; ambas as fontes devolvem o mesmo
    formato (DatetimeIndex mensal, colunas = simbolos em maiusculas), entao o
    alinhamento e direto.
    """
    from core.market_read import load_precos_mensais
    from core.us_read import load_precos_mensais_us

    classes = classes_por_simbolo or {}
    tickers_us = tuple(sorted(t for t in tickers if classes.get(t) == "us"))
    tickers_outros = tuple(sorted(t for t in tickers if classes.get(t) != "us"))

    partes = []
    if tickers_outros:
        partes.append(load_precos_mensais(tickers_outros))
    if tickers_us:
        partes.append(load_precos_mensais_us(tickers_us))
    partes = [p for p in partes if isinstance(p, pd.DataFrame) and not p.empty]
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, axis=1)


def retornos_mensais(df_posicoes: pd.DataFrame,
                     *, loader=None) -> tuple[pd.DataFrame, Cobertura]:
    """Retornos mensais dos ativos com serie, mais o relatorio de cobertura."""
    if df_posicoes is None or df_posicoes.empty:
        return pd.DataFrame(), _VAZIA

    linhas = df_posicoes.to_dict(orient="records")
    peso_total = sum(float(l.get("weight_global") or 0.0) for l in linhas)

    mapa_classes = {str(l["symbol"]): str(l.get("asset_class") or "").lower()
                    for l in linhas}
    loader = loader or partial(_default_loader, classes_por_simbolo=mapa_classes)

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

    # fill_method=None: um preco ausente vira NaN no retorno, nunca um 0%
    # fabricado (o default 'pad' do pandas preenche o preco antes de diferenciar
    # e transforma o gap em "calmaria" que nao existiu).
    retornos = precos.sort_index().pct_change(fill_method=None).dropna(how="all")
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

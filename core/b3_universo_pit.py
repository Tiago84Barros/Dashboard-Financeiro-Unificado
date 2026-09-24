"""Universo da reconstrução histórica da B3 filtrado pela liquidez DA ÉPOCA.

O piso de negociabilidade da tela era aplicado com o volume de HOJE e valia
para todos os anos da reconstrução: um papel que em 2014 não tinha contraparte,
mas hoje negocia bem, podia ser escolhido líder em 2014 e entrar no backtest
com um retorno que ninguém conseguiria capturar. É olhar o futuro pelo
universo, não pelo balanço.

Aqui a elegibilidade de cada ano de decisão é medida com o volume dos
``meses`` anteriores ao mês de rebalanceamento daquele ano. Limitações
declaradas, porque o filtro só consegue REMOVER nomes:

* o universo de partida continua sendo o de hoje (quem deslistou nunca
  entrou) — viés de sobrevivência que este filtro não trata;
* o piso é em reais nominais, não deflacionado: R$ 1 mi/dia de 2012 vale mais
  que o de hoje, então o filtro é MAIS brando nos anos antigos;
* ticker sem nenhum pregão medido na janela fica ELEGÍVEL — ausência pode ser
  ingestão truncada, e punir ausência apagaria o universo em silêncio.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

PREGOES_MES = 21


def janela_decisao(ano: int, rebal_month: int, meses: int) -> tuple[date, date]:
    """[início, fim) dos meses que antecedem o rebalanceamento de ``ano``."""
    fim = date(ano, rebal_month, 1)
    inicio = (pd.Timestamp(fim) - pd.DateOffset(months=meses)).date()
    return inicio, fim


def elegiveis_por_ano(
    mensal: pd.DataFrame,
    anos: list[int],
    piso_diario: float,
    rebal_month: int = 4,
    meses: int = 6,
    pregoes_mes: int = PREGOES_MES,
) -> dict[int, dict]:
    """Por ano de decisão: quem ficou abaixo do piso com o volume da época.

    ``mensal``: colunas ``ticker``, ``mes`` (primeiro dia do mês) e
    ``financeiro`` (R$ negociados no mês). Devolve, por ano,
    ``{"medido": bool, "abaixo": set[str], "tickers_medidos": int}``; ano sem
    nenhum dado na janela sai com ``medido=False`` e não filtra ninguém.
    """
    out: dict[int, dict] = {}
    if mensal is None or mensal.empty:
        return {a: {"medido": False, "abaixo": set(), "tickers_medidos": 0} for a in anos}
    df = mensal.copy()
    df["mes"] = pd.to_datetime(df["mes"]).dt.date
    df["financeiro"] = pd.to_numeric(df["financeiro"], errors="coerce")
    df = df.dropna(subset=["financeiro"])
    for ano in anos:
        ini, fim = janela_decisao(ano, rebal_month, meses)
        jan = df[(df["mes"] >= ini) & (df["mes"] < fim)]
        if jan.empty:
            out[ano] = {"medido": False, "abaixo": set(), "tickers_medidos": 0}
            continue
        diario = jan.groupby("ticker")["financeiro"].median() / pregoes_mes
        out[ano] = {
            "medido": True,
            "abaixo": {str(t) for t, v in diario.items() if float(v) < piso_diario},
            "tickers_medidos": int(diario.size),
        }
    return out


def filtrar(tickers: list[str], elegibilidade: dict[int, dict] | None, ano: int,
            chave=str) -> list[str]:
    """Tickers elegíveis em ``ano``; sem medição do ano, devolve todos.

    ``chave`` normaliza o ticker para o formato usado em ``abaixo``.
    """
    info = (elegibilidade or {}).get(ano)
    if not info or not info.get("medido"):
        return list(tickers)
    abaixo = info["abaixo"]
    return [tk for tk in tickers if chave(tk) not in abaixo]

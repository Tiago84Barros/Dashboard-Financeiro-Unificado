"""Universo da reconstrução histórica da B3 filtrado pela liquidez DA ÉPOCA.

O piso de negociabilidade da tela era aplicado com o volume de HOJE e valia
para todos os anos da reconstrução: um papel que em 2014 não tinha contraparte,
mas hoje negocia bem, podia ser escolhido líder em 2014 e entrar no backtest
com um retorno que ninguém conseguiria capturar. É olhar o futuro pelo
universo, não pelo balanço.

Aqui a elegibilidade de cada ano de decisão é medida com o volume dos
``meses`` anteriores ao mês de rebalanceamento daquele ano. Limitações
declaradas, porque o filtro só consegue REMOVER nomes:

* este filtro só remove: quem deslistou entra pelo outro lado, via
  ``core.b3_saidas`` (o volume da época delas é somado ao dos vivos antes
  de chamar ``elegiveis_por_ano``);
* o piso é em reais nominais, não deflacionado: R$ 1 mi/dia de 2012 vale mais
  que o de hoje, então o filtro é MAIS brando nos anos antigos;
* ticker sem nenhum pregão medido na janela fica ELEGÍVEL — ausência pode ser
  ingestão truncada, e punir ausência apagaria o universo em silêncio.

Papel PARADO (auditoria app4, B3-06/07): a mediana da janela só enxerga os
meses em que houve negócio, e o ticker sem nenhum mês na janela caía na regra
da ausência acima. Resultado: quem parou de negociar em novembro passava pelo
piso com a mediana de outubro e novembro, e quem parou havia anos continuava
elegível — a auditoria contou 124 ativos sem negócio desde 01/09/2026 tratados
como elegíveis. ``parados_em`` separa os dois casos pela EVIDÊNCIA: só é parado
quem tem histórico de negócio no quadro e cujo último mês negociado terminou
mais de ``dias_parado`` dias antes da referência. Quem nunca apareceu no quadro
continua elegível (ausência não é morte). A referência é o último mês com
negócio de QUALQUER papel, limitado à data da decisão: se a ingestão inteira
parou, ninguém vira parado por isso. O filtro age só na ENTRADA da safra: quem
já estava na carteira continua contando o retorno até a série acabar, então
não há viés de sobrevivência por aqui.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

PREGOES_MES = 21
# 90 dias ≈ um trimestre inteiro sem negócio: mais que uma suspensão por fato
# relevante (dias) e menos que o intervalo entre balanços que a decisão lê.
DIAS_PARADO = 90


def janela_decisao(ano: int, rebal_month: int, meses: int) -> tuple[date, date]:
    """[início, fim) dos meses que antecedem o rebalanceamento de ``ano``."""
    fim = date(ano, rebal_month, 1)
    inicio = (pd.Timestamp(fim) - pd.DateOffset(months=meses)).date()
    return inicio, fim


def _mais_um_mes(d: date) -> date:
    return (pd.Timestamp(d) + pd.DateOffset(months=1)).date()


def parados_em(mensal: pd.DataFrame, data_ref: date,
               dias_parado: int = DIAS_PARADO) -> set[str]:
    """Tickers com negócio ANTES de ``data_ref`` mas parados há mais de ``dias_parado``.

    Só lê meses anteriores a ``data_ref`` (sem olhar o futuro). O mês é a
    granularidade da série: o último negócio é tomado como o fim do último mês
    com volume, a leitura mais branda possível. A referência é o fim do último
    mês com negócio de qualquer ticker, limitada a ``data_ref``.
    """
    if mensal is None or mensal.empty:
        return set()
    df = mensal[["ticker", "mes", "financeiro"]].copy()
    df["mes"] = pd.to_datetime(df["mes"]).dt.date
    df["financeiro"] = pd.to_numeric(df["financeiro"], errors="coerce")
    df = df[(df["financeiro"] > 0) & (df["mes"] < data_ref)]
    if df.empty:
        return set()
    referencia = min(data_ref, _mais_um_mes(df["mes"].max()))
    ultimo = df.groupby("ticker")["mes"].max()
    return {
        str(t) for t, m in ultimo.items()
        if (referencia - _mais_um_mes(m)).days > int(dias_parado)
    }


def elegiveis_por_ano(
    mensal: pd.DataFrame,
    anos: list[int],
    piso_diario: float,
    rebal_month: int = 4,
    meses: int = 6,
    pregoes_mes: int = PREGOES_MES,
    dias_parado: int | None = DIAS_PARADO,
) -> dict[int, dict]:
    """Por ano de decisão: quem ficou abaixo do piso ou parado na época.

    ``mensal``: colunas ``ticker``, ``mes`` (primeiro dia do mês) e
    ``financeiro`` (R$ negociados no mês). Devolve, por ano,
    ``{"medido": bool, "abaixo": set[str], "parados": set[str],
    "tickers_medidos": int}``; ano sem nenhum dado na janela sai com
    ``medido=False`` e não filtra ninguém. ``piso_diario=0`` aplica só a regra
    do papel parado; ``dias_parado=None`` a desliga.
    """
    out: dict[int, dict] = {}
    if mensal is None or mensal.empty:
        return {a: {"medido": False, "abaixo": set(), "parados": set(),
                    "tickers_medidos": 0} for a in anos}
    df = mensal.copy()
    df["mes"] = pd.to_datetime(df["mes"]).dt.date
    df["financeiro"] = pd.to_numeric(df["financeiro"], errors="coerce")
    df = df.dropna(subset=["financeiro"])
    for ano in anos:
        ini, fim = janela_decisao(ano, rebal_month, meses)
        jan = df[(df["mes"] >= ini) & (df["mes"] < fim)]
        if jan.empty:
            out[ano] = {"medido": False, "abaixo": set(), "parados": set(),
                        "tickers_medidos": 0}
            continue
        diario = jan.groupby("ticker")["financeiro"].median() / pregoes_mes
        out[ano] = {
            "medido": True,
            "abaixo": {str(t) for t, v in diario.items() if float(v) < piso_diario},
            # Todo o quadro até a decisão, não só a janela: quem parou antes do
            # início da janela não tem linha nela e cairia na regra da ausência.
            "parados": (set() if dias_parado is None
                        else parados_em(df, fim, dias_parado)),
            "tickers_medidos": int(diario.size),
        }
    return out


def filtrar(tickers: list[str], elegibilidade: dict[int, dict] | None, ano: int,
            chave=str) -> list[str]:
    """Tickers elegíveis em ``ano``; sem medição do ano, devolve todos.

    ``chave`` normaliza o ticker para o formato usado em ``abaixo`` e
    ``parados``.
    """
    info = (elegibilidade or {}).get(ano)
    if not info or not info.get("medido"):
        return list(tickers)
    fora = set(info.get("abaixo") or ()) | set(info.get("parados") or ())
    return [tk for tk in tickers if chave(tk) not in fora]

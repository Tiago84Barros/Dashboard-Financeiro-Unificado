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

TAMANHO por época (auditoria app4, look-ahead da aba B3): o piso de valor de
mercado também era o de HOJE e removia o papel de todos os anos da
reconstrução — e do Pesos Iguais com ele. Quem era grande em 2016 e encolheu
sumia do passado (o benchmark perdia justamente as quedas), e quem era nano
em 2016 e cresceu entrava em 2016. ``abaixo_do_tamanho_por_ano`` mede o valor
de mercado em 31/12 do exercício que a decisão lê (N-1), como ``core.b3_saidas``
já fazia com quem saiu. A série de preços não tem número de ações por data;
a estimativa é ``valor de hoje × fechamento(dez N-1) / fechamento de hoje``,
com o fechamento ajustado só por desdobramento/grupamento (a coluna ``close``
de ``market.historical_prices``). Emissão e recompra entre a data e hoje não
entram: quem emitiu muito aparece maior no passado do que era. Sem valor de
mercado hoje ou sem fechamento na época, o papel não é marcado (mesma regra da
ausência do volume).

O erro tem direção. Emissão posterior infla o passado e o piso DEIXA ENTRAR
quem talvez fosse menor; só recompra grande o faria barrar indevidamente.
Medido no armazém em 04/10/2026, dez/2018: PETR4 R$ 310 bi (real ~R$ 340 bi);
CVCB3 R$ 26,5 bi, AMER3 R$ 824 bi e BHIA3 R$ 94 bi, todas recapitalizadas
depois — muito acima do real, mas do lado em que o piso não morde. O volume
da época (acima) segue barrando quem não negociava.
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


# Fechamento de dezembro com até dois meses de folga: a série é mensal e pode
# faltar o último mês do ano; mais que isso já é outro preço, não o de 31/12.
MESES_FOLGA_FECHAMENTO = 2


def abaixo_do_tamanho_por_ano(
    fechamento: pd.DataFrame,
    mcap_hoje: dict[str, float],
    anos: list[int],
    piso: float,
    meses_folga: int = MESES_FOLGA_FECHAMENTO,
) -> dict[int, set[str]]:
    """Por ano de decisão N: quem valia menos que ``piso`` em 31/12 de N-1.

    ``fechamento``: colunas ``ticker``, ``mes`` (primeiro dia do mês) e
    ``fechamento`` (último preço do mês, ajustado só por desdobramento). A
    referência de "hoje" é o último mês com fechamento do próprio ticker. Ver
    no docstring do módulo a estimativa e o que ela não alcança. ``piso<=0``
    não marca ninguém.
    """
    out: dict[int, set[str]] = {int(a): set() for a in anos}
    if piso <= 0 or fechamento is None or fechamento.empty or not mcap_hoje:
        return out
    df = fechamento[["ticker", "mes", "fechamento"]].copy()
    df["mes"] = pd.to_datetime(df["mes"]).dt.date
    df["fechamento"] = pd.to_numeric(df["fechamento"], errors="coerce")
    df = df[df["fechamento"] > 0].sort_values(["ticker", "mes"])
    for tk, serie in df.groupby("ticker"):
        mc = mcap_hoje.get(str(tk))
        if mc is None or not (float(mc) > 0):
            continue
        meses = serie["mes"].tolist()
        precos = serie["fechamento"].tolist()
        ref = float(precos[-1])
        for ano in out:
            fim = date(int(ano) - 1, 12, 1)
            ini = (pd.Timestamp(fim) - pd.DateOffset(months=int(meses_folga))).date()
            idx = [i for i, m in enumerate(meses) if ini <= m <= fim]
            if not idx:
                continue
            if float(mc) * float(precos[idx[-1]]) / ref < float(piso):
                out[ano].add(str(tk))
    return out


def incorporar_tamanho(
    elegibilidade: dict[int, dict] | None,
    abaixo_tamanho: dict[int, set[str]],
) -> dict[int, dict]:
    """Soma o piso de tamanho da época à elegibilidade por volume.

    O tamanho vale mesmo nos anos sem volume medido: as duas medições são
    independentes, e a falta de uma não apaga a outra.
    """
    out = {a: dict(v) for a, v in (elegibilidade or {}).items()}
    for ano, fora in (abaixo_tamanho or {}).items():
        info = out.setdefault(int(ano), {"medido": False, "abaixo": set(),
                                         "parados": set(), "tickers_medidos": 0})
        info["abaixo_tamanho"] = set(fora)
    return out


def filtrar(tickers: list[str], elegibilidade: dict[int, dict] | None, ano: int,
            chave=str) -> list[str]:
    """Tickers elegíveis em ``ano``.

    Sem volume medido no ano, o volume não filtra; o piso de tamanho da época
    (``abaixo_tamanho``) filtra sempre que existir. ``chave`` normaliza o
    ticker para o formato usado nos conjuntos.
    """
    info = (elegibilidade or {}).get(ano)
    if not info:
        return list(tickers)
    fora = set(info.get("abaixo_tamanho") or ())
    if info.get("medido"):
        fora |= set(info.get("abaixo") or ()) | set(info.get("parados") or ())
    return [tk for tk in tickers if chave(tk) not in fora]

"""Momento e pesquisa e desenvolvimento: as duas trilhas de estilo dos EUA.

As seis trilhas do score americano (``core.us_score``) são fundamentalistas e
ficaram sem os dois estilos que mais renderam acima do mercado nos EUA na base
acadêmica sem viés de sobrevivência (JKP, carteiras por terço ponderadas por
valor, retorno acima do mercado):

==========================  =============  ==========  ==========  ==========
estilo                      desde          terço baixo  terço meio  terço alto
==========================  =============  ==========  ==========  ==========
momento 12-1 (``ret_12_1``)  1927           -3,1% a.a.   +0,1% a.a.  +4,1% a.a.
P&D / valor de mercado      1951           -1,0% a.a.   +0,4% a.a.  +3,7% a.a.
(``rd_me``)
==========================  =============  ==========  ==========  ==========

Medido em 30/10/2026 sobre ``dados/jkp`` da pesquisa ``PESQUISA 2026 - PREVISAO``
(relatório final, seção 8). Os três terços andam em escada, então a nota é o
percentil no universo avaliado: o alto sobe a nota, o baixo a puxa para baixo.

Diferente das seis trilhas, o percentil é no UNIVERSO, não na indústria: a
evidência é de carteiras do mercado inteiro, e o momento de indústria e a
concentração de P&D em tecnologia e saúde fazem parte do efeito medido.

- Momento: retorno total (``adjusted_close``) de 12 meses até 1 mês antes do
  último pregão de cada ação -- o mês mais recente fica de fora, como no
  ``ret_12_1``, porque nele o retorno tende a se reverter. Medida de mais de
  ``IDADE_MAXIMA_MOMENTO_DIAS`` atrás do pregão mais recente do universo vira
  ausência: é ação que parou de negociar, não momento.
- P&D: despesa de P&D do último exercício anual sobre o valor de mercado.
  Empresa sem P&D declarado (ou com zero) fica neutra: banco, varejo e
  mineradora não têm o que declarar, e a evidência compara só quem declara.

Ausência vira 50 na nota de entrada (``core.us_advanced_lab``), como nas
outras trilhas: não premia nem pune o que não foi medido.

Puro: os dados chegam por ``core.us_read.load_us_estilos`` (armazém local) ou
pelo blob ``metrics`` da vitrine, com as mesmas chaves de ``ESTILOS_COLS``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

ESTILOS_COLS = ("momento_12_1", "momento_12_1_at", "pd_anual_usd", "pd_exercicio")

#: O preço de cada ponta pode ser de até tantos dias antes da data-alvo
#: (feriado, dia sem negócio); mais longe que isso, a ponta não existe.
TOLERANCIA_PONTA_DIAS = 10
IDADE_MAXIMA_MOMENTO_DIAS = 45


def momento_12_1(precos: pd.DataFrame) -> dict[str, dict]:
    """``{símbolo: {"momento_12_1", "momento_12_1_at"}}`` a partir do diário.

    ``precos``: colunas ``symbol``, ``date`` e ``adjusted_close``, com pelo
    menos os 13 meses mais recentes. Para cada ação, ``d`` é o último pregão;
    o retorno vai do fechamento em ``d − 12 meses`` ao de ``d − 1 mês`` (o
    último pregão até cada data, com no máximo ``TOLERANCIA_PONTA_DIAS`` de
    distância). Sem as duas pontas, a ação fica de fora.
    """
    if precos is None or precos.empty:
        return {}
    p = precos[["symbol", "date", "adjusted_close"]].copy()
    p["symbol"] = p["symbol"].astype(str).str.strip().str.upper()
    p["date"] = pd.to_datetime(p["date"])
    p["adjusted_close"] = pd.to_numeric(p["adjusted_close"], errors="coerce")
    p = p[p["adjusted_close"] > 0].sort_values(["symbol", "date"])
    tolerancia = pd.Timedelta(days=TOLERANCIA_PONTA_DIAS)
    out: dict[str, dict] = {}
    for symbol, g in p.groupby("symbol", sort=False):
        serie = g.set_index("date")["adjusted_close"]
        ultimo = serie.index[-1]

        def ponta(alvo):
            antes = serie[serie.index <= alvo]
            if antes.empty or alvo - antes.index[-1] > tolerancia:
                return None
            return float(antes.iloc[-1])

        inicio = ponta(ultimo - pd.DateOffset(months=12))
        fim = ponta(ultimo - pd.DateOffset(months=1))
        if inicio is None or fim is None:
            continue
        out[symbol] = {
            "momento_12_1": fim / inicio - 1.0,
            "momento_12_1_at": datetime(ultimo.year, ultimo.month, ultimo.day,
                                        tzinfo=timezone.utc).isoformat(),
        }
    return out


def estilos_da_empresa(symbol: str, momento: dict, pesquisa: dict) -> dict:
    """As chaves de ``ESTILOS_COLS`` para o blob ``metrics`` de uma empresa.

    Ausência vira None, nunca zero: momento zero e P&D zero são medidas.
    """
    s = str(symbol).strip().upper()
    m = momento.get(s) or {}
    r = pesquisa.get(s) or {}
    return {"momento_12_1": m.get("momento_12_1"),
            "momento_12_1_at": m.get("momento_12_1_at"),
            "pd_anual_usd": r.get("pd_anual_usd"),
            "pd_exercicio": r.get("pd_exercicio")}


def _percentil(valores: pd.Series) -> pd.Series:
    """0–100 entre os valores observados; ausência continua ausência."""
    return (valores.rank(pct=True, method="average") * 100).round(1)


def _valor_de_mercado(frame: pd.DataFrame) -> pd.Series:
    for coluna in ("_market_cap", "market_cap"):
        if coluna in frame:
            return _numerica(frame, coluna)
    return pd.Series(float("nan"), index=frame.index)


def notas_de_estilo(frame: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta ``pd_valor_mercado``, ``score_momentum`` e ``score_pd``.

    Coluna de nota que já existe é mantida: quem calculou antes manda.
    """
    out = frame.copy()
    if "score_momentum" not in out:
        mom = _numerica(out, "momento_12_1")
        if "momento_12_1_at" in out:
            quando = pd.to_datetime(out["momento_12_1_at"], errors="coerce", utc=True)
            if quando.notna().any():
                velho = quando < quando.max() - pd.Timedelta(days=IDADE_MAXIMA_MOMENTO_DIAS)
                mom = mom.where(~velho.fillna(False))
        out["score_momentum"] = _percentil(mom)
    if "score_pd" not in out:
        pd_usd = _numerica(out, "pd_anual_usd")
        mcap = _valor_de_mercado(out)
        razao = (pd_usd / mcap).where((pd_usd > 0) & (mcap > 0))
        out["pd_valor_mercado"] = razao
        out["score_pd"] = _percentil(razao)
    return out


def _numerica(frame: pd.DataFrame, coluna: str) -> pd.Series:
    if coluna not in frame:
        return pd.Series(float("nan"), index=frame.index)
    return pd.to_numeric(frame[coluna], errors="coerce")

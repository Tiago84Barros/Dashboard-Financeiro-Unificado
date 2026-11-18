"""
data_pipeline/us/prices_yf.py
Preços/dividendos/splits via yfinance — implementa MarketDataProvider.

A SEC não publica cotação (só filings), então a fonte de preço é separada da de
fundamentos. O yfinance já é dependência do projeto (requirements.txt) e é usado
no lado B3, então não adiciona nada novo à stack.

Limitação honesta: yfinance raspa a API pública do Yahoo Finance — não tem SLA,
pode mudar sem aviso e não é fonte oficial. Para preço (dado amplamente
replicado) o risco é aceitável; para fundamento, não — por isso fundamento vem
da EDGAR.

O factory de Ticker é injetável (`ticker_factory`) para teste sem rede.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Optional

from data_pipeline.us.identity import normalize_symbol
from data_pipeline.us.providers import MarketDataProvider, ProviderError

logger = logging.getLogger("us_prices_yf")

# Quebra de ajuste do Yahoo (18/11/2026). Nos grupamentos de setembro e outubro
# de 2026, o Yahoo aplicou o fator só às barras do ano do split: tudo até
# 31/12/2025 ficou sem ajuste, e a série dá um salto falso de 1/razão em
# 02/01/2026 (BRTX 20,8x para um 1:20; OPTT 32x para um 1:30). O Yahoo também
# demora alguns dias para ajustar um split novo: aí a quebra cai no próprio dia
# do split. `corrigir_quebra_de_ajuste` procura o salto só nesses dois lugares
# e só para split grande, para não confundir com movimento de verdade.
FATOR_MINIMO_DE_SPLIT = 2.0
# Tolerância em log: o salto da quebra traz junto o retorno do próprio dia,
# que numa microcap passa de 10% (FRGT 10,95x para um 1:10).
TOLERANCIA_LOG_QUEBRA = math.log(1.3)
# Dia do split sem barra (COSM, 13/11/2026): vale o primeiro pregão até aqui.
DIAS_APOS_O_SPLIT = 7


class YFinanceProvider(MarketDataProvider):
    """Preços/dividendos/splits do yfinance.

    IMPORTANTE: baixa o histórico UMA vez por (symbol,range) e memoiza — preços,
    dividendos e splits saem do MESMO DataFrame (yfinance traz tudo com
    actions=True). Sem isso seriam 3 downloads por ticker, o que dispara o
    rate-limit do Yahoo (o erro "possibly delisted" em nomes óbvios). Retry com
    backoff cobre a flakiness residual do Yahoo.
    """

    def __init__(self, ticker_factory: Optional[Callable[[str], Any]] = None,
                 retries: int = 3, retry_wait: float = 2.0,
                 sleep_fn: Callable[[float], None] = time.sleep):
        self._factory = ticker_factory
        self.retries = max(1, retries)
        self.retry_wait = retry_wait
        self.sleep_fn = sleep_fn
        self.calls_made = 0
        self._cache: dict[tuple, Any] = {}

    def _ticker(self, symbol: str):
        if self._factory is not None:
            return self._factory(symbol)
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("yfinance não instalado — veja requirements.txt") from exc
        return yf.Ticker(symbol)

    def _history(self, symbol: str, start=None, end=None):
        sym = normalize_symbol(symbol) or symbol
        key = (sym, start, end)
        if key in self._cache:
            return self._cache[key]
        df = None
        for attempt in range(1, self.retries + 1):
            self.calls_made += 1
            try:
                t = self._ticker(sym)
                df = t.history(start=start, end=end, period=None if start else "max",
                               auto_adjust=False, actions=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("yfinance %s tentativa %d falhou: %s", sym, attempt, exc)
                df = None
            if df is not None and not getattr(df, "empty", True):
                break
            if attempt < self.retries:
                self.sleep_fn(self.retry_wait * attempt)  # backoff: 2s, 4s...
        self._cache[key] = df
        return df

    def get_prices_daily(self, symbol: str, start=None, end=None) -> list[dict]:
        df = self._history(symbol, start, end)
        if df is None or getattr(df, "empty", True):
            return []
        out = []
        for idx, row in df.iterrows():
            d = idx.date() if hasattr(idx, "date") else idx
            out.append({
                "date": str(d),
                "open": _f(row.get("Open")), "high": _f(row.get("High")),
                "low": _f(row.get("Low")), "close": _f(row.get("Close")),
                # 'Adj Close' já reflete splits e dividendos
                "adjClose": _f(row.get("Adj Close", row.get("Close"))),
                "volume": _i(row.get("Volume")),
            })
        return corrigir_quebra_de_ajuste(out, self._splits_do_df(df))

    def get_dividends(self, symbol: str, start=None, end=None) -> list[dict]:
        # Mesma janela que `get_prices_daily` de propósito: a memoização é por
        # (símbolo, start, end), então divergir aqui baixaria o histórico duas
        # vezes por símbolo.
        df = self._history(symbol, start, end)
        if df is None or getattr(df, "empty", True) or "Dividends" not in df:
            return []
        out = []
        for idx, val in df["Dividends"].items():
            v = _f(val)
            if v:                     # 0 = dia sem provento, não é dado ausente
                d = idx.date() if hasattr(idx, "date") else idx
                out.append({"date": str(d), "dividend": v})
        return out

    def get_splits(self, symbol: str, start=None, end=None) -> list[dict]:
        df = self._history(symbol, start, end)
        if df is None or getattr(df, "empty", True):
            return []
        return self._splits_do_df(df)

    @staticmethod
    def _splits_do_df(df) -> list[dict]:
        if "Stock Splits" not in df:
            return []
        out = []
        for idx, val in df["Stock Splits"].items():
            v = _f(val)
            if v:                     # yfinance dá a razão (ex.: 4.0 para 4:1)
                d = idx.date() if hasattr(idx, "date") else idx
                out.append({"date": str(d), "numerator": v, "denominator": 1.0})
        return out


def _razao(split: dict) -> Optional[float]:
    num, den = _f(split.get("numerator")), _f(split.get("denominator") or 1.0)
    if not num or not den or num <= 0 or den <= 0:
        return None
    return num / den


def _preco_de_referencia(linha: dict) -> Optional[float]:
    # O fechamento primeiro: o split mora nele, e o ajustado soma o provento.
    # IPDN (1:30 em 14/09/2026) salta 29x no fechamento e 39x no ajustado.
    v = linha.get("close") or linha.get("adjClose")
    return v if v and v > 0 else None


def corrigir_quebra_de_ajuste(linhas: list[dict], splits: list[dict]) -> list[dict]:
    """Aplica às barras de antes da quebra o ajuste de split que o Yahoo esqueceu.

    `linhas` são as barras de `get_prices_daily` (datas ISO em ordem crescente);
    `splits`, os do mesmo download, com a razão do yfinance (0,05 num 1:20).
    Para cada split com fator de ao menos `FATOR_MINIMO_DE_SPLIT`, os lugares
    candidatos são o dia do split e o primeiro pregão do ano dele. Se o preço
    ajustado salta ali por 1/razão (dentro de `TOLERANCIA_LOG_QUEBRA`), as barras
    de antes recebem o fator: preços ÷ razão, volume × razão. Dois splits do
    mesmo ano esquecidos juntos são testados pelo produto das razões.

    Série que o Yahoo ajustou direito não tem salto nesses lugares e sai igual.
    A função é idempotente: depois da correção o salto some. Também é a mesma
    para janela e histórico completo, então `ingest.passado_retroajustado`
    compara barras corrigidas pela mesma regra.
    """
    if not linhas or not splits:
        return linhas
    linhas = [dict(linha) for linha in linhas]
    datas = [str(linha["date"])[:10] for linha in linhas]
    grandes = []
    for sp in splits:
        r = _razao(sp)
        if r and max(r, 1 / r) >= FATOR_MINIMO_DE_SPLIT:
            grandes.append((str(sp["date"])[:10], r))
    if not grandes:
        return linhas

    def primeiro_pregao(desde: str, ate: str) -> Optional[int]:
        # Primeira barra com preço: o Yahoo às vezes deixa o dia do split vazio.
        for i, d in enumerate(datas):
            if d > ate:
                return None
            if d >= desde and _preco_de_referencia(linhas[i]):
                return i
        return None

    # candidato (índice da barra) -> razões a testar
    candidatos: dict[int, list[float]] = {}
    for d, r in grandes:
        i = primeiro_pregao(d, _mais_dias(d, DIAS_APOS_O_SPLIT))
        if i is not None:
            candidatos.setdefault(i, []).append(r)
        ano = d[:4]
        i_ano = primeiro_pregao(f"{ano}-01-01", d)
        if i_ano is not None and datas[i_ano] < d:
            candidatos.setdefault(i_ano, []).append(r)
    for i, razoes in candidatos.items():
        # O produto passa pelo mesmo piso: 4:1 seguido de 1:5 dá 0,8, e um
        # fator de 1,25 cabe na tolerância de uma série que não saltou.
        produto = math.prod(razoes)
        if len(razoes) > 1 and max(produto, 1 / produto) >= FATOR_MINIMO_DE_SPLIT:
            razoes.append(produto)

    for i in sorted(candidatos, reverse=True):
        anteriores = [j for j in range(i) if _preco_de_referencia(linhas[j])]
        if not anteriores:
            continue
        antes = _preco_de_referencia(linhas[anteriores[-1]])
        depois = _preco_de_referencia(linhas[i])
        if not antes or not depois:
            continue
        salto = depois / antes
        acerto = [r for r in candidatos[i]
                  if abs(math.log(salto * r)) <= TOLERANCIA_LOG_QUEBRA]
        if not acerto:
            continue
        r = min(acerto, key=lambda x: abs(math.log(salto * x)))
        for linha in linhas[:i]:
            for k in ("open", "high", "low", "close", "adjClose"):
                if linha.get(k) is not None:
                    linha[k] = linha[k] / r
            if linha.get("volume") is not None:
                linha["volume"] = int(round(linha["volume"] * r))
        logger.info("quebra de ajuste do Yahoo em %s: %d barras anteriores x %.4g",
                    datas[i], i, 1 / r)
    return linhas


def _mais_dias(d: str, dias: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(d) + timedelta(days=dias)).isoformat()


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def _i(v) -> Optional[int]:
    f = _f(v)
    return None if f is None else int(f)

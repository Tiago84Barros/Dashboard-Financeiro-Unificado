"""Cotações ao vivo da carteira e do dólar, com cache de 15 minutos.

**Por que existe:** a carteira lia preço e câmbio só de ``asset_quotes``, que a
rotina noturna grava uma vez por dia. Em 26/10/2026 o dólar abriu a R$ 5,55 e a
carteira ainda convertia os US$ 17.957 da Nomad pelo R$ 4,9652 de domingo: R$ 11
mil a menos que o card "USD / BRL" da mesma tela, que já buscava o câmbio ao
vivo. Duas fontes de câmbio na mesma página dão dois patrimônios.

Aqui a tela busca, numa chamada só ao yfinance, a cotação de cada ativo cotável
da carteira e o ``USDBRL=X``. O resultado vale 15 minutos para todos os
usuários (preço de mercado é público). O atraso é o da fonte: a B3 chega ao
Yahoo com cerca de 15 minutos.

**Reserva:** sem resposta, ou com um preço implausível diante do gravado no
banco, vale o fechamento de ``asset_quotes``, como antes. Uma falha total fica
em espera por ``ESPERA_APOS_FALHA_S`` para que um Yahoo fora do ar (ou com 429)
não custe uma tentativa de rede a cada clique.

Renda fixa, Tesouro, FIP e CDB não têm cotação no Yahoo e ficam de fora pelo
formato do ticker (``simbolo_yahoo``), não por lista de exceções.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Iterable, NamedTuple
from zoneinfo import ZoneInfo

import streamlit as st

logger = logging.getLogger(__name__)

TTL_S = 900
ESPERA_APOS_FALHA_S = 120
SIMBOLO_CAMBIO = "USDBRL=X"

#: Câmbio abaixo disto é dado corrompido (mesmo piso de core.fx_aquisicao).
CAMBIO_MINIMO = 2.0
#: Preço ao vivo fora desta razão contra o do banco é símbolo trocado ou dado
#: quebrado, não movimento de mercado.
RAZAO_PLAUSIVEL = (0.5, 2.0)

_RE_B3 = re.compile(r"^[A-Z]{4}\d{1,2}F?$")
_RE_EUA = re.compile(r"^[A-Z]{1,5}([.\-][A-Z])?$")
_FUSO_B3 = ZoneInfo("America/Sao_Paulo")
_FUSO_EUA = ZoneInfo("America/New_York")
_CLASSES_SEM_COTACAO = frozenset({
    "fixed_income", "tesouro", "renda_fixa", "fundo_rf", "fip",
})

_falha_ate = 0.0


class Cotacao(NamedTuple):
    preco: float
    timestamp: datetime


def simbolo_yahoo(ticker: str | None, moeda: str | None,
                  classe: str | None = None) -> str | None:
    """Símbolo do Yahoo para o ticker da carteira, ou None se não é cotável.

    Lote fracionário (``PETR4F``) cota pelo lote padrão. Ação americana com
    classe (``BRK.B``) usa hífen no Yahoo.
    """
    tk = (ticker or "").upper().strip()
    if not tk or (classe or "").lower() in _CLASSES_SEM_COTACAO:
        return None
    if (moeda or "BRL").upper() == "USD":
        return tk.replace(".", "-") if _RE_EUA.match(tk) else None
    if _RE_B3.match(tk):
        return (tk[:-1] if tk.endswith("F") else tk) + ".SA"
    return None


def _simbolo_da_linha(r) -> str | None:
    classe = getattr(r, "asset_type", None) or getattr(r, "asset_class", None)
    return simbolo_yahoo(getattr(r, "ticker", None),
                         getattr(r, "currency", None), classe)


def simbolos(*listas: Iterable) -> tuple[str, ...]:
    """Símbolos de todas as linhas, mais o câmbio se alguma está em dólar."""
    vistos: set[str] = set()
    usa_dolar = False
    for linhas in listas:
        for r in linhas or ():
            s = _simbolo_da_linha(r)
            if s:
                vistos.add(s)
            usa_dolar = usa_dolar or (getattr(r, "currency", None) or "").upper() == "USD"
    if usa_dolar:
        vistos.add(SIMBOLO_CAMBIO)
    return tuple(sorted(vistos))


def _momento(simbolo: str, dia: date, agora: datetime) -> datetime:
    """Barra de hoje vale como "agora"; barra de dia anterior, como o seu dia."""
    fuso = _FUSO_EUA if not simbolo.endswith((".SA", "=X")) else _FUSO_B3
    if dia >= agora.astimezone(fuso).date():
        return agora
    return datetime(dia.year, dia.month, dia.day, tzinfo=timezone.utc)


def _baixar(simbolos_: tuple[str, ...]) -> dict[str, Cotacao]:
    """Último fechamento (o da barra de hoje, durante o pregão) de cada símbolo."""
    import pandas as pd
    import yfinance as yf

    df = yf.download(list(simbolos_), period="5d", interval="1d",
                     auto_adjust=False, progress=False, threads=True, timeout=10)
    if df is None or df.empty:
        return {}
    fech = df["Close"]
    if isinstance(fech, pd.Series):
        fech = fech.to_frame(simbolos_[0])
    agora = datetime.now(timezone.utc)
    saida: dict[str, Cotacao] = {}
    for simbolo in fech.columns:
        serie = fech[simbolo].dropna()
        if serie.empty:
            continue
        preco = float(serie.iloc[-1])
        if preco > 0:
            saida[str(simbolo)] = Cotacao(preco, _momento(str(simbolo),
                                                          serie.index[-1].date(), agora))
    return saida


@st.cache_data(ttl=TTL_S, show_spinner=False)
def _buscar_em_cache(simbolos_: tuple[str, ...]) -> dict[str, Cotacao]:
    cotacoes = _baixar(simbolos_)
    if not cotacoes:
        # Levanta para o st.cache_data não guardar o vazio por 15 minutos.
        raise LookupError("yfinance sem cotação para nenhum símbolo")
    return cotacoes


def buscar(simbolos_: Iterable[str]) -> dict[str, Cotacao]:
    """Cotações ao vivo dos símbolos; ``{}`` quando a fonte não responde."""
    global _falha_ate
    chave = tuple(sorted(set(simbolos_)))
    if not chave or time.monotonic() < _falha_ate:
        return {}
    try:
        return _buscar_em_cache(chave)
    except Exception as exc:  # noqa: BLE001 - a reserva é o banco
        _falha_ate = time.monotonic() + ESPERA_APOS_FALHA_S
        logger.warning("[cotacao_viva] sem cotação ao vivo (%s); vale o banco.",
                       type(exc).__name__)
        return {}


def _plausivel(vivo: float, banco) -> bool:
    try:
        ref = float(banco) if banco is not None else 0.0
    except (TypeError, ValueError):
        ref = 0.0
    if ref <= 0:
        return True
    lo, hi = RAZAO_PLAUSIVEL
    return lo <= vivo / ref <= hi


def cambio(cotacoes: dict[str, Cotacao], taxa_banco=None) -> Cotacao | None:
    """USDBRL ao vivo, se plausível diante do banco; senão None."""
    c = cotacoes.get(SIMBOLO_CAMBIO)
    if c is None or c.preco < CAMBIO_MINIMO:
        return None
    banco = taxa_banco if (taxa_banco or 0) and float(taxa_banco) >= CAMBIO_MINIMO else None
    return c if _plausivel(c.preco, banco) else None


def cambio_ao_vivo() -> float | None:
    """Só o USDBRL ao vivo (mesmo cache da carteira quando o lote coincide)."""
    c = cambio(buscar((SIMBOLO_CAMBIO,)))
    return c.preco if c else None


def cambio_de_hoje(taxa_banco) -> float | None:
    """Câmbio para converter posição em dólar: ao vivo, senão o do banco.

    O do banco só serve se passar do piso; abaixo dele não há câmbio.
    """
    c = cambio(buscar((SIMBOLO_CAMBIO,)), taxa_banco)
    if c is not None:
        return c.preco
    taxa = float(taxa_banco) if taxa_banco is not None else 0.0
    return taxa if taxa >= CAMBIO_MINIMO else None


class _LinhaViva:
    """A linha do SQL com preço, câmbio e horário trocados pelos ao vivo."""
    __slots__ = ("_linha", "_troca")

    def __init__(self, linha, troca: dict):
        self._linha = linha
        self._troca = troca

    def __getattr__(self, nome):
        troca = object.__getattribute__(self, "_troca")
        if nome in troca:
            return troca[nome]
        return getattr(object.__getattribute__(self, "_linha"), nome)


def aplicar(linhas: list, attr_preco: str, attr_ts: str,
            cotacoes: dict[str, Cotacao]) -> tuple[list, int]:
    """(linhas com cotação ao vivo onde houver, quantas receberam preço vivo).

    ``attr_preco``/``attr_ts`` nomeiam a coluna de cotação daquele SQL
    (``live_price`` no snapshot, ``current_price`` em portfolio_positions).
    O câmbio de toda linha que o traz (``usd_brl_rate``) também é trocado.
    """
    if not cotacoes:
        return linhas, 0
    saida, n = [], 0
    for r in linhas:
        troca: dict = {}
        fx = cambio(cotacoes, getattr(r, "usd_brl_rate", None))
        if fx is not None and hasattr(r, "usd_brl_rate"):
            troca["usd_brl_rate"] = fx.preco
            troca["usd_brl_timestamp"] = fx.timestamp
        s = _simbolo_da_linha(r)
        c = cotacoes.get(s) if s else None
        if c is not None and _plausivel(c.preco, getattr(r, attr_preco, None)):
            troca[attr_preco] = c.preco
            troca[attr_ts] = c.timestamp
            n += 1
        saida.append(_LinhaViva(r, troca) if troca else r)
    return saida, n


def resumo(cotacoes: dict[str, Cotacao], n_ao_vivo: int) -> dict:
    """O que a tela diz sobre a origem das cotações."""
    fx = cotacoes.get(SIMBOLO_CAMBIO)
    momentos = [c.timestamp for c in cotacoes.values()]
    return {
        "n_ao_vivo": n_ao_vivo,
        "atualizado_em": max(momentos) if momentos else None,
        "cambio": fx.preco if fx and fx.preco >= CAMBIO_MINIMO else None,
    }

"""
core/b3_vigencia.py — a regra de vigencia da safra B3, num lugar so.

Uma safra N e pontuada com dados ate N-1 (lag=1) e vigora de 01/04/N a
31/03/N+1: os balancos do exercicio N-1 so sao publicos ate 31/03 (CVM),
entao uma carteira que assume em janeiro/N usa informacao contabil que
ainda nao existia. Medir jan-dez/N seria look-ahead.

Esta regra ja existia em tres implementacoes independentes
(_simular_seg_backtest, _rank_ic_por_ano, o filtro de inicio do backtest)
e um quarto consumidor -- o grafico de desempenho -- tinha ficado de fora.
Modulo puro: sem streamlit, sem banco. Coberto por tests/test_b3_vigencia.py.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

# Balancos FY N-1 publicados ate 31/03 (CVM). Auditoria 2026-07.
REBAL_MONTH = 4


def _ts(data: pd.Timestamp | datetime | date) -> pd.Timestamp:
    return pd.Timestamp(data)


def safra_vigente_em(data: pd.Timestamp | datetime | date) -> int:
    """Ano N cuja safra esta em vigor na data.

    Antes de abril a safra vigente ainda e a do ano anterior -- e por isso
    que "ano atual" nunca serve como rotulo: em fevereiro/2027 quem manda
    ainda e a safra 2026.
    """
    d = _ts(data)
    return int(d.year) if int(d.month) >= REBAL_MONTH else int(d.year) - 1


def janela_de_vigencia(safra: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(1o de abril da safra, 31 de marco do ano seguinte)."""
    safra = int(safra)
    return (pd.Timestamp(safra, REBAL_MONTH, 1),
            pd.Timestamp(safra + 1, REBAL_MONTH, 1) - pd.Timedelta(days=1))


def ano_base_do_score(safra: int) -> int:
    """Exercicio cujos balancos alimentaram o score da safra."""
    return int(safra) - 1


def safra_completa(safra: int,
                   hoje: pd.Timestamp | datetime | date | None = None) -> bool:
    """A janela da safra ja fechou? Safra incompleta nao entra em media."""
    fim = janela_de_vigencia(safra)[1]
    return _ts(hoje if hoje is not None else pd.Timestamp.now()) >= fim

"""Conversao de DataFrame compartilhada pelos tres adaptadores.

As tabelas de origem tem formatos diferentes, mas a conversao para dicts com
NaN -> None e identica. Coberto por tests/test_portfolio_adapter_frames.py.
"""
from __future__ import annotations

import pandas as pd


def registros(frame) -> list[dict]:
    """DataFrame -> lista de dicts, tolerante a None e vazio. NaN vira None."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    # Converte para dict e substitui NaN por None
    records = frame.to_dict(orient="records")
    return [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in records]


def indexar(frame, coluna: str) -> dict[str, dict]:
    """DataFrame -> {chave normalizada: linha}. Vazio se a coluna nao existir."""
    if not isinstance(frame, pd.DataFrame) or frame.empty or coluna not in frame:
        return {}
    return {str(linha[coluna]).strip().upper(): linha
            for linha in registros(frame)}

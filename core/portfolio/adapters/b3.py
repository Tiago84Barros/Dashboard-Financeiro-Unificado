"""Adaptador de snapshot das empresas B3.

Le em lote (nao ticker a ticker) porque montar o snapshot acrescenta tempo ao
salvamento da carteira. Coberto por tests/test_portfolio_adapter_b3.py.
"""
from __future__ import annotations

import datetime as dt

from core.portfolio.adapters._frames import registros
from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import get_spec

SPEC = get_spec("b3")


def _default_loaders() -> dict:
    from core import market_read
    return {
        "multiplos": lambda tks: market_read.load_multiplos_historico_batch(tks),
        "demonstracoes": lambda tks: market_read.load_demonstracoes_batch(tks),
    }


def _ticker(item: dict) -> str:
    return str(item.get("tk") or item.get("ticker") or "").strip().upper()


def build_snapshots(items: list[dict], *, model_id: str, params: dict,
                    as_of: dt.date, loaders: dict | None = None) -> list[AssetSnapshot]:
    """Monta um AssetSnapshot por item valido da carteira B3."""
    if loaders is None:
        loaders = _default_loaders()
    validos = [(item, _ticker(item)) for item in items]
    validos = [(item, tk) for item, tk in validos if tk]
    if not validos:
        return []

    tickers = tuple(sorted({tk for _, tk in validos}))
    multiplos = loaders["multiplos"](tickers) or {}
    demonstracoes = loaders["demonstracoes"](tickers) or {}

    saida: list[AssetSnapshot] = []
    for item, tk in validos:
        mult = registros(multiplos.get(tk))
        demo = registros(demonstracoes.get(tk))
        saida.append(AssetSnapshot.from_blocks(
            asset_class=SPEC.key,
            model_id=model_id,
            symbol=tk,
            as_of_date=as_of,
            blocks={
                "identity": {
                    "symbol": tk,
                    "name": item.get("nome") or tk,
                    "asset_class": SPEC.key,
                    "currency": SPEC.currency,
                    "country": SPEC.country,
                    "sector": item.get("setor"),
                    "subsector": item.get("subsetor"),
                    "segment": item.get("segmento"),
                },
                "fundamentals": mult[-1] if mult else {},
                "metrics": {
                    "score": item.get("score"),
                    "alpha_selic": item.get("alpha_selic"),
                    "alpha_ew": item.get("alpha_ew"),
                    "rank_score": item.get("rank_score"),
                    "weight": item.get("peso") if item.get("peso") is not None
                              else item.get("weight"),
                },
                "classification": {
                    "ano_lider": item.get("ano_lider"),
                    "motivos": list(item.get("motivos") or []),
                    "quali": item.get("quali") or {},
                    "has_history": bool(mult or demo),
                },
                "history": {
                    "multiplos_anuais": mult,
                    "demonstracoes_anuais": demo,
                },
                "assumptions": {"params": dict(params or {})},
                "evidence": {},
                "notes": "",
                "provenance": {
                    "source": "criacao_portfolio_b3",
                    "as_of_date": as_of.isoformat(),
                    "backfilled": False,
                },
            },
        ))
    return saida

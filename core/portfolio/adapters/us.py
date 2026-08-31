"""Adaptador de snapshot das empresas americanas.

A vitrine (load_snapshot_scored) e lida uma unica vez e indexada em memoria;
os demonstrativos sao lidos por simbolo porque nao ha versao em lote em
core/us_read.py. Coberto por tests/test_portfolio_adapter_us.py.
"""
from __future__ import annotations

import datetime as dt

from core.portfolio.adapters._frames import indexar, registros
from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import get_spec

SPEC = get_spec("us")

# Campos da vitrine que entram como classificacao, e nao como fundamento.
# `impairment_flags` viaja junto de `critical_missing` porque so as duas
# juntas dizem POR QUE o selo de decisao faltou: lacuna de dado ou
# veredito sobre o balanco. Separadas, a tela chama veredito de lacuna.
_CAMPOS_CLASSIFICACAO = ("score_confidence", "score_status",
                         "critical_missing", "impairment_flags")


def _default_loaders() -> dict:
    from core import us_read
    return {
        "scored": lambda: us_read.load_snapshot_scored(),
        "financials": lambda sym: us_read.load_company_financials(sym),
    }


def _symbol(item: dict) -> str:
    return str(item.get("symbol") or item.get("tk") or item.get("ticker") or "").strip().upper()


def build_snapshots(items: list[dict], *, model_id: str, params: dict,
                    as_of: dt.date, loaders: dict | None = None) -> list[AssetSnapshot]:
    """Monta um AssetSnapshot por item valido da carteira americana."""
    if loaders is None:
        loaders = _default_loaders()
    validos = [(item, _symbol(item)) for item in items]
    validos = [(item, sym) for item, sym in validos if sym]
    if not validos:
        return []

    vitrine = indexar(loaders["scored"](), "symbol")

    saida: list[AssetSnapshot] = []
    for item, sym in validos:
        linha = dict(vitrine.get(sym) or {})
        classificacao = {campo: linha.pop(campo, None) for campo in _CAMPOS_CLASSIFICACAO}
        linha.pop("symbol", None)
        financials = registros(loaders["financials"](sym))

        saida.append(AssetSnapshot.from_blocks(
            asset_class=SPEC.key,
            model_id=model_id,
            symbol=sym,
            as_of_date=as_of,
            blocks={
                "identity": {
                    "symbol": sym,
                    "name": item.get("nome") or sym,
                    "asset_class": SPEC.key,
                    "currency": SPEC.currency,
                    "country": SPEC.country,
                    "sector": item.get("setor"),
                    "subsector": item.get("industria"),
                    "segment": None,
                },
                "fundamentals": linha,
                "metrics": {
                    "entry_score": item.get("entry_score"),
                    "fundamental_score": item.get("fundamental_score"),
                    "coverage": item.get("coverage"),
                    "rank_score": item.get("rank_score"),
                    "weight": item.get("peso") if item.get("peso") is not None
                              else item.get("weight"),
                },
                "classification": {**classificacao,
                                   "has_history": bool(financials)},
                "history": {"financials_anuais": financials},
                "assumptions": {"params": dict(params or {})},
                "evidence": {},
                "notes": "",
                "provenance": {
                    "source": "criacao_portfolio_us",
                    "as_of_date": as_of.isoformat(),
                    "backfilled": False,
                },
            },
        ))
    return saida

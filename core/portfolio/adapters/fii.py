"""Adaptador de snapshot dos FIIs.

A composicao por tipo de ativo (imoveis, papel, caixa, fundos) e guardada em
classification.composition porque e o insumo do look-through da Fase 2.
Coberto por tests/test_portfolio_adapter_fii.py.
"""
from __future__ import annotations

import datetime as dt

from core.portfolio.adapters._frames import indexar
from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import get_spec

SPEC = get_spec("fii")

# Coluna da base (market_read.load_fiis) -> chave no bloco fundamentals.
_FUNDAMENTOS = {
    "Preço": "preco",
    "P/VP": "pvp",
    "DY_12m": "dy_12m",
    "Liquidez_Diaria": "liquidez_diaria",
    "Patrimonio": "patrimonio_liquido",
    "VPA": "vpa",
    "Cotistas": "num_cotistas",
    "Gestao": "tipo_gestao",
}

_COMPOSICAO = {
    "Pct_Imoveis": "pct_imoveis",
    "Pct_Papel": "pct_papel",
    "Pct_Caixa": "pct_caixa",
    "Pct_Fundos": "pct_fundos",
}


def _default_loaders() -> dict:
    from core import market_read
    return {"fiis": lambda: market_read.load_fiis()}


def _ticker(item: dict) -> str:
    # Precedência: tk > ticker (alinhado com b3._ticker)
    return str(item.get("tk") or item.get("ticker") or "").strip().upper()


def build_snapshots(items: list[dict], *, model_id: str, params: dict,
                    as_of: dt.date, loaders: dict | None = None) -> list[AssetSnapshot]:
    """Monta um AssetSnapshot por item valido da carteira de FIIs."""
    if loaders is None:
        loaders = _default_loaders()
    validos = [(item, _ticker(item)) for item in items]
    validos = [(item, tk) for item, tk in validos if tk]
    if not validos:
        return []

    base = indexar(loaders["fiis"](), "Ticker")

    saida: list[AssetSnapshot] = []
    for item, tk in validos:
        linha = base.get(tk) or {}
        fundamentals = {destino: linha[origem]
                        for origem, destino in _FUNDAMENTOS.items() if origem in linha}
        composition = {destino: linha[origem]
                       for origem, destino in _COMPOSICAO.items() if origem in linha}

        saida.append(AssetSnapshot.from_blocks(
            asset_class=SPEC.key,
            model_id=model_id,
            symbol=tk,
            as_of_date=as_of,
            blocks={
                "identity": {
                    "symbol": tk,
                    "name": item.get("nome") or linha.get("Nome") or tk,
                    "asset_class": SPEC.key,
                    "currency": SPEC.currency,
                    "country": SPEC.country,
                    "sector": item.get("segmento") or linha.get("Segmento"),
                    "subsector": None,
                    "segment": linha.get("Tipo"),
                },
                "fundamentals": fundamentals,
                "metrics": {
                    "score": item.get("score") if item.get("score") is not None
                             else linha.get("Score"),
                    "weight": item.get("peso") if item.get("peso") is not None
                              else item.get("weight"),
                },
                "classification": {"composition": composition},
                "history": {},
                "assumptions": {"params": dict(params or {})},
                "evidence": {},
                "notes": "",
                "provenance": {
                    "source": "selecao_fiis",
                    "as_of_date": as_of.isoformat(),
                    "backfilled": False,
                },
            },
        ))
    return saida

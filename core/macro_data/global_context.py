"""Contexto global: compara o macro atual com o já embutido na carteira salva."""
import math
from datetime import datetime, timezone

from core.macro_data.portfolio_context import load_portfolio_macro_snapshot


def load_global_macro_context(engine, positions, *, as_of=None):
    """Retorna snapshots por classe e DELTAS de impacto; legado fica contextual."""
    as_of = as_of or datetime.now(timezone.utc)
    snapshots, changes, limitations = {}, {}, []
    if engine is None:
        return snapshots, changes, ["Macro local indisponível."]
    for asset_class, frame in positions.groupby("asset_class"):
        if asset_class not in {"b3", "us", "fii"}:
            continue
        assets = {}
        for row in frame.to_dict("records"):
            symbol = str(row["symbol"])
            params = (row.get("payload") or {}).get("assumptions", {}).get("params", {})
            details = (params.get("macro_snapshot") or {}).get("details") or []
            # Preserva a taxonomia macro original (inclusive tipo de FII).
            sector = next((d.get("sector") for d in details
                           if d.get("symbol") == symbol and d.get("sector")), None)
            assets[symbol] = str(sector or row.get("sector_raw") or row.get("sector") or "")
        snapshot = load_portfolio_macro_snapshot(engine, asset_class=asset_class,
                                                 assets=assets, as_of=as_of)
        snapshots[asset_class] = snapshot
        for row in frame.to_dict("records"):
            symbol = str(row["symbol"])
            current = snapshot.impacts.get(symbol)
            params = (row.get("payload") or {}).get("assumptions", {}).get("params", {})
            saved = params.get("macro_snapshot") or {}
            previous = (saved.get("impacts") or {}).get(symbol)
            if params.get("macro_mode") == "fundamental":
                previous = 0.0
            if current is None or previous is None:
                limitations.append(f"{symbol}: sem comparação macro rastreável; somente contexto.")
                continue
            try:
                current, previous = float(current), float(previous)
            except (TypeError, ValueError, OverflowError):
                limitations.append(f"{symbol}: impacto macro inválido; somente contexto.")
                continue
            if not (math.isfinite(current) and math.isfinite(previous)):
                limitations.append(f"{symbol}: impacto macro não finito; somente contexto.")
                continue
            changes[symbol] = max(-100., min(100., current - previous))
    return snapshots, changes, limitations

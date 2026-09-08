"""Avalia premissas setoriais com dados locais; não grava nem ativa parâmetros.

python -m scripts.evaluate_macro_calibration --classe b3 --inicio 2010-01-01
"""
import argparse
import json
from datetime import datetime, timezone

import pandas as pd

from core.macro_data.calibration import calibrate_sector_factor
from core.macro_data.database import get_local_macro_engine
from core.macro_data.portfolio_context import load_portfolio_macro_snapshot
from core.macro_data.sector_policy import validated_sector_exposures
from scripts.backtest_macro_tilt import carregar_ativos, carregar_retornos


def evaluate(engine, asset_class, start, end):
    assets = carregar_ativos(engine, asset_class)
    panels = {}
    snapshots = []
    for cutoff in pd.date_range(start, end, freq="ME", tz="UTC"):
        snapshot = load_portfolio_macro_snapshot(engine, asset_class=asset_class,
            assets=assets, as_of=cutoff.to_pydatetime(), knowledge_mode="strict")
        if snapshot.details:
            snapshots.append(snapshot)
    # Evita consultar preços se nem os sinais têm a amostra mínima.
    returns = carregar_retornos(asset_class, list(assets)) if len(snapshots) >= 60 else pd.DataFrame()
    if not returns.empty:
        returns.index = pd.to_datetime(returns.index, utc=True)
        for snapshot in snapshots:
            grouped = {}
            for detail in snapshot.details:
                grouped.setdefault((detail["sector"], detail["factor"]), []).append(detail)
            for key, details in grouped.items():
                next_date = snapshot.as_of + pd.offsets.MonthEnd(1)
                if next_date > pd.Timestamp(end, tz="UTC") or next_date not in returns.index:
                    continue
                symbols = sorted({r["symbol"] for r in details}.intersection(returns.columns))
                values = returns.loc[next_date, symbols]
                if not symbols or values.isna().any():
                    continue
                panels.setdefault(key, []).append({
                    "cutoff": snapshot.as_of, "known_at": snapshot.as_of,
                    "return_end": next_date, "signal": sum(float(r["signal_score"]) for r in details) / len(details),
                    "forward_return": float(values.mean()),
                })
    results = []
    for cls, sector, factor, prior, _, _ in validated_sector_exposures():
        if cls == asset_class:
            result = calibrate_sector_factor(pd.DataFrame(panels.get((sector, factor), [])), prior=prior)
            results.append({"sector": sector, "factor": factor, **result.to_payload()})
    return {"class": asset_class, "strict_cutoffs_with_data": len(snapshots), "results": results,
            "limitations": ["composição atual; viés de sobrevivência",
                            "retornos locais de preço; não certifica retorno total nem custos",
                            "nenhum coeficiente foi ativado ou persistido"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classe", choices=["b3", "us", "fii"], required=True)
    parser.add_argument("--inicio", default="2010-01-01")
    parser.add_argument("--fim", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args()
    engine = get_local_macro_engine()
    if engine is None:
        print("Banco macro local indisponível; calibração não executada.")
        return 1
    try:
        print(json.dumps(evaluate(engine, args.classe, args.inicio, args.fim), ensure_ascii=False, indent=2))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Reconstrução histórica e validação walk-forward point-in-time de FIIs.

Somente dados cujo ``knowledge_at`` não ultrapassa a decisão entram no score.
Backfills sem data de publicação comprovável podem aparecer em diagnósticos,
mas reduzem a fração verificada e impedem a aprovação metodológica.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
import math
from typing import Any

import pandas as pd
from sqlalchemy import text

from core.fii_methodology import (FORMULA_VERSION, METHODOLOGY_VERSION,
                                  score_fiis_by_type)
from core.fii_validation import (evaluate_regime_performance, point_in_time_backtest,
                                 validate_methodology)
from data_pipeline.utils.db_utils import get_pipeline_engine


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    """Converte resultados analíticos em JSON válido para PostgreSQL jsonb."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return str(value)


def _normalize_type(value: Any) -> str | None:
    raw = str(value or "").lower()
    if raw in {"tijolo", "papel", "fof", "hibrido"}:
        return raw
    if "fundo de fundo" in raw or "fof" in raw:
        return "fof"
    if "receb" in raw or "papel" in raw or "cri" in raw:
        return "papel"
    if "hibr" in raw:
        return "hibrido"
    if raw:
        return "tijolo"
    return None


def _type_from_asset_classes(weights: dict[str, float]) -> str | None:
    if not weights:
        return None
    if weights.get("fund_holdings", 0.0) >= .55:
        return "fof"
    if weights.get("credit", 0.0) >= .55:
        return "papel"
    if weights.get("real_estate", 0.0) >= .55:
        return "tijolo"
    return "hibrido"


def _monthly_market_features(prices: pd.DataFrame, dividends: pd.DataFrame) -> dict[str, dict]:
    output: dict[str, dict] = {}
    if prices.empty:
        return output
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame["price"] = pd.to_numeric(frame["adjusted_close"], errors="coerce").fillna(
        pd.to_numeric(frame["close"], errors="coerce"))
    frame["close_raw"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame.get("volume"), errors="coerce")
    div = dividends.copy()
    if not div.empty:
        div["date"] = pd.to_datetime(div["event_date"], errors="coerce").fillna(
            pd.to_datetime(div["ex_date"], errors="coerce")).fillna(
            pd.to_datetime(div["payment_date"], errors="coerce"))
        div["amount"] = pd.to_numeric(div["amount"], errors="coerce")
    for ticker, group in frame.groupby("ticker"):
        group = group.sort_values("date").drop_duplicates("date", keep="last").set_index("date")
        monthly = group[["price", "close_raw"]].resample("ME").last().dropna(subset=["price"])
        monthly["return"] = monthly["price"].pct_change(fill_method=None)
        daily_value = group["close_raw"] * group["volume"]
        ticker_div = (div[div["ticker"] == ticker].dropna(subset=["date", "amount"])
                      if not div.empty else pd.DataFrame())
        output[str(ticker)] = {"daily": group, "monthly": monthly,
                               "daily_value": daily_value, "dividends": ticker_div}
    return output


def _features_as_of(bundle: dict, cutoff: pd.Timestamp) -> dict[str, float | int | None]:
    daily = bundle["daily"].loc[:cutoff]
    monthly = bundle["monthly"].loc[:cutoff]
    if daily.empty or monthly.empty:
        return {}
    price = _num(daily["close_raw"].dropna().iloc[-1])
    adjusted = monthly["price"].dropna()
    history_months = len(adjusted)
    trailing = adjusted.tail(37)
    drawdown = trailing / trailing.cummax() - 1.0 if len(trailing) else pd.Series(dtype=float)
    trend = (float(adjusted.iloc[-1] / adjusted.iloc[-13] - 1.0)
             if len(adjusted) >= 13 and adjusted.iloc[-13] > 0 else None)
    liquidity = bundle["daily_value"].loc[:cutoff].tail(63).median()
    div = bundle["dividends"]
    trailing_div = div[(div["date"] <= cutoff) &
                       (div["date"] > cutoff - pd.Timedelta(days=365))] if not div.empty else div
    dividend_sum = float(trailing_div["amount"].sum()) if not trailing_div.empty else 0.0
    dy = dividend_sum / price if price and price > 0 and dividend_sum > 0 else None
    recurrence = None
    growth = None
    if not div.empty:
        history_div = div[(div["date"] <= cutoff) &
                          (div["date"] > cutoff - pd.Timedelta(days=3 * 365 + 31))].copy()
        if not history_div.empty:
            monthly_div = history_div.set_index("date")["amount"].resample("ME").sum().tail(24)
            if len(monthly_div) >= 12 and monthly_div.mean() > 0:
                recurrence = max(0.0, 1.0 - float(monthly_div.std(ddof=0) / monthly_div.mean()))
            annual = history_div.groupby(history_div["date"].dt.year)["amount"].sum()
            if len(annual) >= 3 and annual.iloc[-3] > 0:
                growth = float((annual.iloc[-1] / annual.iloc[-3]) ** .5 - 1.0)
    return {
        "price": price, "dy_12m": dy, "liquidez_diaria": _num(liquidity),
        "history_months": history_months, "total_return_trend": trend,
        "max_drawdown": float(drawdown.min()) if len(drawdown) else None,
        "income_growth_per_share_3y": growth, "income_recurrence": recurrence,
    }


def reconstruct_snapshots(
    prices: pd.DataFrame, dividends: pd.DataFrame, observations: pd.DataFrame,
    exposures: pd.DataFrame, funds: pd.DataFrame, *, start: str | date | None = None,
    end: str | date | None = None,
) -> list[dict]:
    bundles = _monthly_market_features(prices, dividends)
    if not bundles:
        return []
    observations = observations.copy()
    exposures = exposures.copy()
    for frame in (observations, exposures):
        if not frame.empty:
            frame["knowledge_at"] = pd.to_datetime(frame["knowledge_at"], utc=True)
            frame["reference_date"] = pd.to_datetime(frame["reference_date"])
    if not observations.empty:
        observations = observations.sort_values(["reference_date", "knowledge_at"])
    if not exposures.empty:
        exposures = exposures.sort_values(["reference_date", "knowledge_at"])
    minimum = pd.Timestamp(start) if start else max(
        min(bundle["monthly"].index.min() for bundle in bundles.values()), pd.Timestamp("2021-01-31"))
    maximum = pd.Timestamp(end) if end else max(bundle["monthly"].index.max() for bundle in bundles.values())
    dates = pd.date_range(minimum, maximum, freq="ME")
    fund_map = {str(row.get("ticker")): row for row in funds.to_dict("records")}
    snapshots: list[dict] = []
    for decision in dates:
        cutoff = pd.Timestamp(datetime.combine(decision.date(), time(23, 59, 59),
                                               tzinfo=timezone.utc))
        eligible_obs = observations[
            (observations["knowledge_at"] <= cutoff) &
            (observations["reference_date"] <= decision)
        ] if not observations.empty else observations
        latest_obs = (eligible_obs
                      .drop_duplicates(["ticker", "metric_name"], keep="last")
                      if not eligible_obs.empty else eligible_obs)
        observations_by_ticker: dict[str, list[dict]] = {}
        for observation in latest_obs.to_dict("records"):
            observations_by_ticker.setdefault(str(observation["ticker"]), []).append(observation)
        eligible_exp = exposures[
            (exposures["knowledge_at"] <= cutoff) &
            (exposures["reference_date"] <= decision) &
            (exposures["exposure_type"] == "asset_class")
        ] if not exposures.empty else exposures
        exposures_by_ticker: dict[str, list[dict]] = {}
        if not eligible_exp.empty:
            latest_reference = eligible_exp.groupby("ticker")["reference_date"].transform("max")
            latest_exp = eligible_exp[eligible_exp["reference_date"] == latest_reference]
            for exposure in latest_exp.to_dict("records"):
                exposures_by_ticker.setdefault(str(exposure["ticker"]), []).append(exposure)
        rows: list[dict] = []
        for ticker, bundle in bundles.items():
            daily = bundle["daily"].loc[:decision]
            if daily.empty or (decision - daily.index[-1]).days > 45:
                continue
            feature = _features_as_of(bundle, decision)
            if not feature:
                continue
            ticker_exp = exposures_by_ticker.get(ticker, [])
            asset_classes = {
                str(item["exposure_name"]): float(item["exposure_weight"])
                for item in ticker_exp
            }
            fii_type = _type_from_asset_classes(asset_classes)
            type_quality = "verified_publication"
            if not fii_type:
                fii_type = _normalize_type((fund_map.get(ticker) or {}).get("tipo"))
                type_quality = "first_observed_proxy"
            if not fii_type:
                continue
            row: dict[str, Any] = {
                "ticker": ticker, "tipo": fii_type, **feature,
                "metric_metadata": {}, "data_consistency": .90,
                "snapshot_availability_quality": type_quality,
            }
            for metric in ("dy_12m", "liquidez_diaria", "total_return_trend", "max_drawdown",
                           "income_growth_per_share_3y", "income_recurrence"):
                if row.get(metric) is not None:
                    row["metric_metadata"][metric] = {
                        "available_at": cutoff.isoformat(), "source": "public_market_history",
                        "source_quality": .85,
                    }
            for obs in observations_by_ticker.get(ticker, []):
                value = obs.get("value_numeric")
                if pd.isna(value):
                    value = obs.get("value_text")
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    value = obs.get("value_json")
                row[str(obs["metric_name"])] = value
                quality = str(obs.get("availability_quality") or "first_observed_proxy")
                row["metric_metadata"][str(obs["metric_name"])] = {
                    "available_at": str(obs.get("knowledge_at")), "source": obs.get("source"),
                    "availability_quality": quality,
                    "source_quality": .95 if quality == "verified_publication" else .75,
                    "reference_date": str(obs.get("reference_date")),
                }
            nav = _num(row.get("nav_per_share"))
            if nav and nav > 0 and feature.get("price"):
                row["pvp"] = float(feature["price"]) / nav
                row["metric_metadata"]["pvp"] = {
                    "available_at": cutoff.isoformat(), "source": "market_price+cvm_nav",
                    "source_quality": .93,
                }
            rows.append(row)
        scored = score_fiis_by_type(rows, as_of=decision.date(), validation_status="unvalidated")
        for item in scored:
            snapshots.append({
                "ticker": item["ticker"], "reference_date": decision.date().isoformat(),
                "available_at": cutoff.isoformat(), "methodology_version": METHODOLOGY_VERSION,
                "formula_version": FORMULA_VERSION, "fii_type": item["tipo"],
                "score": item["type_score"], "confidence": item["confidence"],
                "coverage": item["coverage"], "critical_coverage": item["critical_coverage"],
                "availability_quality": item.get("snapshot_availability_quality", "first_observed_proxy"),
                "components_json": item["components"], "inputs_json": item["score_inputs"],
                "missing_metrics_json": list(item["missing_metrics"]),
                "data_readiness_status": item["data_readiness_status"],
            })
    return snapshots


def _load_frames(conn) -> tuple[pd.DataFrame, ...]:
    prices = pd.read_sql(text("""
        SELECT ticker,date,close,adjusted_close,volume,source
        FROM market.historical_prices
        WHERE close IS NOT NULL AND ticker <> 'XFIX11'
    """), conn)
    dividends = pd.read_sql(text("""
        SELECT ticker,payment_date,ex_date,event_date,amount
        FROM market.dividends WHERE amount > 0
    """), conn)
    observations = pd.read_sql(text("""
        SELECT ticker,metric_name,value_numeric,value_text,value_json,reference_date,
               knowledge_at,availability_quality,source
        FROM market.fii_metric_observations
        WHERE quality_status IN ('observed','accepted')
          AND availability_quality <> 'migration_baseline'
    """), conn)
    exposures = pd.read_sql(text("""
        SELECT ticker,exposure_type,exposure_name,exposure_weight,reference_date,
               knowledge_at,availability_quality,source
        FROM market.fii_exposures
        WHERE availability_quality <> 'migration_baseline'
    """), conn)
    funds = pd.read_sql(text("SELECT ticker,tipo FROM market.fiis"), conn)
    return prices, dividends, observations, exposures, funds


def _monthly_returns(prices: pd.DataFrame) -> pd.DataFrame:
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["value"] = pd.to_numeric(frame["adjusted_close"], errors="coerce").fillna(
        pd.to_numeric(frame["close"], errors="coerce"))
    monthly = (frame.sort_values("date").set_index("date").groupby("ticker")["value"]
               .resample("ME").last().rename("value").reset_index())
    monthly["total_return"] = monthly.groupby("ticker")["value"].pct_change(fill_method=None)
    return monthly.dropna(subset=["total_return"])[["date", "ticker", "total_return"]]


def _macro_regimes(conn, dates: list[pd.Timestamp]) -> pd.DataFrame:
    macro = pd.read_sql(text("SELECT ano,selic,ipca FROM public.macro ORDER BY ano"), conn)
    if macro.empty:
        return pd.DataFrame(columns=["date", "regime"])
    by_year = {int(row.ano): row for row in macro.itertuples()}
    rows = []
    previous_selic = None
    for dt in dates:
        item = by_year.get(int(dt.year))
        if item is None:
            continue
        selic = float(item.selic or 0) * (100 if float(item.selic or 0) <= 1 else 1)
        ipca = float(item.ipca or 0) * (100 if float(item.ipca or 0) <= 1 else 1)
        if ipca >= 7:
            regime = "inflation_stress"
        elif selic - ipca >= 7:
            regime = "high_real_rate"
        elif previous_selic is not None and selic < previous_selic - 1:
            regime = "easing"
        else:
            regime = "neutral"
        rows.append({"date": dt, "regime": regime})
        previous_selic = selic
    return pd.DataFrame(rows)


def _persist_snapshots(conn, snapshots: list[dict]) -> int:
    if not snapshots:
        return 0
    result = conn.execute(text("""
        INSERT INTO market.fii_pit_score_snapshots (
            ticker,reference_date,available_at,methodology_version,formula_version,
            fii_type,type_score,confidence,coverage,critical_coverage,availability_quality,
            components_json,inputs_json,missing_metrics_json,data_readiness_status
        ) SELECT ticker,reference_date,available_at,methodology_version,formula_version,
            fii_type,score,confidence,coverage,critical_coverage,availability_quality,
            components_json,inputs_json,missing_metrics_json,data_readiness_status
        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
            ticker text,reference_date date,available_at timestamptz,methodology_version text,
            formula_version text,fii_type text,score numeric,confidence numeric,coverage numeric,
            critical_coverage numeric,availability_quality text,components_json jsonb,
            inputs_json jsonb,missing_metrics_json jsonb,data_readiness_status text
        ) ON CONFLICT (ticker,reference_date,methodology_version,formula_version)
        DO UPDATE SET available_at=EXCLUDED.available_at,type_score=EXCLUDED.type_score,
                      confidence=EXCLUDED.confidence,coverage=EXCLUDED.coverage,
                      critical_coverage=EXCLUDED.critical_coverage,
                      availability_quality=EXCLUDED.availability_quality,
                      components_json=EXCLUDED.components_json,inputs_json=EXCLUDED.inputs_json,
                      missing_metrics_json=EXCLUDED.missing_metrics_json,
                      data_readiness_status=EXCLUDED.data_readiness_status,
                      reconstructed_at=now()
    """), {"rows": json.dumps(_json_safe(snapshots), ensure_ascii=False)})
    return max(int(result.rowcount or 0), 0)


def run_pit_validation(*, years: int = 10, top_n: int = 12) -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "blockers": ["banco indisponível"]}
    with engine.begin() as conn:
        prices, dividends, observations, exposures, funds = _load_frames(conn)
        if prices.empty:
            return {"status": "failed", "blockers": ["histórico de preços ausente"]}
        end = pd.to_datetime(prices["date"]).max().date()
        start = (pd.Timestamp(end) - pd.DateOffset(years=max(int(years), 1))).date()
        snapshots = reconstruct_snapshots(
            prices, dividends, observations, exposures, funds, start=start, end=end,
        )
        persisted = _persist_snapshots(conn, snapshots)
        snapshot_frame = pd.DataFrame(snapshots)
        returns = _monthly_returns(prices)
        benchmark_prices = pd.read_sql(text("""
            SELECT date,close,adjusted_close FROM market.historical_prices
            WHERE ticker='XFIX11' ORDER BY date
        """), conn)
        benchmark_name = "XFIX11_proxy"
        if benchmark_prices.empty:
            benchmark = returns.groupby("date")["total_return"].median()
            benchmark_name = "universe_median_proxy"
        else:
            benchmark_prices["date"] = pd.to_datetime(benchmark_prices["date"])
            benchmark_prices["value"] = pd.to_numeric(
                benchmark_prices["adjusted_close"], errors="coerce").fillna(
                    pd.to_numeric(benchmark_prices["close"], errors="coerce"))
            benchmark = (benchmark_prices.set_index("date")["value"].resample("ME").last()
                         .pct_change(fill_method=None).dropna())
        if snapshot_frame.empty:
            backtest = {"status": "blocked", "blockers": ["nenhum snapshot histórico reconstruível"]}
        else:
            snapshot_frame = snapshot_frame.rename(columns={"score": "score"})
            backtest = point_in_time_backtest(
                snapshot_frame, returns, benchmark, top_n=top_n,
                transaction_cost=.0015, slippage=.0010,
            )
        regimes = _macro_regimes(conn, [pd.Timestamp(row["date"])
                                        for row in backtest.get("observations", [])])
        regime_results = evaluate_regime_performance(backtest.get("observations", []), regimes)
        validation = validate_methodology(backtest, regime_results)
        blockers = list(validation["blockers"])
        if benchmark_name != "IFIX_official":
            blockers.append("benchmark oficial IFIX ainda não disponível; proxy utilizada")
        # A presença de tickers históricos B3 mede a capacidade de incluir fundos
        # que já não estão na lista corrente. Sem isso, o viés de sobrevivência
        # continua explicitamente bloqueante.
        historical_tickers = conn.execute(text(
            "SELECT count(DISTINCT ticker) FROM market.fii_b3_security_history"
        )).scalar() or 0
        if int(historical_tickers) == 0:
            blockers.append("security master histórico B3 ainda não carregado")
        status = "passed" if not blockers else "blocked"
        validation_id = conn.execute(text("""
            INSERT INTO market.fii_validation_runs (
                methodology_version,as_of_date,status,metrics_json,blockers_json,finished_at
            ) VALUES (:version,:as_of,:status,CAST(:metrics AS jsonb),
                      CAST(:blockers AS jsonb),now()) RETURNING id
        """), {"version": METHODOLOGY_VERSION, "as_of": end, "status": status,
                "metrics": json.dumps(_json_safe({"backtest": backtest,
                                                   "regimes": regime_results,
                                                   "benchmark": benchmark_name,
                                                   "snapshots_reconstructed": len(snapshots),
                                                   "snapshots_persisted": persisted})),
                "blockers": json.dumps(blockers, ensure_ascii=False)}).scalar()
        backtest_id = conn.execute(text("""
            INSERT INTO market.fii_backtest_runs (
                validation_run_id,methodology_version,formula_version,start_date,end_date,
                rebalance_frequency,benchmark_name,top_n,transaction_cost,slippage,status,
                metrics_json,blockers_json,finished_at
            ) VALUES (:validation,:version,:formula,:start,:end,'monthly',:benchmark,:top_n,
                      .0015,.0010,:status,CAST(:metrics AS jsonb),CAST(:blockers AS jsonb),now())
            RETURNING id
        """), {"validation": validation_id, "version": METHODOLOGY_VERSION,
                "formula": FORMULA_VERSION, "start": start, "end": end,
                "benchmark": benchmark_name, "top_n": top_n, "status": status,
                "metrics": json.dumps(_json_safe(backtest)),
                "blockers": json.dumps(blockers, ensure_ascii=False)}).scalar()
        periods = []
        positions = []
        for item in backtest.get("observations", []):
            periods.append({"backtest_run_id": backtest_id, **item,
                            "holdings": item.get("holdings") or {}})
            for ticker, weight in (item.get("holdings") or {}).items():
                positions.append({"backtest_run_id": backtest_id,
                                  "decision_date": str(item["decision_date"])[:10],
                                  "ticker": ticker, "weight": weight})
        if periods:
            conn.execute(text("""
                INSERT INTO market.fii_backtest_periods (
                    backtest_run_id,decision_date,execution_date,portfolio_return,
                    benchmark_return,coverage,turnover,holdings_json
                ) SELECT backtest_run_id,decision_date,date,portfolio_return,
                    benchmark_return,coverage,turnover,holdings
                FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
                    backtest_run_id bigint,decision_date date,date date,portfolio_return numeric,
                    benchmark_return numeric,coverage numeric,turnover numeric,holdings jsonb)
                ON CONFLICT (backtest_run_id,decision_date) DO NOTHING
            """), {"rows": json.dumps(_json_safe(periods))})
        if positions:
            conn.execute(text("""
                INSERT INTO market.fii_backtest_positions
                    (backtest_run_id,decision_date,ticker,weight)
                SELECT * FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
                    backtest_run_id bigint,decision_date date,ticker text,weight numeric)
                ON CONFLICT DO NOTHING
            """), {"rows": json.dumps(_json_safe(positions))})
    return {"status": status, "validation_run_id": int(validation_id),
            "backtest_run_id": int(backtest_id), "snapshots": len(snapshots),
            "periods": int(backtest.get("periods") or 0), "benchmark": benchmark_name,
            "blockers": blockers, "metrics": backtest, "regimes": regime_results}

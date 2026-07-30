"""Monitoramento determinístico da Carteira-modelo de FIIs.

Não executa ordens nem altera o banco. Consolida frescor, gate, versão,
look-through e drift entre a composição calculada e a última versão salva.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from core.fii_validation import portfolio_turnover, validation_supports_strategy


@dataclass(frozen=True)
class PortfolioMonitorPolicy:
    """Limites operacionais; taxas e pesos usam fração decimal."""

    # Mantém o monitor coerente com o gate de publicação da metodologia.
    warn_snapshot_age_days: int = 3
    max_snapshot_age_days: int = 4
    max_turnover: float = .20
    max_asset_weight_drift: float = .05
    min_dimension_coverage: float = .80
    required_dimensions: tuple[str, ...] = ("sector", "issuer")
    supplementary_dimensions: tuple[str, ...] = (
        "tenant", "debtor", "indexer", "region",
    )


def _check(code: str, status: str, message: str, **details: Any) -> dict:
    return {
        "code": code,
        "status": status,
        "message": message,
        "details": details,
    }


def _as_date(value: Any) -> date | None:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return parsed.date() if pd.notna(parsed) else None


def _weights(items: list[dict]) -> pd.Series:
    values: dict[str, float] = {}
    for item in items:
        ticker = str(item.get("ticker") or item.get("tk") or "").strip().upper()
        if not ticker:
            continue
        try:
            weight = float(item.get("weight") or item.get("peso") or 0.0)
        except (TypeError, ValueError):
            continue
        if weight > 0:
            values[ticker] = values.get(ticker, 0.0) + weight
    total = sum(values.values())
    if total <= 0:
        return pd.Series(dtype=float)
    return pd.Series({ticker: weight / total for ticker, weight in values.items()})


def _coverage_value(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("coverage")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(number, 1.0))


def build_fii_portfolio_monitor(
    *,
    current_items: list[dict],
    saved_model: dict | None,
    snapshot_as_of: Any,
    validation: dict,
    expected_strategy_id: str,
    expected_methodology_version: str,
    gate_can_publish: bool,
    dimension_coverage: dict[str, Any] | None,
    now: datetime | date | None = None,
    policy: PortfolioMonitorPolicy | None = None,
) -> dict:
    """Avalia prontidão operacional sem transformar ausência em baixo risco."""
    policy = policy or PortfolioMonitorPolicy()
    reference_date = (
        now.date() if isinstance(now, datetime)
        else now if isinstance(now, date)
        else datetime.now(timezone.utc).date()
    )
    checks: list[dict] = []

    snapshot_date = _as_date(snapshot_as_of)
    snapshot_age_days = (
        (reference_date - snapshot_date).days if snapshot_date is not None else None
    )
    if snapshot_age_days is None or snapshot_age_days < 0:
        checks.append(_check(
            "snapshot_freshness", "blocked",
            "Data auditável do snapshot ausente ou inválida.",
            snapshot_as_of=str(snapshot_as_of or ""),
        ))
    elif snapshot_age_days > policy.max_snapshot_age_days:
        checks.append(_check(
            "snapshot_freshness", "blocked",
            f"Snapshot vencido há {snapshot_age_days} dias.",
            age_days=snapshot_age_days,
            limit_days=policy.max_snapshot_age_days,
        ))
    elif snapshot_age_days > policy.warn_snapshot_age_days:
        checks.append(_check(
            "snapshot_freshness", "warning",
            f"Snapshot com {snapshot_age_days} dias; atualização recomendada.",
            age_days=snapshot_age_days,
            limit_days=policy.max_snapshot_age_days,
        ))
    else:
        checks.append(_check(
            "snapshot_freshness", "ok",
            f"Snapshot dentro da janela operacional ({snapshot_age_days} dias).",
            age_days=snapshot_age_days,
            limit_days=policy.max_snapshot_age_days,
        ))

    validation_ok = validation_supports_strategy(
        validation or {}, expected_strategy_id
    )
    checks.append(_check(
        "validation_strategy",
        "ok" if validation_ok else "blocked",
        (
            "Backtest PIT corresponde ao motor em execução."
            if validation_ok
            else "Backtest PIT aprovado para este motor não foi encontrado."
        ),
        expected_strategy_id=expected_strategy_id,
    ))
    checks.append(_check(
        "publication_gate",
        "ok" if gate_can_publish else "blocked",
        (
            "Gates vigentes aprovam a composição."
            if gate_can_publish
            else "Ao menos um gate vigente bloqueia a composição."
        ),
    ))

    coverage = {
        dimension: _coverage_value(value)
        for dimension, value in (dimension_coverage or {}).items()
    }
    for dimension in policy.required_dimensions:
        value = coverage.get(dimension)
        ok = value is not None and value >= policy.min_dimension_coverage
        checks.append(_check(
            f"coverage_{dimension}",
            "ok" if ok else "blocked",
            (
                f"Cobertura obrigatória de {dimension}: {value:.0%}."
                if value is not None
                else f"Cobertura obrigatória de {dimension} indisponível."
            ),
            coverage=value,
            minimum=policy.min_dimension_coverage,
        ))
    for dimension in policy.supplementary_dimensions:
        value = coverage.get(dimension)
        ok = value is not None and value >= policy.min_dimension_coverage
        checks.append(_check(
            f"coverage_{dimension}",
            "ok" if ok else "warning",
            (
                f"Look-through adicional de {dimension}: {value:.0%}."
                if value is not None
                else f"Look-through adicional de {dimension} não observável."
            ),
            coverage=value,
            minimum=policy.min_dimension_coverage,
        ))

    current_weights = _weights(current_items)
    saved_model = saved_model or {}
    saved_weights = _weights(list(saved_model.get("items") or []))
    turnover = None
    max_weight_drift = None
    if saved_weights.empty:
        checks.append(_check(
            "portfolio_drift", "warning",
            "Ainda não existe versão salva comparável para medir drift.",
        ))
    elif current_weights.empty:
        checks.append(_check(
            "portfolio_drift", "blocked",
            "Composição atual sem pesos válidos.",
        ))
    else:
        turnover = portfolio_turnover(saved_weights, current_weights)
        index = saved_weights.index.union(current_weights.index)
        drift = (
            current_weights.reindex(index, fill_value=0.0)
            - saved_weights.reindex(index, fill_value=0.0)
        ).abs()
        max_weight_drift = float(drift.max()) if not drift.empty else 0.0
        drift_ok = (
            turnover <= policy.max_turnover
            and max_weight_drift <= policy.max_asset_weight_drift
        )
        checks.append(_check(
            "portfolio_drift",
            "ok" if drift_ok else "warning",
            (
                "Composição dentro das bandas de monitoramento."
                if drift_ok
                else "Composição mudou além das bandas; requer revisão humana."
            ),
            turnover=turnover,
            max_asset_weight_drift=max_weight_drift,
            turnover_limit=policy.max_turnover,
            weight_drift_limit=policy.max_asset_weight_drift,
        ))

    params = saved_model.get("params_json") or {}
    saved_methodology = params.get("methodology_version")
    saved_strategy = params.get("strategy_id")
    version_ok = (
        not saved_model
        or (
            saved_methodology == expected_methodology_version
            and saved_strategy == expected_strategy_id
        )
    )
    checks.append(_check(
        "saved_model_version",
        "ok" if version_ok else "warning",
        (
            "Versão salva alinhada ao motor atual."
            if saved_model and version_ok
            else "Não há versão salva para comparar."
            if not saved_model
            else "Versão salva difere do motor atual; republicação deve ser revisada."
        ),
        saved_methodology_version=saved_methodology,
        saved_strategy_id=saved_strategy,
        expected_methodology_version=expected_methodology_version,
        expected_strategy_id=expected_strategy_id,
    ))

    statuses = {item["status"] for item in checks}
    overall_status = (
        "blocked" if "blocked" in statuses
        else "warning" if "warning" in statuses
        else "ok"
    )
    return {
        "status": overall_status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "metrics": {
            "snapshot_age_days": snapshot_age_days,
            "turnover": turnover,
            "max_asset_weight_drift": max_weight_drift,
            "dimension_coverage": coverage,
        },
        "policy": asdict(policy),
        "limitations": [
            "Ausência de exposição permanece desconhecida; não é convertida em zero.",
            "O monitor informa revisão e não executa compra, venda ou rebalanceamento.",
        ],
    }

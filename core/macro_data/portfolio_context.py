"""Contexto macro por ativo, calculado apenas com dados do Docker local."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping

import pandas as pd
from sqlalchemy import text

from core.macro_data.exposure import AssetMacroExposure, assess_asset_impact
from core.macro_data.models import MacroObservation
from core.macro_data.signals import evaluate_observation


@dataclass(frozen=True)
class PortfolioMacroSnapshot:
    impacts: dict[str, float]
    details: tuple[dict[str, object], ...]
    as_of: datetime
    asset_count: int
    covered_assets: int
    source_count: int
    limitations: tuple[str, ...] = ()
    knowledge_mode: str = "strict"

    @property
    def snapshot_id(self) -> str:
        """Identidade dos insumos, independente da hora de abertura da tela."""
        payload = asdict(self)
        payload.pop("as_of")
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str,
                                        ensure_ascii=False).encode()).hexdigest()[:24]

    def to_payload(self) -> dict:
        return json.loads(json.dumps({**asdict(self), "snapshot_id": self.snapshot_id,
                                     "method_version": "macro-integration-v2"}, default=str))

    @property
    def coverage(self) -> float:
        return self.covered_assets / self.asset_count if self.asset_count else 0.0


def aggregate_impact_rows(
    rows: Iterable[Mapping[str, object]],
) -> dict[str, float]:
    """Agrega fatores sem transformar ausência de cobertura em neutralidade."""
    grouped: dict[str, list[tuple[float, float]]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        direction = str(row.get("direction") or "neutral")
        if not symbol or direction not in {"positive", "negative", "neutral"}:
            continue
        sign = 1.0 if direction == "positive" else -1.0 if direction == "negative" else 0.0
        try:
            intensity = min(max(float(row.get("intensity") or 0.0), 0.0), 100.0)
            confidence = min(max(float(row.get("confidence") or 0.0), 0.0), 100.0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(intensity) or not math.isfinite(confidence):
            continue
        grouped.setdefault(symbol, []).append((sign * intensity, confidence))

    result: dict[str, float] = {}
    for symbol, values in grouped.items():
        confidence_sum = sum(confidence for _, confidence in values)
        if confidence_sum <= 0:
            continue
        weighted = sum(value * confidence for value, confidence in values) / confidence_sum
        result[symbol] = round(min(max(weighted, -100.0), 100.0), 4)
    return result


def observation_applies_to_asset_class(
    asset_class: str, row: Mapping[str, object]
) -> bool:
    """Evita tratar macro de outro país como se fosse exposição doméstica."""
    country = str(row.get("country_code") or "").upper()
    category = str(row.get("category") or "")
    provider_code = str(row.get("provider_code") or "").upper()
    if category == "commodities" and not country:
        return True
    if asset_class in {"b3", "fii"}:
        return country == "BRA" or (
            category == "currencies" and "BRL" in provider_code
        )
    return country in {"US", "USA"} or (
        category == "currencies" and "USD" in provider_code
    )


def load_portfolio_macro_snapshot(
    engine,
    *,
    asset_class: str,
    assets: Mapping[str, str],
    as_of: datetime | None = None,
    knowledge_mode: str = "strict",
) -> PortfolioMacroSnapshot:
    """Calcula impactos para símbolos/setores informados sem consultar a rede.

    Para datas históricas, a consulta é estritamente point-in-time: uma linha só
    entra se já havia sido recuperada e, quando conhecida, divulgada até ``as_of``.
    """
    if asset_class not in {"b3", "us", "fii"}:
        raise ValueError("classe de ativo macro inválida")
    if knowledge_mode not in {"strict", "reconstructed"}:
        raise ValueError("modo de conhecimento macro inválido")
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    normalized_assets = {
        str(symbol).strip().upper(): str(sector).strip()
        for symbol, sector in assets.items()
        if str(symbol).strip()
    }
    if not normalized_assets:
        return PortfolioMacroSnapshot(
            {}, (), as_of, 0, 0, 0, ("ativos sem setor",), knowledge_mode
        )

    with engine.connect() as conn:
        exposures = conn.execute(
            text("""
                SELECT sector, factor, sensitivity, confidence, channel
                  FROM macro_sector_exposures
                 WHERE asset_class=:asset_class AND sector = ANY(:sectors)
            """),
            {"asset_class": asset_class, "sectors": list(set(normalized_assets.values()))},
        ).mappings().all()
        observations = conn.execute(
            text("""
                WITH versions AS (
                    SELECT i.provider, i.provider_code, i.country_code, i.category,
                           i.frequency, i.unit, i.source_url,
                           o.reference_period, o.value, o.retrieved_at, o.released_at,
                           o.is_preliminary, o.is_forecast, o.vintage_date,
                           ROW_NUMBER() OVER (
                             PARTITION BY i.provider, i.provider_code,
                                          COALESCE(i.country_code, ''), o.reference_period
                             ORDER BY COALESCE(o.vintage_date, o.reference_period) DESC,
                                      o.retrieved_at DESC, o.id DESC
                           ) AS version_position
                      FROM macro_indicators i
                      JOIN macro_observations o
                        ON o.provider=i.provider
                       AND o.provider_code=i.provider_code
                       AND COALESCE(o.country_code, '')=COALESCE(i.country_code, '')
                     WHERE i.active AND i.category <> 'unmapped'
                       AND o.value IS NOT NULL AND NOT o.is_forecast
                       AND o.reference_period <= CAST(:as_of AS date)
                       AND (
                         (:knowledge_mode = 'strict'
                          AND o.retrieved_at <= :as_of
                          AND (o.released_at IS NULL OR o.released_at <= :as_of))
                         OR
                         (:knowledge_mode = 'reconstructed'
                          AND COALESCE(
                            o.released_at,
                            CAST(o.vintage_date AS timestamp),
                            CAST(o.reference_period AS timestamp)
                          ) <= :as_of)
                       )
                ), ranked AS (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY provider, provider_code, COALESCE(country_code, '')
                        ORDER BY reference_period DESC
                    ) AS position FROM versions WHERE version_position = 1
                )
                SELECT * FROM ranked WHERE position <= 24
                ORDER BY provider, provider_code, country_code, reference_period
            """),
            {"as_of": as_of, "knowledge_mode": knowledge_mode},
        ).mappings().all()

    exposure_by_sector_factor = {
        (str(row["sector"]), str(row["factor"])): row for row in exposures
    }
    grouped_observations: dict[tuple[str, str, str | None, str], list[Mapping[str, object]]] = {}
    for row in observations:
        if not observation_applies_to_asset_class(asset_class, row):
            continue
        key = (
            str(row["provider"]), str(row["provider_code"]),
            str(row["country_code"]) if row["country_code"] else None,
            str(row["category"]),
        )
        grouped_observations.setdefault(key, []).append(row)

    details: list[dict[str, object]] = []
    limitations = ["sensibilidades setoriais iniciais; não são coeficientes historicamente calibrados"]
    usable_series = 0
    for (provider, provider_code, country_code, factor), series in grouped_observations.items():
        observations_typed = [
            MacroObservation(
                provider=provider,
                provider_code=provider_code,
                country_code=country_code,
                reference_period=row["reference_period"],
                value=float(row["value"]),
                retrieved_at=row["retrieved_at"],
                released_at=row["released_at"],
                is_preliminary=bool(row["is_preliminary"]),
                vintage_date=row["vintage_date"],
            )
            for row in series
        ]
        signal = evaluate_observation(observations_typed, desirability=1,
                                      as_of=as_of, frequency=str(series[-1].get("frequency", "irregular")))
        if signal.direction == "unknown":
            limitations.append(f"{provider}.{provider_code}: " + "; ".join(signal.limitations))
            continue
        usable_series += 1
        for symbol, sector in normalized_assets.items():
            exposure = exposure_by_sector_factor.get((sector, factor))
            if exposure is None:
                continue
            impact = assess_asset_impact(
                signal,
                [AssetMacroExposure(
                    symbol,
                    factor,
                    float(exposure["sensitivity"]),
                    float(exposure["confidence"]),
                    str(exposure["channel"]),
                )],
                factor=factor,
            )[0]
            details.append({
                "symbol": symbol,
                "sector": sector,
                "factor": factor,
                "provider": provider,
                "provider_code": provider_code,
                "direction": impact.direction,
                "intensity": impact.intensity,
                "confidence": impact.confidence,
                "channel": impact.channel,
                "reference_period": series[-1]["reference_period"],
                "retrieved_at": series[-1]["retrieved_at"],
                "released_at": series[-1]["released_at"],
                "vintage_date": series[-1]["vintage_date"],
                "value": series[-1]["value"],
                "unit": series[-1].get("unit"),
                "source_url": series[-1].get("source_url"),
                "sensitivity": float(exposure["sensitivity"]),
                "calibration_status": "initial_prior",
                "signal_score": signal.impact_score * (1 if signal.direction == "favorable" else -1 if signal.direction == "adverse" else 0) / 100,
            })

    impacts = aggregate_impact_rows(details)
    if knowledge_mode == "reconstructed":
        limitations.append(
            "histórico reconstruído ex post; não equivale a captura disponível no dia"
        )
    if not observations:
        limitations.append("nenhuma observação era conhecida na data de corte")
    if len(impacts) < len(normalized_assets):
        limitations.append("parte dos ativos não possui setor/fator macro mapeado")
    return PortfolioMacroSnapshot(
        impacts=impacts,
        details=tuple(details),
        as_of=as_of,
        asset_count=len(normalized_assets),
        covered_assets=len(impacts),
        source_count=usable_series,
        limitations=tuple(limitations),
        knowledge_mode=knowledge_mode,
    )


def format_portfolio_macro_context(snapshot: PortfolioMacroSnapshot) -> str:
    """Texto factual limitado para LLM; decisões e pesos continuam em Python."""
    lines = [
        "CAMADA MACRO DETERMINÍSTICA (Docker local):",
        f"  data de corte={snapshot.as_of.isoformat()}; cobertura="
        f"{snapshot.coverage:.1%}; séries={snapshot.source_count}; "
        f"modo={snapshot.knowledge_mode}",
        f"  snapshot={snapshot.snapshot_id}; método=macro-integration-v2",
    ]
    if snapshot.limitations:
        lines.append("  limitações: " + "; ".join(snapshot.limitations))
    for symbol, score in sorted(snapshot.impacts.items()):
        lines.append(f"  {symbol}: impacto agregado={score:+.2f}/100")
        drivers = sorted((r for r in snapshot.details if r.get("symbol") == symbol),
                         key=lambda r: float(r.get("intensity") or 0), reverse=True)
        for row in drivers[:3]:
            lines.append(
                f"    {row.get('provider')}.{row.get('provider_code')}: "
                f"{row.get('value', 'ausente')} {row.get('unit') or ''}; "
                f"período={row.get('reference_period')}; divulgação={row.get('released_at') or 'não informada'}; "
                f"efeito={row.get('direction')}, intensidade={row.get('intensity')}, "
                f"confiança={row.get('confidence')}; canal={row.get('channel')}; "
                f"fonte={row.get('source_url') or row.get('provider')}"
            )
    lines.append(
        "  O impacto é contextual, não previsão; a LLM apenas explica e não calcula pesos."
    )
    content = "\n".join(lines)
    return content if len(content) <= 16000 else content[:15700] + "\n[drivers truncados; impacto contextual, não previsão; pesos calculados em Python]"


def format_saved_macro_context(payload: dict | None) -> str:
    """Mesma evidência da criação; não substitui snapshot salvo por dados de hoje."""
    if not payload:
        return ""
    try:
        return format_portfolio_macro_context(PortfolioMacroSnapshot(
            impacts=payload["impacts"], details=tuple(payload["details"]),
            as_of=datetime.fromisoformat(payload["as_of"]), asset_count=payload["asset_count"],
            covered_assets=payload["covered_assets"], source_count=payload["source_count"],
            limitations=tuple(payload.get("limitations", ())),
            knowledge_mode=payload.get("knowledge_mode", "strict")))
    except (KeyError, TypeError, ValueError):
        return "Snapshot macro salvo incompleto; evidência indisponível."


def historical_macro_weight_path(
    engine,
    *,
    asset_class: str,
    holdings: pd.DataFrame,
    symbol_column: str,
    sector_column: str,
    score_column: str,
    cutoffs: Iterable[datetime],
    mode: str = "moderate",
    rebuild: Callable | None = None,
) -> pd.DataFrame:
    """Reaplica a composição atual aos regimes passados, sem alegar backtest.

    A trajetória é uma análise de sensibilidade reconstruída. Ela não substitui
    um backtest point-in-time dos constituintes, pois mantém o conjunto atual.
    """
    from core.macro_data.portfolio_tilt import apply_macro_tilt

    if holdings is None or holdings.empty:
        return pd.DataFrame()
    required = {symbol_column, sector_column, score_column, "weight"}
    missing = required.difference(holdings.columns)
    if missing:
        raise ValueError("colunas ausentes para trajetória macro: " + ", ".join(sorted(missing)))
    assets = dict(zip(
        holdings[symbol_column].astype(str), holdings[sector_column].astype(str)
    ))
    # As tres telas que chamam isto passam a tabela que ja esta na tela -- e a
    # tabela da tela ja passou pelo tilt de hoje. Reaplicar por cima compoe dois
    # tilts: com impacto maximo, 0,500 vira 0,575 na tela e 0,639 na trajetoria,
    # +27,8% sobre o fundamental quando `max_relative_weight_tilt` vale 0,15.
    # O teto nao e violado por chamada nenhuma; e contornado pela composicao.
    # Pior que o numero, o rotulo: `weight_fundamental` recebia a base recebida,
    # que nesse caso e o peso ja inclinado -- a linha "fundamental" do grafico
    # nao era fundamental (memoria: procedencia-segue-a-decisao).
    #
    # Entao a trajetoria parte sempre do peso anterior ao macro quando ele veio
    # junto. Ausente a coluna, `weight` ja e o fundamental e nada muda.
    if "weight_before_macro" in holdings.columns:
        holdings = holdings.assign(weight=holdings["weight_before_macro"])
    elif "peso_fundamental" in holdings.columns:
        holdings = holdings.assign(weight=holdings["peso_fundamental"])
    rows: list[dict[str, object]] = []
    for cutoff in sorted(set(cutoffs)):
        snapshot = load_portfolio_macro_snapshot(
            engine,
            asset_class=asset_class,
            assets=assets,
            as_of=cutoff,
            knowledge_mode="reconstructed",
        )
        tilted = rebuild(holdings.copy(), snapshot.impacts, mode) if rebuild else apply_macro_tilt(
            holdings,
            snapshot.impacts,
            symbol_column=symbol_column,
            score_column=score_column,
            mode=mode,
        )
        if (symbol_column not in tilted.columns
                or tilted[symbol_column].astype(str).duplicated().any()
                or set(tilted[symbol_column].astype(str)) != set(assets)):
            raise ValueError("Reconstrução alterou a composição solicitada da carteira.")
        for _, item in tilted.iterrows():
            rows.append({
                "as_of": cutoff,
                "symbol": str(item[symbol_column]),
                "weight_fundamental": float(item["weight_before_macro"]),
                "weight_contextual": float(item["weight"]),
                "macro_impact": (
                    float(item["macro_impact"])
                    if pd.notna(item["macro_impact"]) else None
                ),
                "coverage": snapshot.coverage,
                "knowledge_mode": snapshot.knowledge_mode,
            })
    return pd.DataFrame(rows)

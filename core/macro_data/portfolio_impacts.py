"""Cálculo determinístico de impactos macro setoriais no Docker local."""

from sqlalchemy import text

from core.macro_data.exposure import AssetMacroExposure, assess_asset_impact
from core.macro_data.models import MacroObservation
from core.macro_data.portfolio_context import observation_applies_to_asset_class
from core.macro_data.signals import evaluate_observation


def persist_portfolio_impacts(connection) -> int:
    assets = connection.execute(
        text("SELECT * FROM macro_portfolio_assets WHERE sector IS NOT NULL")
    ).mappings().all()
    total = 0
    indicators = connection.execute(text("""
        SELECT provider, provider_code, country_code, category
          FROM macro_indicators
         WHERE category <> 'unmapped'
    """)).mappings()
    for indicator in indicators:
        observations = connection.execute(
            text("""
                SELECT reference_period, value, retrieved_at, country_code
                  FROM macro_observations
                 WHERE provider=:provider AND provider_code=:provider_code
                   AND COALESCE(country_code, '')=COALESCE(:country_code, '')
                   AND value IS NOT NULL
                 ORDER BY reference_period DESC
                 LIMIT 24
            """),
            {
                "provider": indicator["provider"],
                "provider_code": indicator["provider_code"],
                "country_code": indicator["country_code"],
            },
        ).mappings().all()[::-1]
        if len(observations) < 2:
            continue
        signal = evaluate_observation(
            [
                MacroObservation(
                    indicator["provider"],
                    indicator["provider_code"],
                    row["reference_period"],
                    float(row["value"]),
                    row["retrieved_at"],
                    country_code=row["country_code"],
                )
                for row in observations
            ],
            desirability=1,
        )
        for asset in assets:
            if not observation_applies_to_asset_class(
                str(asset["asset_class"]), indicator
            ):
                continue
            exposure = connection.execute(
                text("""
                    SELECT sensitivity, confidence, channel
                      FROM macro_sector_exposures
                     WHERE asset_class=:asset_class AND sector=:sector
                       AND factor=:factor
                """),
                {
                    "asset_class": asset["asset_class"],
                    "sector": asset["sector"],
                    "factor": indicator["category"],
                },
            ).mappings().first()
            if not exposure:
                continue
            impact = assess_asset_impact(
                signal,
                [AssetMacroExposure(
                    asset["symbol"],
                    indicator["category"],
                    float(exposure["sensitivity"]),
                    float(exposure["confidence"]),
                    exposure["channel"],
                )],
                factor=indicator["category"],
            )[0]
            connection.execute(
                text("""
                    INSERT INTO macro_portfolio_impacts (
                      asset_class, model_id, symbol, provider, provider_code,
                      factor, direction, intensity, confidence,
                      portfolio_weight, weighted_intensity
                    ) VALUES (
                      :asset_class, :model_id, :symbol, :provider,
                      :provider_code, :factor, :direction, :intensity,
                      :confidence, :portfolio_weight, :weighted_intensity
                    )
                """),
                {
                    "asset_class": asset["asset_class"],
                    "model_id": str(asset["model_id"]),
                    "symbol": asset["symbol"],
                    "provider": indicator["provider"],
                    "provider_code": indicator["provider_code"],
                    "factor": indicator["category"],
                    "direction": impact.direction,
                    "intensity": impact.intensity,
                    "confidence": impact.confidence,
                    "portfolio_weight": asset["weight"],
                    "weighted_intensity": round(
                        float(asset["weight"]) * impact.intensity, 4
                    ),
                },
            )
            total += 1
    return total

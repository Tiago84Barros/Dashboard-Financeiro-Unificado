"""Persistência append-only para observações e vintages macro."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from core.macro_data.models import MacroIndicator, MacroObservation, MacroRelease


def upsert_indicator(conn, indicator: MacroIndicator) -> int:
    return int(
        conn.execute(
            text("""
        INSERT INTO macro_indicators (canonical_code, provider_code, provider, name, description, country_code,
          category, unit, frequency, seasonal_adjustment, source_organization, source_url, active)
        VALUES (:canonical_code,:provider_code,:provider,:name,:description,:country_code,:category,:unit,:frequency,
          :seasonal_adjustment,:source_organization,:source_url,:active)
        ON CONFLICT (provider, provider_code, COALESCE(country_code, '')) DO NOTHING
    """),
            indicator.__dict__,
        ).rowcount
    )


def append_observation(conn, observation: MacroObservation) -> int:
    """Nunca atualiza valor anterior: nova revisão ganha uma linha/vintage próprio."""
    params = observation.__dict__.copy()
    params["retrieved_at"] = observation.retrieved_at.astimezone(timezone.utc)
    return int(
        conn.execute(
            text("""
        INSERT INTO macro_observations (provider, provider_code, country_code, reference_period, value, status,
          released_at, retrieved_at, provider_updated_at, is_preliminary, is_forecast, vintage_date, revision_number,
          raw_payload_reference)
        VALUES (:provider,:provider_code,:country_code,:reference_period,:value,:status,:released_at,:retrieved_at,
          :provider_updated_at,:is_preliminary,:is_forecast,:vintage_date,:revision_number,:raw_payload_reference)
        ON CONFLICT DO NOTHING
    """),
            params,
        ).rowcount
    )


def append_release(conn, release: MacroRelease) -> int:
    """Acrescenta uma divulgação sem substituir a versão anteriormente vista."""
    params = release.__dict__.copy()
    params["scheduled_at"] = release.scheduled_at.astimezone(timezone.utc)
    params["retrieved_at"] = release.retrieved_at.astimezone(timezone.utc)
    return int(
        conn.execute(
            text("""
        INSERT INTO macro_releases (provider, country_code, event_name, scheduled_at, retrieved_at,
          status, actual_value, previous_value, revised_previous_value, consensus_value, forecast_value,
          unit, importance, raw_payload_reference)
        VALUES (:provider,:country_code,:event_name,:scheduled_at,:retrieved_at,:status,:actual_value,
          :previous_value,:revised_previous_value,:consensus_value,:forecast_value,:unit,:importance,
          :raw_payload_reference)
        ON CONFLICT DO NOTHING
    """),
            params,
        ).rowcount
    )


def observations_known_at(
    conn,
    *,
    provider: str,
    provider_code: str,
    as_of: datetime,
    country_code: str | None = None,
):
    """Consulta point-in-time: não deixa backtest enxergar uma revisão futura."""
    return (
        conn.execute(
            text("""
      SELECT DISTINCT ON (reference_period) * FROM macro_observations
       WHERE provider=:provider AND provider_code=:provider_code
         AND (:country_code IS NULL OR country_code=:country_code)
         AND retrieved_at <= :as_of AND (released_at IS NULL OR released_at <= :as_of)
       ORDER BY reference_period, COALESCE(vintage_date, reference_period) DESC, retrieved_at DESC
    """),
            {
                "provider": provider,
                "provider_code": provider_code,
                "country_code": country_code,
                "as_of": as_of,
            },
        )
        .mappings()
        .all()
    )


def configured_indicator_frequency(
    conn, *, provider: str, provider_code: str, country_code: str | None = None
) -> str | None:
    return conn.execute(
        text("""
        SELECT frequency
          FROM macro_indicators
         WHERE provider=:provider AND provider_code=:provider_code
           AND (:country_code IS NULL OR country_code=:country_code)
         ORDER BY created_at DESC
         LIMIT 1
    """),
        {
            "provider": provider,
            "provider_code": provider_code,
            "country_code": country_code,
        },
    ).scalar()


def last_observation_retrieved_at(
    conn, *, provider: str, provider_code: str, country_code: str | None = None
):
    return conn.execute(
        text("""
        SELECT MAX(retrieved_at)
          FROM macro_observations
         WHERE provider=:provider AND provider_code=:provider_code
           AND (:country_code IS NULL OR country_code=:country_code)
    """),
        {
            "provider": provider,
            "provider_code": provider_code,
            "country_code": country_code,
        },
    ).scalar()

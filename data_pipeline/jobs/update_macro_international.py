"""Ingestão incremental das fontes macro habilitadas; falha parcial é esperada."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from uuid import uuid4

from core.config import settings
from core.macro_data.providers import ProviderError, configured_providers
from core.macro_data.repository import (
    append_observation,
    append_release,
    configured_indicator_frequency,
    last_observation_retrieved_at,
    upsert_indicator,
)
from core.macro_data.runtime import (
    checkpoint,
    finish_run,
    record_health,
    release_lock,
    start_run,
    try_acquire_lock,
)
from core.macro_data.schedule import observation_due
from core.macro_data.taxonomy import map_indicator

logger = logging.getLogger(__name__)
JOB_NAME = "update_macro_international"


def run(
    *,
    start: date | None = None,
    end: date | None = None,
    provider_names: set[str] | None = None,
    fred_vintages: bool = False,
    max_fred_vintages: int = 12,
) -> dict:
    result = {
        "status": "skipped",
        "table_name": "macro_indicators, macro_observations",
        "source_name": "Fontes macro oficiais",
        "job_name": JOB_NAME,
        "records_inserted": 0,
        "records_updated": 0,
        "records_failed": 0,
        "error_message": None,
    }
    providers = configured_providers(settings)
    if provider_names is not None:
        providers = {
            name: provider for name, provider in providers.items() if name in provider_names
        }
    if not providers:
        result["error_message"] = "nenhuma fonte macro internacional habilitada"
        return result
    from core.macro_data.database import get_local_macro_engine

    engine = get_local_macro_engine()
    if engine is None:
        result.update(
            status="failed", error_message="MACRO_LOCAL_DB_URL não configurada"
        )
        return result
    configured = settings.macro_series()
    mappings = settings.macro_indicator_mappings()
    end = end or date.today()
    errors = []
    with engine.connect() as conn:
        if not try_acquire_lock(conn):
            conn.rollback()
            result["error_message"] = "coleta já em execução no banco macro local"
            return result
        # pg_try_advisory_lock inicia uma transação implícita no SQLAlchemy.
        # O lock é de sessão e continua válido após o commit; encerra-se apenas
        # a transação de aquisição antes da transação de escrita.
        conn.commit()
        try:
            with conn.begin():
                run_id = start_run(conn, str(uuid4()))
                for provider_name, provider in providers.items():
                    inserted_before = result["records_inserted"]
                    failed_before = result["records_failed"]
                    error_type = None
                    health = provider.health_check()
                    record_health(conn, health, run_id)
                    if not health.available:
                        result["records_failed"] += 1
                        errors.append(f"{provider_name}:indisponível")
                        checkpoint(
                            conn,
                            run_id=run_id,
                            provider=provider_name,
                            status="failed",
                            records_failed=1,
                            error_type="health_check",
                        )
                        continue
                    try:
                        if provider_name == "trading_economics":
                            if start is not None:
                                checkpoint(
                                    conn,
                                    run_id=run_id,
                                    provider=provider_name,
                                    status="skipped",
                                    cursor_value="calendar_not_historical",
                                )
                                continue
                            for country in settings.macro_calendar_countries():
                                for release in provider.fetch_calendar(
                                    country, date.today(), date.today() + timedelta(days=14)
                                ):
                                    result["records_inserted"] += append_release(conn, release)
                        else:
                            for spec in configured.get(provider_name, ()):
                                code, country = spec["code"], spec.get("country")
                                frequency = configured_indicator_frequency(
                                    conn,
                                    provider=provider_name,
                                    provider_code=code,
                                    country_code=country,
                                )
                                if frequency is None:
                                    metadata = provider.fetch_metadata(code, country)
                                    for indicator in metadata:
                                        mapped = map_indicator(indicator, mappings)
                                        result["records_inserted"] += upsert_indicator(conn, mapped)
                                    frequency = metadata[0].frequency if metadata else "irregular"
                                if not observation_due(
                                    last_observation_retrieved_at(
                                        conn,
                                        provider=provider_name,
                                        provider_code=code,
                                        country_code=country,
                                    ),
                                    frequency,
                                ) and start is None:
                                    continue
                                query = type(
                                    "Q",
                                    (),
                                    {
                                        "provider_code": code,
                                        "country_code": country,
                                        "start": start,
                                        "end": end,
                                        "vintage_date": None,
                                    },
                                )()
                                observations = (
                                    provider.fetch_revisions(
                                        query,
                                        max_vintages=max(max_fred_vintages, 0),
                                    )
                                    if provider_name == "fred" and fred_vintages
                                    else provider.fetch_observations(query)
                                )
                                for observation in observations:
                                    result["records_inserted"] += append_observation(
                                        conn, observation
                                    )
                    except ProviderError as exc:
                        result["records_failed"] += 1
                        error_type = type(exc).__name__
                        errors.append(f"{provider_name}:{error_type}")
                    checkpoint(
                        conn,
                        run_id=run_id,
                        provider=provider_name,
                        status="completed" if result["records_failed"] == failed_before else "failed",
                        records_inserted=result["records_inserted"] - inserted_before,
                        records_failed=result["records_failed"] - failed_before,
                        error_type=error_type,
                    )
                result["status"] = (
                    "success"
                    if not errors
                    else ("partial_success" if result["records_inserted"] else "failed")
                )
                result["error_message"] = "; ".join(errors)[:500] or None
                finish_run(conn, run_id, result["status"], result["error_message"])
        finally:
            release_lock(conn)
            conn.commit()
    return result

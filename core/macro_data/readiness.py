"""Diagnóstico local de prontidão para cargas históricas, sem chamar APIs."""

from __future__ import annotations

from dataclasses import dataclass

_HISTORICAL_PROVIDERS = ("fred", "world_bank", "imf", "oecd", "bis", "ecb", "eurostat")
_SDMX_PROVIDERS = {"imf", "oecd", "bis", "ecb", "eurostat"}


@dataclass(frozen=True)
class ProviderReadiness:
    provider: str
    enabled: bool
    ready: bool
    detail: str


def backfill_readiness(settings) -> list[ProviderReadiness]:
    configured = settings.macro_series()
    rows = []
    for provider in _HISTORICAL_PROVIDERS:
        enabled = settings.macro_enabled(provider)
        series = configured.get(provider, ())
        if not enabled:
            rows.append(ProviderReadiness(provider, False, False, "fonte desabilitada"))
        elif not series:
            rows.append(ProviderReadiness(provider, True, False, "nenhuma série configurada"))
        elif provider == "fred" and not settings.FRED_API_KEY:
            rows.append(ProviderReadiness(provider, True, False, "credencial ausente"))
        elif provider in _SDMX_PROVIDERS and any("|" not in spec["code"] for spec in series):
            rows.append(
                ProviderReadiness(
                    provider, True, False, "série SDMX deve usar dataflow|chave"
                )
            )
        else:
            rows.append(ProviderReadiness(provider, True, True, "pronta para backfill"))
    return rows

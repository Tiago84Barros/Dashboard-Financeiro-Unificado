"""Normalização pura dos endpoints dedicados ``/api/v2/fii/*`` da Brapi.

O módulo só cria métricas semanticamente defensáveis. Emissor de CRI não é
tratado como devedor; imóvel não implica locatário; vencimento ausente não vira
duration zero. Todo backfill usa o instante da coleta como ``available_at``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Iterable
import unicodedata
from zoneinfo import ZoneInfo

from data_pipeline.market.fii_sources import metric_observation


SOURCE = "brapi_fii_v2"
VALID_TYPES = {"tijolo", "papel", "fof", "hibrido"}
_UF_REGION = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte",
    "RR": "Norte", "TO": "Norte", "AL": "Nordeste", "BA": "Nordeste",
    "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste", "PE": "Nordeste",
    "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste", "DF": "Centro-Oeste",
    "GO": "Centro-Oeste", "MT": "Centro-Oeste", "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _available_at(payload: dict) -> datetime:
    raw = payload.get("requestedAt")
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _published_at(value: Any) -> datetime | None:
    """Converte uma data de entrega em cutoff conservador no fim do dia BRT."""
    parsed = _date(value)
    if parsed is None:
        return None
    local = datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59,
                     tzinfo=ZoneInfo("America/Sao_Paulo"))
    return local.astimezone(timezone.utc)


def _ticker(row: dict) -> str:
    return str(row.get("symbol") or "").strip().upper().replace(".SA", "")


def _security_part(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return str(int(raw)) if raw.isdigit() else raw.upper()


def cri_security_key(item: dict) -> str | None:
    """Chave conciliavel com o informe mensal de CRI da CVM."""
    issuer = re.sub(r"\D", "", str(item.get("issuerCnpj") or ""))
    issue = _security_part(item.get("issue"))
    series = _security_part(item.get("series"))
    if issuer and issue and series:
        return f"{issuer}|{issue}|{series}"
    identifier = _security_part(item.get("ticker") or item.get("identifier"))
    if identifier.startswith("BR") and len(identifier) >= 10:
        return identifier
    return None


def normalize_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    text = "".join(char for char in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(char))
    aliases = {"tijolo": "tijolo", "papel": "papel", "fof": "fof",
               "fundo de fundos": "fof", "hibrido": "hibrido"}
    return aliases.get(text)


def _observation(ticker: str, metric: str, value: Any, reference_date: date,
                 available_at: datetime, raw_payload_id: int | None, *,
                 endpoint: str, vintage: str | None = None,
                 metadata: dict | None = None,
                 source_published_at: datetime | None = None,
                 availability_quality: str | None = None) -> dict | None:
    number = _num(value)
    normalized: Any = number
    if number is None and isinstance(value, str) and value.strip():
        normalized = value.strip()
    if not ticker or normalized is None:
        return None
    quality = availability_quality or (
        "retrospective_backfill" if endpoint.endswith("/history") else "first_observed_proxy")
    row = metric_observation(
        ticker=ticker, metric_name=metric, value=normalized,
        reference_date=reference_date, available_at=available_at,
        source=SOURCE, raw_payload_id=raw_payload_id,
        vintage=vintage or reference_date.isoformat(),
        metadata={"endpoint": endpoint, **(metadata or {})},
        source_published_at=source_published_at,
        availability_quality=quality,
    )
    if number is not None:
        invalid = (
            metric in {"vacancia_fisica", "vacancia_financeira", "property_delinquency",
                       "holdings_overlap", "income_recurrence",
                       "portfolio_income_recurrence", "ltv"} and not 0 <= number <= 1
        ) or (metric == "leverage" and number < 0) or (
            metric in {"dy_12m", "dy_1m"} and not 0 <= number <= .60) or (
            metric == "pvp" and not 0 < number <= 10)
        if invalid:
            row["quality_status"] = "rejected"
            metadata_value = json.loads(row["metadata_json"])
            metadata_value["rejection_reason"] = "metric_domain_violation"
            row["metadata_json"] = json.dumps(metadata_value, ensure_ascii=False)
    return row


def _exposure(*, ticker: str, exposure_type: str, exposure_name: Any,
              exposure_weight: Any, reference_date: date, available_at: datetime,
              raw_payload_id: int | None, endpoint: str, vintage: str,
              metadata: dict | None = None,
              availability_quality: str | None = None) -> dict | None:
    name = str(exposure_name or "").strip()
    weight = _num(exposure_weight)
    if not ticker or not name or weight is None or not 0 <= weight <= 1:
        return None
    quality = availability_quality or (
        "retrospective_backfill" if endpoint.endswith("/history") else "first_observed_proxy")
    semantic = json.dumps({"ticker": ticker, "type": exposure_type, "name": name,
                           "weight": weight, "reference": str(reference_date),
                           "vintage": vintage}, sort_keys=True, separators=(",", ":"))
    return {
        "ticker": ticker, "exposure_type": exposure_type,
        "exposure_name": name[:300], "exposure_weight": weight,
        "reference_date": reference_date, "available_at": available_at,
        "knowledge_at": available_at, "availability_quality": quality,
        "content_hash": hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
        "vintage": vintage, "source": SOURCE, "raw_payload_id": raw_payload_id,
        "metadata_json": json.dumps({"endpoint": endpoint, **(metadata or {})},
                                     ensure_ascii=False, default=str),
    }


def normalize_indicators(payload: dict, raw_payload_id: int | None = None) -> dict:
    available = _available_at(payload)
    updates, observations, exposures = [], [], []
    for item in payload.get("fiis") or []:
        ticker = _ticker(item)
        reference = _date(item.get("asOfDate")) or available.date()
        fii_type = normalize_type(item.get("segmentType"))
        pvp_raw = _num(item.get("priceToNav"))
        dy_raw = _num(item.get("dividendYield12m"))
        pvp = pvp_raw if pvp_raw is not None and 0 < pvp_raw <= 10 else None
        dy = dy_raw if dy_raw is not None and 0 <= dy_raw <= .60 else None
        update = {
            "ticker": ticker, "name": item.get("name"), "cnpj": item.get("cnpj"),
            "tipo": fii_type, "segmento_cvm": item.get("segmentoAtuacao"),
            "tipo_gestao": item.get("tipoGestao"), "mandate": item.get("mandate"),
            "administrator_name": item.get("administratorName"),
            "administrator_cnpj": item.get("administratorCnpj"),
            "price": _num(item.get("price")),
            "pvp": pvp, "dy_12m": dy, "vpa": _num(item.get("navPerShare")),
            "patrimonio_liquido": _num(item.get("equity")),
            "num_cotistas": _num(item.get("totalInvestors")),
            "cvm_ref_date": reference,
        }
        updates.append({key: value for key, value in update.items() if value is not None})
        for metric, field in (
            ("price", "price"), ("nav_per_share", "navPerShare"),
            ("pvp", "priceToNav"), ("dy_12m", "dividendYield12m"),
            ("dy_1m", "dividendYield1m"), ("monthly_return", "monthlyReturn"),
            ("total_investors", "totalInvestors"),
            ("shares_outstanding", "sharesOutstanding"), ("equity", "equity"),
            ("total_assets", "totalAssets"),
        ):
            observation = _observation(ticker, metric, item.get(field), reference, available,
                                       raw_payload_id, endpoint="indicators")
            if observation:
                observations.append(observation)
        if fii_type == "fof" and pvp is not None:
            observation = _observation(ticker, "nav_discount", 1.0 - pvp, reference,
                                       available, raw_payload_id, endpoint="indicators",
                                       metadata={"formula": "1-priceToNav"})
            if observation:
                observations.append(observation)
        manager = item.get("administratorCnpj") or item.get("administratorName")
        exposure = _exposure(
            ticker=ticker, exposure_type="manager", exposure_name=manager,
            exposure_weight=1.0, reference_date=reference, available_at=available,
            raw_payload_id=raw_payload_id, endpoint="indicators",
            vintage=f"indicators:{reference}",
            metadata={"administrator_name": item.get("administratorName"),
                      "administrator_cnpj": item.get("administratorCnpj")},
        )
        if exposure:
            exposures.append(exposure)
    return {"fii_updates": updates, "observations": observations, "exposures": exposures}


def normalize_historical(payload: dict, raw_payload_id: int | None = None) -> list[dict]:
    """Normaliza OHLCV diario sem transformar backfill em historico PIT."""
    available = _available_at(payload)
    rows: list[dict] = []
    for fund in payload.get("fiis") or []:
        ticker = _ticker(fund)
        if not ticker:
            continue
        for item in fund.get("historicalDataPrice") or []:
            raw_date = item.get("date")
            try:
                if isinstance(raw_date, (int, float)) or str(raw_date).isdigit():
                    price_date = datetime.fromtimestamp(float(raw_date), tz=timezone.utc).date()
                else:
                    price_date = _date(raw_date)
            except (OSError, OverflowError, ValueError):
                price_date = None
            close = _num(item.get("close"))
            adjusted = _num(item.get("adjustedClose"))
            if price_date is None or close is None or close <= 0:
                continue
            quality = ("first_observed_proxy"
                       if (available.date() - price_date).days <= 7
                       else "retrospective_backfill")
            semantic = json.dumps({
                "ticker": ticker, "date": price_date.isoformat(),
                "open": _num(item.get("open")), "high": _num(item.get("high")),
                "low": _num(item.get("low")), "close": close,
                "adjusted_close": adjusted, "volume": item.get("volume"),
                "source": SOURCE,
            }, sort_keys=True, separators=(",", ":"), default=str)
            rows.append({
                "ticker": ticker, "date": price_date,
                "open": _num(item.get("open")), "high": _num(item.get("high")),
                "low": _num(item.get("low")), "close": close,
                "adjusted_close": adjusted, "volume": int(_num(item.get("volume")) or 0),
                "source": SOURCE, "raw_payload_id": raw_payload_id,
                "available_at": available, "knowledge_at": available,
                "availability_quality": quality,
                "content_hash": hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
            })
    return rows


def normalize_indicator_history(payload: dict, raw_payload_id: int | None = None) -> list[dict]:
    available = _available_at(payload)
    observations: list[dict] = []
    for item in payload.get("history") or []:
        ticker = _ticker(item)
        reference = _date(item.get("referenceDate"))
        if not ticker or reference is None:
            continue
        for metric, field in (
            ("price", "price"), ("nav_per_share", "navPerShare"),
            ("pvp", "priceToNav"), ("dy_12m", "dividendYield12m"),
            ("dy_1m", "dividendYield1m"), ("monthly_return", "monthlyReturn"),
            ("total_investors", "totalInvestors"),
            ("shares_outstanding", "sharesOutstanding"), ("equity", "equity"),
            ("total_assets", "totalAssets"),
        ):
            observation = _observation(ticker, metric, item.get(field), reference, available,
                                       raw_payload_id, endpoint="indicators/history",
                                       vintage=f"backfill:{reference.isoformat()}")
            if observation:
                observations.append(observation)
    return observations


def _recurrence(values: list[float]) -> float | None:
    if len(values) < 6:
        return None
    positive_share = sum(value > 0 for value in values) / len(values)
    mean = sum(values) / len(values)
    if mean <= 0:
        return None
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    cv = math.sqrt(variance) / mean
    return max(0.0, min(1.0, positive_share / (1.0 + cv)))


def normalize_reports(payload: dict, raw_payload_id: int | None = None) -> list[dict]:
    available = _available_at(payload)
    observations: list[dict] = []
    reports_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for item in payload.get("reports") or []:
        ticker = _ticker(item)
        reference = _date(item.get("referenceDate"))
        if not ticker or reference is None:
            continue
        reports_by_ticker[ticker].append(item)
        assets = _num(item.get("totalAssets"))
        liabilities = _num(item.get("totalLiabilities"))
        leverage = liabilities / assets if assets and assets > 0 and liabilities is not None else None
        liquid_assets = sum(_num(item.get(field)) or 0 for field in
                            ("cash", "governmentBonds", "privateBonds", "fixedIncomeFunds"))
        liquidity_ratio = liquid_assets / assets if assets and assets > 0 else None
        monthly_fee = _num(item.get("adminFeeRate"))
        annual_fee = monthly_fee * 12 if monthly_fee is not None and monthly_fee >= 0 else None
        fee_efficiency = max(0.0, 1.0 - annual_fee / .02) if annual_fee is not None else None
        for metric, value, formula in (
            ("leverage", leverage, "totalLiabilities/totalAssets"),
            ("admin_fee_rate_annual", annual_fee, "adminFeeRate*12"),
            ("fee_efficiency", fee_efficiency, "max(0,1-adminFeeRate*12/0.02)"),
            ("liquidity_ratio", liquidity_ratio,
             "(cash+governmentBonds+privateBonds+fixedIncomeFunds)/totalAssets"),
            ("rental_receivables", _num(item.get("rentalReceivables")), "reported"),
            ("real_estate_obligations", _num(item.get("realEstateObligations")), "reported"),
        ):
            observation = _observation(ticker, metric, value, reference, available,
                                       raw_payload_id, endpoint="reports",
                                       vintage=f"report:{reference.isoformat()}:v{item.get('version', 1)}",
                                       metadata={"formula": formula})
            if observation:
                observations.append(observation)
    for ticker, items in reports_by_ticker.items():
        ordered = sorted(items, key=lambda row: _date(row.get("referenceDate")) or date.min)
        yields = [_num(item.get("monthlyDividendYield")) for item in ordered]
        values = [value for value in yields if value is not None and value >= 0]
        recurrence = _recurrence(values)
        reference = _date(ordered[-1].get("referenceDate")) or available.date()
        for metric in ("income_recurrence", "portfolio_income_recurrence"):
            observation = _observation(ticker, metric, recurrence, reference, available,
                                       raw_payload_id, endpoint="reports",
                                       metadata={"formula": "positive_share/(1+coefficient_of_variation)",
                                                 "months": len(values)})
            if observation:
                observations.append(observation)
    return observations


def normalize_annual_reports(payload: dict, raw_payload_id: int | None = None) -> dict:
    """Extrai campos governança/mandato preservando a entrega oficial."""
    collected = _available_at(payload)
    updates: list[dict] = []
    observations: list[dict] = []
    for item in payload.get("reports") or []:
        ticker = _ticker(item)
        fields = item.get("fields") or {}
        reference = _date(fields.get("Data_Referencia") or item.get("referenceDate"))
        if not ticker or reference is None:
            continue
        published = _published_at(fields.get("Data_Entrega"))
        version = fields.get("Versao") or item.get("year") or 1
        quality = "verified_publication" if published else "retrospective_backfill"
        updates.append({
            "ticker": ticker, "mandate": fields.get("Mandato"),
            "tipo_gestao": fields.get("Tipo_Gestao"),
        })
        candidates = {
            "mandate": fields.get("Mandato"),
            "management_type": fields.get("Tipo_Gestao"),
            "target_audience": fields.get("Publico_Alvo"),
            "fund_duration_profile": fields.get("Prazo_Duracao"),
            "exclusive_fund_flag": fields.get("Fundo_Exclusivo"),
            "operating_since": fields.get("Data_Funcionamento"),
        }
        for metric, value in candidates.items():
            observation = _observation(
                ticker, metric, value, reference, collected, raw_payload_id,
                endpoint="annual-reports", vintage=f"annual:{reference}:v{version}",
                source_published_at=published, availability_quality=quality,
                metadata={"year": item.get("year")})
            if observation:
                observations.append(observation)
    return {"fii_updates": updates, "observations": observations}


def normalize_financials(payload: dict, raw_payload_id: int | None = None) -> dict:
    """Registra DFIN e seus links públicos como documentos versionáveis."""
    collected = _available_at(payload)
    documents: list[dict] = []
    observations: list[dict] = []
    for item in payload.get("financials") or []:
        ticker = _ticker(item)
        fields = item.get("fields") or {}
        reference = _date(fields.get("Data_Referencia") or item.get("referenceDate"))
        url = str(fields.get("Link_Download") or "").strip()
        if not ticker or reference is None:
            continue
        published = _published_at(fields.get("Data_Entrega"))
        version = fields.get("Versao") or 1
        natural_key = f"{ticker}|{item.get('documentType') or 'DFIN'}|{reference}|v{version}"
        if url:
            documents.append({
                "ticker": ticker, "document_type": item.get("documentType") or "DFIN",
                "natural_key": natural_key, "reference_date": reference,
                "source_published_at": published, "first_observed_at": collected,
                "source_url": url, "raw_payload_id": raw_payload_id,
                "metadata": {"year": item.get("year"), "version": version},
            })
        auditor = fields.get("Parecer_Auditor")
        observation = _observation(
            ticker, "auditor_opinion", auditor, reference, collected, raw_payload_id,
            endpoint="financials", vintage=f"dfin:{reference}:v{version}",
            source_published_at=published,
            availability_quality=("verified_publication" if published else
                                  "retrospective_backfill"),
            metadata={"document_type": item.get("documentType"), "source_url": url})
        if observation:
            observations.append(observation)
    return {"documents": documents, "observations": observations}


def normalize_properties(payload: dict, raw_payload_id: int | None = None) -> dict:
    available = _available_at(payload)
    observations, properties, exposures = [], [], []
    for fund in payload.get("fiis") or []:
        ticker = _ticker(fund)
        reference = _date(fund.get("referenceDate")) or available.date()
        version = fund.get("version", 1)
        summary = fund.get("summary") or {}
        observation = _observation(ticker, "vacancia_fisica", summary.get("vacancyRate"),
                                   reference, available, raw_payload_id,
                                   endpoint="properties", vintage=f"properties:{reference}:v{version}")
        if observation:
            observations.append(observation)
        delinquency_total = 0.0
        delinquency_weight = 0.0
        region_values: dict[str, float] = defaultdict(float)
        property_values: dict[str, float] = defaultdict(float)
        for item in fund.get("properties") or []:
            name = str(item.get("name") or item.get("identifier") or "").strip()
            if not name:
                continue
            address = str(item.get("address") or "")
            matches = re.findall(r"\b(" + "|".join(_UF_REGION) + r")\b", address.upper())
            uf = matches[-1] if matches else None
            revenue_share = _num(item.get("revenueShare"))
            delinquency = _num(item.get("delinquencyRate"))
            if revenue_share is not None and revenue_share > 0:
                property_values[name] += revenue_share
                if uf:
                    region_values[_UF_REGION[uf]] += revenue_share
                if delinquency is not None and 0 <= delinquency <= 1:
                    delinquency_total += delinquency * revenue_share
                    delinquency_weight += revenue_share
            properties.append({
                "ticker": ticker, "nome_imovel": name[:300],
                "area_m2": _num(item.get("area")), "vacancia": _num(item.get("vacancyRate")),
                "uf": uf, "regiao": _UF_REGION.get(uf),
                "segmento_imovel": item.get("propertyClass"),
                "pct_receita": revenue_share, "fonte": SOURCE,
            })
        property_total = sum(property_values.values())
        if property_total >= .60:
            property_weights = [value / property_total for value in property_values.values()]
            observation = _observation(
                ticker, "property_diversification",
                1.0 - sum(weight * weight for weight in property_weights),
                reference, available, raw_payload_id, endpoint="properties",
                vintage=f"properties:{reference}:v{version}",
                metadata={"formula": "1-HHI", "revenue_share_coverage": property_total})
            if observation:
                observations.append(observation)
        if delinquency_weight >= .60:
            observation = _observation(
                ticker, "property_delinquency", delinquency_total / delinquency_weight,
                reference, available, raw_payload_id, endpoint="properties",
                vintage=f"properties:{reference}:v{version}",
                metadata={"revenue_share_coverage": delinquency_weight})
            if observation:
                observations.append(observation)
        region_total = sum(region_values.values())
        if region_total >= .60:
            region_weights = [value / region_total for value in region_values.values()]
            observation = _observation(
                ticker, "geographic_diversification",
                1.0 - sum(weight * weight for weight in region_weights),
                reference, available, raw_payload_id, endpoint="properties",
                vintage=f"properties:{reference}:v{version}",
                metadata={"formula": "1-HHI", "revenue_share_coverage": region_total})
            if observation:
                observations.append(observation)
            for region, value in region_values.items():
                exposure = _exposure(
                    ticker=ticker, exposure_type="region", exposure_name=region,
                    exposure_weight=value / region_total, reference_date=reference,
                    available_at=available, raw_payload_id=raw_payload_id,
                    endpoint="properties", vintage=f"properties:{reference}:v{version}",
                    metadata={"revenue_share_coverage": region_total})
                if exposure:
                    exposures.append(exposure)
    return {"observations": observations, "properties": properties,
            "exposures": exposures}


def normalize_property_history(payload: dict, raw_payload_id: int | None = None) -> list[dict]:
    available = _available_at(payload)
    observations: list[dict] = []
    for item in payload.get("history") or []:
        ticker = _ticker(item)
        reference = _date(item.get("referenceDate"))
        if not ticker or reference is None:
            continue
        observation = _observation(
            ticker, "vacancia_fisica", (item.get("summary") or {}).get("vacancyRate"),
            reference, available, raw_payload_id, endpoint="properties/history",
            vintage=f"backfill:{reference}:v{item.get('version', 1)}")
        if observation:
            observations.append(observation)
    return observations


def _weighted_exposures(items: Iterable[dict], name_fn) -> tuple[dict[str, float], float]:
    amounts: dict[str, float] = defaultdict(float)
    total = 0.0
    for item in items:
        value = _num(item.get("value"))
        name = name_fn(item)
        if value is None or value <= 0 or not name or item.get("confidential"):
            continue
        amounts[str(name)] += value
        total += value
    return ({name: value / total for name, value in amounts.items()} if total > 0 else {}), total


def normalize_portfolio(payload: dict, raw_payload_id: int | None = None) -> dict:
    available = _available_at(payload)
    observations, exposures, type_inferences = [], [], []
    for fund in payload.get("fiis") or []:
        ticker = _ticker(fund)
        reference = _date(fund.get("referenceDate")) or available.date()
        version = fund.get("version", 1)
        vintage = f"portfolio:{reference}:v{version}"
        allocation_types: set[str] = set()
        allocations = fund.get("allocations") or []
        for allocation in allocations:
            asset_class = str(allocation.get("assetClass") or "").lower()
            count = _num(allocation.get("count")) or 0
            if count <= 0:
                continue
            if asset_class in {"real_estate", "property", "land", "right"}:
                allocation_types.add("tijolo")
            elif asset_class in {"cri", "lci", "receivable", "private_bond"}:
                allocation_types.add("papel")
            elif asset_class in {"fund_share", "fii", "fund_holding"}:
                allocation_types.add("fof")
        if allocation_types:
            inferred_type = (next(iter(allocation_types)) if len(allocation_types) == 1
                             else "hibrido")
            type_inferences.append({"ticker": ticker, "tipo": inferred_type,
                                    "reference_date": reference, "raw_payload_id": raw_payload_id})
        financial = fund.get("financialAssets") or []
        allocation_weights, allocation_value = _weighted_exposures(
            allocations, lambda item: item.get("assetClass"))
        issuer_weights, known_issuer_value = _weighted_exposures(
            financial, lambda item: item.get("issuerCnpj") or item.get("issuer"))
        issue_weights, _ = _weighted_exposures(
            financial, lambda item: item.get("identifier") or
            "|".join(str(item.get(key) or "") for key in ("issuerCnpj", "issue", "series")))
        holding_weights, known_holding_value = _weighted_exposures(
            fund.get("fundHoldings") or [],
            lambda item: item.get("issuerCnpj") or item.get("identifier") or item.get("name"))
        for exposure_type, mapping, known_value in (
            ("asset_class", allocation_weights, allocation_value),
            ("issuer", issuer_weights, known_issuer_value),
            ("holding", holding_weights, known_holding_value),
        ):
            for name, weight in mapping.items():
                exposure = _exposure(
                    ticker=ticker, exposure_type=exposure_type, exposure_name=name,
                    exposure_weight=weight, reference_date=reference,
                    available_at=available, raw_payload_id=raw_payload_id,
                    endpoint="portfolio", vintage=vintage,
                    metadata={"known_value": known_value},
                )
                if exposure:
                    exposures.append(exposure)
        security_items: dict[str, dict] = {}
        security_total = 0.0
        for item in financial:
            value = _num(item.get("value"))
            key = cri_security_key(item)
            if value is None or value <= 0 or not key or item.get("confidential"):
                continue
            security_total += value
            grouped = security_items.setdefault(key, {"item": item, "value": 0.0})
            grouped["value"] += value
        for key, grouped in security_items.items():
            item, value = grouped["item"], grouped["value"]
            exposure = _exposure(
                ticker=ticker, exposure_type="security", exposure_name=key,
                exposure_weight=value / security_total, reference_date=reference,
                available_at=available, raw_payload_id=raw_payload_id,
                endpoint="portfolio", vintage=vintage,
                metadata={
                    "known_value": security_total,
                    "linkable_portfolio_coverage": (
                        min(security_total / known_issuer_value, 1.0)
                        if known_issuer_value > 0 else 0.0),
                    "identifier": item.get("identifier"), "issuer": item.get("issuer"),
                    "issuer_cnpj": item.get("issuerCnpj"), "issue": item.get("issue"),
                    "series": item.get("series"), "maturity_date": item.get("maturityDate"),
                })
            if exposure:
                exposures.append(exposure)
        concentration = max(issue_weights.values()) if issue_weights else None
        observation = _observation(ticker, "issuance_concentration", concentration,
                                   reference, available, raw_payload_id, endpoint="portfolio",
                                   vintage=vintage, metadata={"formula": "max_issue_value_share"})
        if observation:
            observations.append(observation)
        maturity_total = 0.0
        maturity_value = 0.0
        for item in financial:
            value = _num(item.get("value"))
            maturity = _date(item.get("maturityDate"))
            if value and value > 0 and maturity:
                maturity_value += value
                maturity_total += value * max((maturity - reference).days / 365.25, 0)
        duration_coverage = maturity_value / known_issuer_value if known_issuer_value > 0 else 0
        if duration_coverage >= .60:
            observation = _observation(
                ticker, "duration_anos", maturity_total / maturity_value, reference,
                available, raw_payload_id, endpoint="portfolio", vintage=vintage,
                metadata={"formula": "weighted_years_to_maturity",
                          "maturity_value_coverage": duration_coverage})
            if observation:
                observations.append(observation)
    return {"observations": observations, "exposures": exposures,
            "type_inferences": type_inferences}


def normalize_portfolio_history(payload: dict, raw_payload_id: int | None = None) -> dict:
    """Normaliza a série trimestral que a Brapi Pro expõe por classe de ativo."""
    available = _available_at(payload)
    observations: list[dict] = []
    exposures: list[dict] = []
    for fund in payload.get("history") or []:
        ticker = _ticker(fund)
        reference = _date(fund.get("referenceDate"))
        if not ticker or reference is None:
            continue
        version = fund.get("version", 1)
        vintage = f"portfolio-history:{reference}:v{version}"
        allocations = fund.get("allocations") or []
        weights, declared = _weighted_exposures(allocations, lambda row: row.get("assetClass"))
        for name, weight in weights.items():
            exposure = _exposure(
                ticker=ticker, exposure_type="asset_class", exposure_name=name,
                exposure_weight=weight, reference_date=reference,
                available_at=available, raw_payload_id=raw_payload_id,
                endpoint="portfolio/history", vintage=vintage,
                metadata={"declared_allocation_value": declared},
                availability_quality="retrospective_backfill",
            )
            if exposure:
                exposures.append(exposure)
        summary = fund.get("summary") or {}
        properties = summary.get("properties") or {}
        financial = summary.get("financialAssets") or {}
        for metric, value in (
            ("portfolio_declared_value", summary.get("declaredValue")),
            ("portfolio_item_count", summary.get("totalItems")),
            ("property_count", properties.get("count")),
            ("vacancia_fisica", properties.get("vacancyRate")),
            ("financial_asset_count", financial.get("count")),
            ("financial_asset_declared_value", financial.get("declaredValue")),
        ):
            observation = _observation(
                ticker, metric, value, reference, available, raw_payload_id,
                endpoint="portfolio/history", vintage=vintage,
                availability_quality="retrospective_backfill",
            )
            if observation:
                observations.append(observation)
    return {"observations": observations, "exposures": exposures,
            "type_inferences": []}


def normalize_dividends(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for item in payload.get("dividends") or []:
        ticker = _ticker(item)
        amount = _num(item.get("rate"))
        payment = _date(item.get("paymentDate"))
        ex_date = _date(item.get("lastDatePrior") or item.get("approvedOn"))
        if ticker and amount and amount > 0 and (payment or ex_date):
            rows.append({"ticker": ticker, "payment_date": payment, "ex_date": ex_date,
                         "amount": amount, "type": str(item.get("label") or "RENDIMENTO")[:40],
                         "source": SOURCE})
    return rows


def income_metrics_from_monthly(monthly: dict[str, dict[date, float]], *,
                                as_of: date) -> list[dict]:
    """Deriva recorrência e crescimento de renda sem usar preços futuros."""
    available = datetime.now(timezone.utc)
    observations: list[dict] = []
    for ticker, values_by_month in monthly.items():
        if not values_by_month:
            continue
        last_month = date(as_of.year, as_of.month, 1)
        series: list[float] = []
        cursor = last_month
        for offset in range(36):
            year = cursor.year
            month = cursor.month - offset
            while month <= 0:
                year -= 1
                month += 12
            series.append(float(values_by_month.get(date(year, month, 1), 0.0)))
        recurrence = _recurrence(list(reversed(series)))
        first_12 = sum(series[24:36])
        last_12 = sum(series[0:12])
        populated_months = sum(value > 0 for value in series)
        growth = ((last_12 / first_12) ** .5 - 1.0
                  if first_12 > 0 and last_12 > 0 and populated_months >= 24 else None)
        if growth is not None:
            growth = max(-1.0, min(1.0, growth))
        for metric, value, formula in (
            ("income_recurrence", recurrence, "positive_share/(1+cv),36m"),
            ("portfolio_income_recurrence", recurrence, "positive_share/(1+cv),36m"),
            ("income_growth_per_share_3y", growth, "cagr(first12m,last12m),36m"),
        ):
            observation = _observation(ticker, metric, value, as_of, available, None,
                                       endpoint="derived/dividends", vintage=f"derived:{as_of}",
                                       metadata={"formula": formula,
                                                 "populated_months": populated_months})
            if observation:
                observation["source"] = "brapi_fii_v2_derived"
                observations.append(observation)
    return observations

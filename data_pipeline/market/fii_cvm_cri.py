"""Look-through auditavel de CRIs detidos por FIIs.

Os informes mensais de securitizadoras sao preservados por hash e versao. A
conciliacao usa apenas chaves regulatorias fortes (CNPJ emissora + emissao +
serie, ou ISIN/CETIP). Agregados de FII exigem ao menos 60% de cobertura da
carteira identificavel; ausencia nunca vira zero.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

import pandas as pd
from sqlalchemy import text

from data_pipeline.market.fii_cvm_structured import (
    CvmArchive, _date, _digits, _num, _published, _ratio, _text,
    archive_manifest,
)
from data_pipeline.market.fii_sources import metric_observation


SOURCE = "cvm_cri_monthly"
LOOKTHROUGH_SOURCE = "cvm_cri_lookthrough"
PARSER_VERSION = "1.1.0"
PARSER_NAME = "cvm_cri_structured"
ENDPOINT = "securit-doc-inf_mensal_cri"
URL = "https://dados.cvm.gov.br/dados/SECURIT/DOC/INF_MENSAL_CRI/DADOS/inf_mensal_cri_{year}.zip"
CACHE_ROOT = Path("local_staging/fii_cvm_cri")


def _security_part(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return str(int(raw)) if raw.isdigit() else raw.upper()


def security_key(issuer_cnpj, issue, series, certificate=None) -> str | None:
    issuer = _digits(issuer_cnpj)
    issue_part, series_part = _security_part(issue), _security_part(series)
    if issuer and issue_part and series_part:
        return f"{issuer}|{issue_part}|{series_part}"
    certificate_part = _security_part(certificate)
    return certificate_part if certificate_part.startswith("BR") else None


def fetch_cri_archive(year: int, *, timeout: int = 120) -> CvmArchive | None:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    url = URL.format(year=year)
    cache = CACHE_ROOT / f"{year}.zip"
    headers_file = cache.with_suffix(".zip.headers.json")
    session = requests.Session()
    session.headers["User-Agent"] = "DashboardFinanceiro/1.0 (+cvm-cri-pit)"
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=.8, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), respect_retry_after_header=True)))
    cached_headers = (json.loads(headers_file.read_text(encoding="utf-8"))
                      if cache.exists() and headers_file.exists() else {})
    conditional = {}
    if cached_headers.get("etag"):
        conditional["If-None-Match"] = cached_headers["etag"]
    if cached_headers.get("last-modified"):
        conditional["If-Modified-Since"] = cached_headers["last-modified"]
    try:
        response = session.get(url, timeout=timeout, headers=conditional)
        if response.status_code == 304 and cache.exists():
            content = cache.read_bytes()
            return CvmArchive("cri", year, url, content, datetime.now(timezone.utc),
                              cached_headers, hashlib.sha256(content).hexdigest(), True)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        content = response.content
        safe_headers = {key.lower(): str(value) for key, value in response.headers.items()
                        if key.lower() in {"etag", "last-modified", "content-length", "content-type"}}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
        headers_file.write_text(json.dumps(safe_headers, ensure_ascii=False), encoding="utf-8")
        return CvmArchive("cri", year, url, content, datetime.now(timezone.utc),
                          safe_headers, hashlib.sha256(content).hexdigest())
    except requests.RequestException:
        if not cache.exists():
            return None
        content = cache.read_bytes()
        headers = (json.loads(headers_file.read_text(encoding="utf-8"))
                   if headers_file.exists() else {})
        return CvmArchive("cri", year, url, content, datetime.now(timezone.utc),
                          headers, hashlib.sha256(content).hexdigest(), True)


def _tables(archive: CvmArchive) -> dict[str, pd.DataFrame]:
    result: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        for name in zipped.namelist():
            if not name.lower().endswith(".csv"):
                continue
            stem = re.sub(r"_\d{4}$", "", Path(name).stem)
            with zipped.open(name) as stream:
                result[stem] = pd.read_csv(
                    stream, sep=";", encoding="latin-1", dtype=str,
                    keep_default_na=False, low_memory=False)
    return result


def _duration_years(general: dict, credit: dict, reference: date,
                    maturity: date | None) -> float | None:
    years, months = _num(general.get("Anos_Duration_Carteira")), _num(general.get("Meses_Duration_Carteira"))
    if years is not None or months is not None:
        value = max(years or 0, 0) + max(months or 0, 0) / 12
        if 0 < value <= 100:
            return value
    raw = _text(credit.get("Duration_Carteira")) or ""
    number = r"(\d+(?:[.,]\d+)?)"
    year_match = re.search(number + r"\s*ano", raw, re.I)
    month_match = re.search(number + r"\s*m[eê]s", raw, re.I)
    day_match = re.search(number + r"\s*dia", raw, re.I)
    if year_match or month_match or day_match:
        def parse(match) -> float:
            return float(match.group(1).replace(",", ".")) if match else 0.0

        value = parse(year_match) + parse(month_match) / 12 + parse(day_match) / 365.25
        if 0 < value <= 100:
            return value
    if maturity and maturity > reference:
        return (maturity - reference).days / 365.25
    return None


def _indexer_profile(*values) -> dict[str, float] | None:
    raw = " ".join(str(value or "") for value in values).upper()
    aliases = (("IPCA", "IPCA"), ("CDI", "CDI"), ("IGP-M", "IGP-M"),
               ("IGPM", "IGP-M"), ("INCC", "INCC"), ("TR", "TR"),
               ("PRE", "PREFIXADO"), ("PREFIX", "PREFIXADO"))
    found = []
    for needle, label in aliases:
        if needle in raw and label not in found:
            found.append(label)
    if not found:
        return None
    weight = 1 / len(found)
    return {label: weight for label in found}


def _credit_spread(*values) -> float | None:
    raw = " ".join(str(value or "") for value in values)
    match = re.search(r"(?:\+|mais)\s*(\d{1,2}(?:[.,]\d+)?)\s*%", raw, re.I)
    if not match:
        return None
    return float(match.group(1).replace(",", ".")) / 100


def _rating_quality(value) -> float | None:
    raw = re.sub(r"[^A-Z]", "", str(value or "").upper())
    if not raw:
        return None
    if raw.startswith("AAA"):
        return 1.0
    if raw.startswith("AA"):
        return .85
    if raw.startswith("A"):
        return .70
    if raw.startswith("BBB"):
        return .50
    if raw.startswith("BB"):
        return .30
    if raw.startswith("B"):
        return .15
    if raw.startswith(("CCC", "CC", "C")):
        return .05
    if raw.startswith("D"):
        return 0.0
    return None


def _metric_row(context: dict, metric: str, value, *, raw_payload_id: int | None,
                release_id: int | None, metadata: dict | None = None) -> dict | None:
    if value is None:
        return None
    numeric = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    textual = value if isinstance(value, str) else None
    structured = value if isinstance(value, (dict, list)) else None
    semantic = json.dumps({"security": context["security_key"], "metric": metric,
                           "value": value, "reference": context["reference"].isoformat(),
                           "vintage": context["vintage"]}, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"), default=str)
    return {
        "security_key": context["security_key"], "issuer_cnpj": context["issuer_cnpj"],
        "certificate_code": context.get("certificate"), "issue_number": context.get("issue"),
        "series_number": context.get("series"), "metric_name": metric,
        "value_numeric": numeric, "value_text": textual,
        "value_json": json.dumps(structured, ensure_ascii=False) if structured is not None else None,
        "reference_date": context["reference"], "available_at": context["available"],
        "source_published_at": context.get("published"), "knowledge_at": context["available"],
        "availability_quality": "verified_publication" if context.get("published") else "first_observed_proxy",
        "vintage": context["vintage"], "source": SOURCE, "source_url": context["source_url"],
        "raw_payload_id": raw_payload_id, "source_release_id": release_id,
        "content_hash": hashlib.sha256(semantic.encode("utf-8")).hexdigest(),
        "quality_status": "observed",
        "metadata_json": json.dumps({**(metadata or {}), "parser_version": PARSER_VERSION},
                                    ensure_ascii=False),
    }


def parse_cri_archive(archive: CvmArchive, raw_payload_id: int | None = None,
                      release_id: int | None = None) -> dict:
    tables = _tables(archive)
    general = tables.get("inf_mensal_cri_geral", pd.DataFrame())
    contexts: dict[tuple[str, str, date], dict] = {}
    for row in general.to_dict("records"):
        issuer, certificate = _digits(row.get("CNPJ_Emissora")), _security_part(row.get("Codigo_Identificacao_Certificado"))
        reference = _date(row.get("Data_Referencia"))
        if not issuer or not certificate or reference is None:
            continue
        key = (issuer, certificate, reference)
        version = int(_num(row.get("Versao")) or 1)
        if key in contexts and contexts[key]["version"] > version:
            continue
        published = _published(row.get("Data_Entrega"))
        contexts[key] = {"issuer_cnpj": issuer, "certificate": certificate,
                         "reference": reference, "version": version, "general": row,
                         "published": published, "available": published or archive.collected_at}

    def grouped(table_name: str) -> dict[tuple[str, str, date], list[dict]]:
        output: dict[tuple[str, str, date], list[dict]] = defaultdict(list)
        frame = tables.get(table_name, pd.DataFrame())
        for row in frame.to_dict("records"):
            key = (_digits(row.get("CNPJ_Emissora")),
                   _security_part(row.get("Codigo_Identificacao_Certificado")),
                   _date(row.get("Data_Referencia")))
            context = contexts.get(key)
            if context and int(_num(row.get("Versao")) or 1) == context["version"]:
                output[key].append(row)
        return output

    classes = grouped("inf_mensal_cri_classe")
    credits = grouped("inf_mensal_cri_creditos")
    portfolios = grouped("inf_mensal_cri_carteira")
    debtors = grouped("inf_mensal_cri_cedente_devedor")
    observations: list[dict] = []
    security_keys: set[str] = set()
    for key, base in contexts.items():
        general_row = base["general"]
        credit = (credits.get(key) or [{}])[0]
        portfolio = (portfolios.get(key) or [{}])[0]
        class_rows = classes.get(key) or [{}]
        debtor_values: dict[str, float] = defaultdict(float)
        for row in debtors.get(key) or []:
            if "DEVEDOR" not in str(row.get("Tipo") or "").upper():
                continue
            debtor, share = _digits(row.get("CNPJ")), _ratio(row.get("Percentual"))
            if debtor and share is not None and share > 0:
                debtor_values[debtor] += share
        debtor_total = sum(debtor_values.values())
        debtor_profile = ({name: value / debtor_total for name, value in debtor_values.items()}
                           if debtor_total > 0 else None)
        linked = _num(portfolio.get("Creditos_Vinculados"))
        delinquent = _num(portfolio.get("Creditos_Vinculados_Inadimplentes"))
        if linked and linked > 0 and delinquent is not None:
            delinquency = max(0.0, min(delinquent / linked, 1.0))
        else:
            due, unpaid = _num(credit.get("A_Vencer")), _num(credit.get("Nao_Pagos"))
            delinquency = (max(0.0, min(unpaid / (due + unpaid), 1.0))
                           if due is not None and unpaid is not None and due + unpaid > 0 else None)
        total_class_value = sum(max(_num(row.get("Valor_Certificados")) or 0, 0) for row in class_rows)
        subordinated_value = sum(max(_num(row.get("Valor_Certificados")) or 0, 0)
                                 for row in class_rows
                                 if any(token in str(row.get("Classe") or "").upper()
                                        for token in ("SUBORD", "MEZAN", "JUNIOR")))
        for class_row in class_rows:
            issue = _security_part(general_row.get("Numero_Emissao"))
            series = _security_part(class_row.get("Numero_Serie"))
            sec_key = security_key(base["issuer_cnpj"], issue, series, base["certificate"])
            if not sec_key:
                continue
            security_keys.add(sec_key)
            context = {**base, "security_key": sec_key, "issue": issue or None,
                       "series": series or None, "source_url": archive.url,
                       "vintage": f"{SOURCE}:{base['reference']}:v{base['version']}"}
            maturity = _date(class_row.get("Data_Vencimento"))
            class_name = str(class_row.get("Classe") or "").upper()
            minimum_subordination = _ratio(class_row.get("Indice_Subordinacao_Minimo"))
            subordination = (0.0 if any(token in class_name for token in ("SUBORD", "MEZAN", "JUNIOR"))
                             else minimum_subordination if minimum_subordination is not None
                             else subordinated_value / total_class_value if total_class_value > 0 else None)
            indexers = _indexer_profile(general_row.get("Taxas_Medias_Indexadores_Creditos_Vinculados"),
                                        class_row.get("Taxas_Indexadores"), class_row.get("Taxa_Juros"))
            metrics = {
                "duration_anos": _duration_years(general_row, credit, base["reference"], maturity),
                "ltv": _ratio(general_row.get("Indice_LTV")),
                "rating_quality": _rating_quality(class_row.get("Classificacao_Risco_Atual")),
                "subordination_protection": subordination,
                "delinquency": delinquency,
                "credit_spread": _credit_spread(class_row.get("Taxas_Indexadores"), class_row.get("Taxa_Juros")),
                "indexer_profile": indexers,
                "debtor_profile": debtor_profile,
            }
            for metric, value in metrics.items():
                row = _metric_row(context, metric, value, raw_payload_id=raw_payload_id,
                                  release_id=release_id,
                                  metadata={"archive_sha256": archive.sha256,
                                            "class": class_row.get("Classe"),
                                            "rating_raw": class_row.get("Classificacao_Risco_Atual")})
                if row:
                    observations.append(row)
    return {"observations": observations, "securities": len(security_keys),
            "contexts": len(contexts)}


def _register_release(conn, archive: CvmArchive, raw_payload_id: int) -> int:
    natural = f"inf_mensal_cri_{archive.year}"
    existing = conn.execute(text("""
        SELECT id FROM market.fii_source_releases
        WHERE provider='cvm' AND endpoint=:endpoint AND natural_key=:natural
          AND content_sha256=:sha
    """), {"endpoint": ENDPOINT, "natural": natural, "sha": archive.sha256}).scalar()
    if existing:
        return int(existing)
    latest = conn.execute(text("""
        SELECT id,revision_no FROM market.fii_source_releases
        WHERE provider='cvm' AND endpoint=:endpoint AND natural_key=:natural
        ORDER BY revision_no DESC LIMIT 1
    """), {"endpoint": ENDPOINT, "natural": natural}).mappings().first()
    value = conn.execute(text("""
        INSERT INTO market.fii_source_releases (
            provider,endpoint,natural_key,first_observed_at,knowledge_at,
            availability_quality,revision_no,supersedes_id,raw_payload_id,
            content_sha256,metadata_json
        ) VALUES ('cvm',:endpoint,:natural,:observed,:observed,'first_observed_proxy',
                  :revision,:previous,:raw,:sha,CAST(:metadata AS jsonb))
        RETURNING id
    """), {"endpoint": ENDPOINT, "natural": natural, "observed": archive.collected_at,
             "revision": int(latest["revision_no"]) + 1 if latest else 1,
             "previous": int(latest["id"]) if latest else None, "raw": raw_payload_id,
             "sha": archive.sha256,
             "metadata": json.dumps({"source_url": archive.url, "headers": archive.headers})}).scalar()
    return int(value)


def _lookthrough(conn) -> dict:
    exposure_rows = conn.execute(text("""
        WITH latest_ref AS (
            SELECT ticker,max(reference_date) reference_date
            FROM market.fii_exposures WHERE exposure_type='security' GROUP BY ticker
        ), latest_at AS (
            SELECT e.ticker,e.reference_date,max(e.available_at) available_at
            FROM market.fii_exposures e JOIN latest_ref r USING(ticker,reference_date)
            WHERE e.exposure_type='security' GROUP BY 1,2
        )
        SELECT DISTINCT ON (e.ticker,e.exposure_name) e.*
        FROM market.fii_exposures e JOIN latest_at l
          USING(ticker,reference_date,available_at)
        WHERE e.exposure_type='security'
        ORDER BY e.ticker,e.exposure_name,e.knowledge_at DESC,e.id DESC
    """)).mappings().all()
    observation_rows = conn.execute(text("""
        SELECT DISTINCT ON (security_key,metric_name) *
        FROM market.cri_security_observations
        WHERE knowledge_at<=now() AND quality_status<>'rejected'
        ORDER BY security_key,metric_name,knowledge_at DESC,reference_date DESC,id DESC
    """)).mappings().all()
    by_security = {(str(row["security_key"]), str(row["metric_name"])): dict(row)
                   for row in observation_rows}
    by_fund: dict[str, list[dict]] = defaultdict(list)
    for row in exposure_rows:
        by_fund[str(row["ticker"])].append(dict(row))
    fii_observations: list[dict] = []
    fii_exposures: list[dict] = []
    numeric_metrics = ("duration_anos", "ltv", "rating_quality",
                       "subordination_protection", "delinquency")
    for ticker, holdings in by_fund.items():
        metadata = holdings[0].get("metadata_json") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if not isinstance(metadata, dict):
            metadata = {}
        base_coverage = float(metadata.get("linkable_portfolio_coverage") or 0)
        reference = max(row["reference_date"] for row in holdings)
        holding_knowledge = max(row["knowledge_at"] for row in holdings)
        quality = ("retrospective_backfill"
                   if any(row["availability_quality"] == "retrospective_backfill" for row in holdings)
                   else "first_observed_proxy")
        for metric in numeric_metrics:
            matched = [(row, by_security.get((str(row["exposure_name"]), metric)))
                       for row in holdings]
            matched = [(holding, obs) for holding, obs in matched
                       if obs and obs.get("value_numeric") is not None]
            matched_weight = sum(float(holding["exposure_weight"]) for holding, _ in matched)
            coverage = base_coverage * matched_weight
            if coverage < .60 or matched_weight <= 0:
                continue
            value = sum(float(holding["exposure_weight"]) * float(obs["value_numeric"])
                        for holding, obs in matched) / matched_weight
            knowledge = max([holding_knowledge] + [obs["knowledge_at"] for _, obs in matched])
            source_published = max((obs.get("source_published_at") for _, obs in matched
                                    if obs.get("source_published_at")), default=None)
            cri_reference = max(obs["reference_date"] for _, obs in matched)
            row = metric_observation(
                ticker=ticker, metric_name=metric, value=value,
                reference_date=max(reference, cri_reference), available_at=knowledge,
                source=LOOKTHROUGH_SOURCE,
                vintage=f"{LOOKTHROUGH_SOURCE}:{reference}:{cri_reference}",
                metadata={"security_coverage": coverage, "holdings_reference": str(reference),
                          "cri_reference": str(cri_reference),
                          "input_security_keys": [str(h["exposure_name"]) for h, _ in matched]},
                source_published_at=source_published, availability_quality=quality)
            fii_observations.append(row)

        for profile_metric, result_metric, exposure_type in (
            ("indexer_profile", "indexer_diversification", "indexer"),
            ("debtor_profile", "debtor_diversification", "debtor"),
        ):
            aggregate: dict[str, float] = defaultdict(float)
            matched_weight = 0.0
            matched_rows = []
            for holding in holdings:
                obs = by_security.get((str(holding["exposure_name"]), profile_metric))
                if not obs or not obs.get("value_json"):
                    continue
                profile = obs["value_json"]
                if isinstance(profile, str):
                    profile = json.loads(profile)
                if not isinstance(profile, dict) or not profile:
                    continue
                weight = float(holding["exposure_weight"])
                matched_weight += weight
                matched_rows.append(obs)
                for name, share in profile.items():
                    aggregate[str(name)] += weight * float(share)
            coverage = base_coverage * matched_weight
            total = sum(aggregate.values())
            if coverage < .60 or total <= 0:
                continue
            normalized = {name: value / total for name, value in aggregate.items()}
            diversification = 1 - sum(value * value for value in normalized.values())
            knowledge = max([holding_knowledge] + [obs["knowledge_at"] for obs in matched_rows])
            cri_reference = max(obs["reference_date"] for obs in matched_rows)
            vintage = f"{LOOKTHROUGH_SOURCE}:{reference}:{cri_reference}"
            fii_observations.append(metric_observation(
                ticker=ticker, metric_name=result_metric, value=diversification,
                reference_date=max(reference, cri_reference), available_at=knowledge,
                source=LOOKTHROUGH_SOURCE, vintage=vintage,
                metadata={"security_coverage": coverage, "formula": "1-HHI",
                          "lookthrough_profile": normalized},
                availability_quality=quality))
            for name, weight in normalized.items():
                semantic = json.dumps({"ticker": ticker, "type": exposure_type,
                                       "name": name, "weight": weight,
                                       "reference": str(max(reference, cri_reference)),
                                       "vintage": vintage}, sort_keys=True, separators=(",", ":"))
                fii_exposures.append({
                    "ticker": ticker, "exposure_type": exposure_type,
                    "exposure_name": name[:300], "exposure_weight": weight,
                    "reference_date": max(reference, cri_reference), "available_at": knowledge,
                    "knowledge_at": knowledge, "availability_quality": quality,
                    "content_hash": hashlib.sha256(semantic.encode()).hexdigest(),
                    "vintage": vintage, "source": LOOKTHROUGH_SOURCE,
                    "raw_payload_id": None,
                    "metadata_json": json.dumps({"security_coverage": coverage}, ensure_ascii=False),
                })
    from data_pipeline.market import repository as repo
    return {"fii_observations": repo.upsert(conn, "fii_metric_observations", fii_observations),
            "fii_exposures": repo.upsert(conn, "fii_exposures", fii_exposures)}


def ingest_cvm_cri(*, years: int = 5) -> dict:
    from email.utils import parsedate_to_datetime
    from data_pipeline.market import repository as repo
    from data_pipeline.market.fii_ingest import (
        _engine, audit_methodology_v4_data, record_validation_readiness,
        snapshot_methodology_v4,
    )

    progress = {"archives": 0, "missing_archives": 0, "securities": 0,
                "security_observations": 0, "fii_observations": 0,
                "fii_exposures": 0, "quarantined": 0, "lineage": 0, "errors": []}
    engine = _engine()
    if engine is None:
        return {**progress, "status": "failed", "errors": ["banco indisponivel"]}
    with engine.connect() as conn:
        if not conn.execute(text(
                "SELECT to_regclass('market.cri_security_observations') IS NOT NULL")).scalar():
            return {**progress, "status": "failed", "errors": ["migration 029 pendente"]}
        if not conn.execute(text(
                "SELECT to_regclass('market.fii_cri_archive_loads') IS NOT NULL")).scalar():
            return {**progress, "status": "failed", "errors": ["migration 038 pendente"]}
    current = datetime.now(timezone.utc).year
    first = max(2021, current - max(int(years), 1) + 1)
    for year in range(first, current + 1):
        archive = None
        try:
            archive = fetch_cri_archive(year)
            if archive is None:
                progress["missing_archives"] += 1
                continue
            published = None
            if archive.headers.get("last-modified"):
                try:
                    published = parsedate_to_datetime(archive.headers["last-modified"])
                except (TypeError, ValueError):
                    published = None
            with engine.begin() as conn:
                raw_id = repo.save_raw_payload(
                    conn, None, f"cvm_cri_{year}", archive_manifest(archive),
                    request_params={"year": year, "url": archive.url},
                    response_headers=archive.headers, http_status=200,
                    collected_at=archive.collected_at, source_published_at=published,
                    request_fingerprint=hashlib.sha256(f"cvm_cri|{year}".encode()).hexdigest(),
                    source="cvm")
                stored_at = conn.execute(text("""
                    SELECT COALESCE(collected_at,fetched_at)
                    FROM market.brapi_raw_payloads WHERE id=:id
                """), {"id": raw_id}).scalar()
                canonical = replace(archive, collected_at=stored_at or archive.collected_at)
                release_id = _register_release(conn, canonical, int(raw_id))
                completed = conn.execute(text("""
                    SELECT status='completed'
                    FROM market.fii_cri_archive_loads
                    WHERE archive_year=:year AND archive_sha256=:sha
                      AND parser_name=:parser AND parser_version=:version
                """), {"year": year, "sha": canonical.sha256,
                       "parser": PARSER_NAME, "version": PARSER_VERSION}).scalar()
                if completed:
                    progress["archives"] += 1
                    continue
                conn.execute(text("""
                    INSERT INTO market.fii_cri_archive_loads (
                        archive_year,archive_sha256,parser_name,parser_version,
                        source_url,status,raw_payload_id,source_release_id,
                        started_at,updated_at,completed_at,error_message
                    ) VALUES (:year,:sha,:parser,:version,:url,'running',:raw,:release,
                              now(),now(),NULL,NULL)
                    ON CONFLICT (archive_year,archive_sha256,parser_name,parser_version)
                    DO UPDATE SET status='running',raw_payload_id=EXCLUDED.raw_payload_id,
                        source_release_id=EXCLUDED.source_release_id,source_url=EXCLUDED.source_url,
                        started_at=now(),updated_at=now(),completed_at=NULL,error_message=NULL
                """), {"year": year, "sha": canonical.sha256, "parser": PARSER_NAME,
                       "version": PARSER_VERSION, "url": canonical.url,
                       "raw": raw_id, "release": release_id})
                already_parsed = conn.execute(text("""
                    SELECT count(*),count(DISTINCT security_key)
                    FROM market.cri_security_observations
                    WHERE raw_payload_id=:raw
                      AND metadata_json->>'parser_version'=:parser
                """), {"raw": raw_id, "parser": PARSER_VERSION}).fetchone()
                if int(already_parsed[0] or 0) > 0:
                    parsed = {"observations": [], "securities": int(already_parsed[1] or 0)}
                else:
                    parsed = parse_cri_archive(canonical, int(raw_id), release_id)
                    progress["security_observations"] += repo.upsert(
                        conn, "cri_security_observations", parsed["observations"])
                progress["lineage"] += repo.record_lineage_for_raw_payload(conn, int(raw_id))
                conn.execute(text("""
                    UPDATE market.fii_cri_archive_loads
                    SET status='completed',security_observation_count=:observations,
                        updated_at=now(),completed_at=now(),error_message=NULL
                    WHERE archive_year=:year AND archive_sha256=:sha
                      AND parser_name=:parser AND parser_version=:version
                """), {"observations": len(parsed.get("observations") or []),
                       "year": year, "sha": canonical.sha256,
                       "parser": PARSER_NAME, "version": PARSER_VERSION})
            progress["archives"] += 1
            progress["securities"] += int(parsed["securities"])
        except Exception as exc:
            try:
                with engine.begin() as conn:
                    if 'archive' in locals() and archive is not None:
                        conn.execute(text("""
                            UPDATE market.fii_cri_archive_loads SET status='failed',
                                updated_at=now(),error_message=:error
                            WHERE archive_year=:year AND archive_sha256=:sha
                              AND parser_name=:parser AND parser_version=:version
                        """), {"year": year, "sha": archive.sha256,
                               "parser": PARSER_NAME, "version": PARSER_VERSION,
                               "error": str(exc)[:1000]})
            except Exception:
                pass
            progress["errors"].append({"year": year, "error": str(exc)[:500]})
    with engine.begin() as conn:
        quarantined = conn.execute(text("""
            UPDATE market.cri_security_observations
            SET quality_status='rejected',
                metadata_json=metadata_json ||
                    '{"automatic_rejection":"impossible_numeric_range_v1"}'::jsonb
            WHERE quality_status<>'rejected' AND (
                (metric_name IN ('ltv','rating_quality','subordination_protection','delinquency')
                 AND (value_numeric<0 OR value_numeric>1))
                OR (metric_name='duration_anos' AND (value_numeric<0 OR value_numeric>100))
            )
        """))
        progress["quarantined"] += max(int(quarantined.rowcount or 0), 0)
        rejected_fii = conn.execute(text("""
            UPDATE market.fii_metric_observations
            SET quality_status='rejected',
                metadata_json=metadata_json ||
                    '{"automatic_rejection":"impossible_numeric_range_v1"}'::jsonb
            WHERE source=:source AND quality_status<>'rejected'
              AND metric_name='duration_anos'
              AND (value_numeric<0 OR value_numeric>100)
        """), {"source": LOOKTHROUGH_SOURCE})
        progress["quarantined"] += max(int(rejected_fii.rowcount or 0), 0)
        lookthrough = _lookthrough(conn)
    progress.update(lookthrough)
    progress["audit"] = audit_methodology_v4_data()
    progress["validation"] = record_validation_readiness(progress["audit"])
    progress["snapshot"] = snapshot_methodology_v4()
    progress["status"] = "completed" if not progress["errors"] else "partial"
    return progress

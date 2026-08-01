"""Document lake reproduzível para DFIN/relatórios públicos de FIIs.

Os binários são endereçados por SHA-256. A extração gera somente evidências
pendentes de revisão; nenhuma métrica documental entra automaticamente no score.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import random
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
import unicodedata
from urllib.parse import urlsplit
import uuid

import requests
from sqlalchemy import text

from data_pipeline.market import repository as repo
from data_pipeline.market.fii_sources import metric_observation

logger = logging.getLogger(__name__)

PARSER_NAME = "fii_public_report"
PARSER_VERSION = "1.6.4"
SCHEMA_VERSION = "fii-evidence-v6"


class DocumentTooLargeError(ValueError):
    """Documento excede o limite seguro configurado para uma única coleta."""


class _HostCircuitBreaker:
    """Circuit breaker em memória, restrito a uma execução do lote."""

    def __init__(self, threshold: int = 3):
        self.threshold = max(int(threshold), 1)
        self._failures: dict[str, int] = {}
        self._opened: set[str] = set()

    def is_open(self, host: str) -> bool:
        return bool(host) and host in self._opened

    def success(self, host: str) -> None:
        if host:
            self._failures.pop(host, None)

    def failure(self, host: str, *, transient: bool) -> bool:
        if not host or not transient:
            return False
        failures = self._failures.get(host, 0) + 1
        self._failures[host] = failures
        if failures >= self.threshold and host not in self._opened:
            self._opened.add(host)
            return True
        return False

_METRIC_PATTERNS = {
    "wault_anos": re.compile(r"\bWAULT\b[^\d]{0,40}(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:anos?|years?)", re.I),
    "vacancia_fisica": re.compile(r"vac[aâ]ncia\s+f[ií]sica[^\d]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "vacancia_financeira": re.compile(r"vac[aâ]ncia\s+financeira[^\d]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "ltv": re.compile(r"\bLTV\b[^\d]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "duration_anos": re.compile(r"\bduration\b[^\d]{0,35}(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:anos?|years?)", re.I),
    "implied_cap_rate": re.compile(r"\bcap\s*rate\b[^\d]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "tenant_concentration": re.compile(r"(?:maior\s+)?(?:locat[aá]rio|inquilino)[^\d%]{0,55}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "debtor_concentration": re.compile(r"(?:maior\s+)?devedor[^\d%]{0,55}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "issuance_concentration": re.compile(r"(?:maior\s+)?(?:cri|emiss[aã]o)[^\d%]{0,55}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "delinquency": re.compile(r"inadimpl[eê]ncia[^\d]{0,45}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "subordination_protection": re.compile(r"subordina[cç][aã]o[^\d]{0,45}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "lease_expiry_concentration_24m": re.compile(r"(?:vencimentos?|revisional)[^\d%]{0,70}(?:24\s*meses|2\s*anos)[^\d%]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "management_fee": re.compile(r"taxa\s+de\s+administra[cç][aã]o[^\d]{0,60}(\d{1,3}(?:[.,]\d{1,3})?)\s*%", re.I),
    "credit_spread": re.compile(r"(?:IPCA|CDI|IGP-?M)[^+\d]{0,15}\+?\s*(\d{1,2}(?:[.,]\d{1,2})?)\s*%", re.I),
    "property_count": re.compile(r"(?:portf[oó]lio|carteira)[^\d]{0,50}(\d{1,4})\s+(?:im[oó]veis|ativos\s+imobili[aá]rios)", re.I),
}

_PERCENT_METRICS = {"vacancia_fisica", "vacancia_financeira", "ltv",
                    "implied_cap_rate", "tenant_concentration",
                    "debtor_concentration", "issuance_concentration", "delinquency",
                    "subordination_protection", "lease_expiry_concentration_24m",
                    "management_fee", "credit_spread"}

# Métricas de cabeçalho/portfólio que podem alimentar uma observação
# provisória quando o relatório contém um único valor explícito. Métricas por
# ativo (spread, emissão, devedor) permanecem somente na fila humana.
_PROVISIONAL_METRICS = {
    "vacancia_fisica", "vacancia_financeira", "wault_anos", "duration_anos",
    "ltv", "implied_cap_rate", "tenant_concentration", "delinquency",
    "lease_expiry_concentration_24m", "property_count",
}

_TYPE_METRICS = {
    "tijolo": {
        "wault_anos", "vacancia_fisica", "vacancia_financeira",
        "implied_cap_rate", "tenant_concentration",
        "lease_expiry_concentration_24m", "management_fee", "property_count",
    },
    "papel": {
        "ltv", "duration_anos", "debtor_concentration",
        "issuance_concentration", "delinquency", "subordination_protection",
        "management_fee", "credit_spread",
    },
    "fof": {"management_fee"},
}
_TYPE_METRICS["hibrido"] = set(_METRIC_PATTERNS)

_DEVELOPMENT_METRICS = (
    ("development_active_project_count",
     re.compile(r"(\d{1,3})\s+ativos\s+formam\s+a\s+carteira", re.I),
     1.0, "quantidade", "manager_reported", 85),
    ("development_completed_project_count",
     re.compile(r"(\d{1,3})\s+conclu[ií]dos", re.I),
     1.0, "quantidade", "manager_reported", 75),
    ("development_construction_project_count",
     re.compile(r"(\d{1,3})\s+em\s+obras", re.I),
     1.0, "quantidade", "manager_reported", 75),
    ("development_prelaunch_project_count",
     re.compile(r"(\d{1,3})\s+em\s+pr[eé]\s*-?\s*lan[cç]amento", re.I),
     1.0, "quantidade", "manager_reported", 75),
    ("development_planned_units",
     re.compile(r"(\d[\d.\s]{1,11})\s+unidades\s+previstas", re.I),
     1.0, "unidades", "manager_estimate", 80),
    ("development_sellable_area_sqm",
     re.compile(r"(\d[\d.\s]{2,14})\s*m[²2]\s+de\s+[aá]rea\s+vend[aá]vel", re.I),
     1.0, "m²", "manager_estimate", 80),
    ("development_receivables_brl",
     re.compile(r"R\$\s*([\d.,]+)\s*milh[oõ]es\s+.{0,20}receber", re.I),
     1_000_000.0, "BRL", "manager_reported", 90),
    ("development_inventory_brl",
     re.compile(r"R\$\s*([\d.,]+)\s*milh[oõ]es\s+.{0,30}estoque", re.I),
     1_000_000.0, "BRL", "manager_estimate", 90),
    ("development_construction_cost_brl",
     re.compile(r"R\$\s*([\d.,]+)\s*milh[oõ]es\s+.{0,35}custo\s+de\s+obras", re.I),
     1_000_000.0, "BRL", "manager_estimate", 90),
    ("development_potential_vgv_brl",
     re.compile(r"R\$\s*([\d.,]+)\s*bilh[aã]o\s+.{0,25}VGV", re.I),
     1_000_000_000.0, "BRL", "manager_estimate", 95),
)

_PROJECT_TABLE_PATTERN = re.compile(
    r"(?P<project_type>Urbaniza[cç][aã]o|Incorpora[cç][aã]o\s+Residencial|"
    r"Cons[oó]rcio\s+Cortel)\s+"
    r"(?P<project_name>.+?)\s+"
    r"(?P<portfolio_weight>\d{1,3},\d+)%\s+"
    r"(?P<city>.+?)\s+[–-]\s*(?P<state>[A-Z]{2})\s+"
    r"(?P<stage>Conclu[ií]do|Obras|Landbank|-)\s+"
    r"(?P<construction_progress>\d{1,3}%|-)\s+"
    r"(?P<sales_progress>\d{1,3}%|-)\s+"
    r"(?P<expected_irr>\d{1,3},\d+)%\s+"
    r"(?P<expected_result>\d{1,4},\d{3})",
    re.I,
)

_RISK_TERMS = (
    "incerteza", "risco", "adiamento", "atraso", "custos dos insumos",
    "fluxo de caixa previsto", "demora", "endividamento das famílias",
)
_GENERIC_RISK_SENTENCE_PATTERNS = (
    re.compile(r"^(?:high grade|high yield)\s*:", re.I),
    re.compile(r"^risco (?:de crédito|de mercado)\s*:", re.I),
    re.compile(r"^cotas? (?:sênior|mezanino|subordinad[ao]s?)\s*:", re.I),
    re.compile(r"^os investidores devem estar preparados", re.I),
    re.compile(r"^investidores com cotas subordinadas recebem", re.I),
    re.compile(r"^compensam o risco com expectativa", re.I),
    re.compile(r"^são as primeiras a receber amortizações", re.I),
    re.compile(r"^por a?\s*ssumirem maior risco", re.I),
    re.compile(r"^essa pulverização contribui", re.I),
    re.compile(r"^embora possa oferecer maior", re.I),
    re.compile(r"^os recursos são aplicados conforme definido", re.I),
)
_MATERIAL_RISK_CONTEXT = (
    "atraso", "aumento", "pressão", "inadimpl", "deterior", "perda",
    "descumpr", "concentra", "exposição", "incerteza", "adiamento",
    "custos", "demora", "endividamento", "vacância", "judicial", "default",
)


def _engine():
    from data_pipeline.utils.db_utils import get_pipeline_engine
    return get_pipeline_engine()


def _cache_root() -> Path:
    configured = os.getenv("FII_DOCUMENT_CACHE", "").strip()
    root = Path(configured) if configured else Path("local_staging") / "fii_documents"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _download_host(url: str) -> str:
    parsed = urlsplit(str(url or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("documento exige URL HTTPS com host válido")
    return parsed.hostname.lower().rstrip(".")


def _is_transient_download_error(exc: Exception) -> bool:
    if isinstance(exc, requests.exceptions.HTTPError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return status is None or status == 429 or status >= 500
    if isinstance(exc, requests.RequestException):
        return True
    return False


def _retry_delay_seconds(attempt: int, response=None) -> float:
    """Backoff curto com jitter e respeito a Retry-After numérico."""
    base = min(2 ** max(int(attempt), 0), 30)
    retry_after = 0.0
    headers = getattr(response, "headers", {}) or {}
    try:
        retry_after = max(float(headers.get("Retry-After") or 0), 0.0)
    except (TypeError, ValueError):
        retry_after = 0.0
    jitter = random.uniform(0.0, min(base * .25, 2.0))
    return min(max(float(base), retry_after) + jitter, 60.0)


def _download(url: str, timeout: int = 60, *,
              max_bytes: int = 30 * 1024 * 1024,
              attempts: int = 3,
              allowed_host: str | None = None,
              max_elapsed_seconds: float | None = None) -> tuple[bytes, str]:
    _download_host(url)
    last_error: Exception | None = None
    total_attempts = max(int(attempts), 1)
    per_attempt_timeout = max(float(timeout), 1.0)
    total_budget = (
        max(float(max_elapsed_seconds), per_attempt_timeout)
        if max_elapsed_seconds is not None
        else per_attempt_timeout * total_attempts + min(2 ** total_attempts, 30)
    )
    deadline = time.monotonic() + total_budget
    for attempt in range(total_attempts):
        response = None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout("prazo total de download excedido")
            current_timeout = max(min(per_attempt_timeout, remaining), 1.0)
            if allowed_host:
                from data_pipeline.market.fii_ri_documents import (
                    download_official_document,
                )
                return download_official_document(
                    url,
                    allowed_host=allowed_host,
                    timeout=current_timeout,
                    max_bytes=max_bytes,
                )
            response = requests.get(
                url, timeout=current_timeout, allow_redirects=True, stream=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (compatible; DashboardFinanceiro/1.0; "
                        "+fii-document-audit)"
                    ),
                    "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
                    # Servidores legados da CVM/Fundos.NET interrompem com mais
                    # frequência respostas comprimidas e conexões reutilizadas.
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                })
            try:
                response.raise_for_status()
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > max_bytes:
                    raise DocumentTooLargeError(
                        f"documento declara {int(declared)} bytes; limite {max_bytes}")
                chunks: list[bytes] = []
                downloaded = 0
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if time.monotonic() >= deadline:
                        raise requests.Timeout("prazo total de download excedido")
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise DocumentTooLargeError(
                            f"documento excedeu {max_bytes} bytes durante download")
                    chunks.append(chunk)
                content = b"".join(chunks)
                mime = str(response.headers.get("Content-Type") or
                           "application/octet-stream").split(";")[0]
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
            if not content:
                raise ValueError("documento vazio")
            return content, mime
        except DocumentTooLargeError:
            raise
        except requests.RequestException as exc:
            last_error = exc
            if not _is_transient_download_error(exc):
                break
            if attempt < total_attempts - 1:
                delay = _retry_delay_seconds(attempt, getattr(exc, "response", response))
                if time.monotonic() + delay >= deadline:
                    break
                time.sleep(delay)
    assert last_error is not None
    raise last_error


def _extract_pdf_text(content: bytes, page_limit: int = 120) -> tuple[str, int, str, list[str]]:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(content))
    pages = min(len(reader.pages), page_limit)
    values = []
    for page in reader.pages[:pages]:
        try:
            values.append(page.extract_text() or "")
        except Exception:
            values.append("")
    extracted = "\n".join(values).strip()
    method = "pypdf"
    density = len(extracted) / max(pages, 1)
    if density < 80:
        try:
            from core.c6_ocr import ocr_extract_text
            ocr_text, ocr_pages = ocr_extract_text(content)
            if len(ocr_text) > len(extracted):
                extracted, pages, method = ocr_text, ocr_pages, "tesseract_ocr"
        except Exception:
            logger.debug("OCR opcional indisponível", exc_info=True)
    return extracted, pages, method, values if method == "pypdf" else []


def _parse_number(raw: str, *, percent: bool = False) -> float:
    value = float(raw.replace(".", "").replace(",", ".") if "," in raw else raw)
    return value / 100.0 if percent else value


def _parse_brazilian_number(raw: str) -> float:
    compact = re.sub(r"\s+", "", str(raw))
    if "," in compact:
        compact = compact.replace(".", "").replace(",", ".")
    elif "." in compact:
        groups = compact.split(".")
        if len(groups) > 1 and all(len(group) == 3 for group in groups[1:]):
            compact = "".join(groups)
    return float(compact)


def _compact_report_text(value: str) -> str:
    compact = re.sub(r"\s+", " ", str(value).replace("\x00", " ")).strip()
    compact = re.sub(r"R\s*\$\s*", "R$ ", compact, flags=re.I)
    compact = re.sub(r"(?<=\d)\s*([.,])\s*(?=\d)", r"\1", compact)
    compact = re.sub(r"\s*%", "%", compact)
    compact = re.sub(r"\bm\s*[²2]\b", "m²", compact, flags=re.I)
    return compact


def _normalized_project_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return normalized[:160]


def _extract_development_evidence(page_texts: list[str] | None) -> list[dict]:
    evidence: list[dict] = []
    for page_number, page_text in enumerate(page_texts or [], start=1):
        compact = _compact_report_text(page_text)
        for metric, pattern, scale, unit, nature, priority in _DEVELOPMENT_METRICS:
            for match in list(pattern.finditer(compact))[:5]:
                try:
                    normalized = _parse_brazilian_number(match.group(1)) * scale
                except ValueError:
                    continue
                if normalized < 0:
                    continue
                evidence.append({
                    "metric_name": metric,
                    "raw_value": match.group(1),
                    "normalized_value": normalized,
                    "unit": unit,
                    "page_number": page_number,
                    "bbox_json": None,
                    "evidence_text": compact[
                        max(match.start() - 100, 0):min(match.end() + 140, len(compact))
                    ],
                    "confidence": .96,
                    "validation_status": "pending",
                    "value_nature": nature,
                    "review_priority": priority,
                })
    return evidence


def _extract_development_projects(page_texts: list[str] | None) -> list[dict]:
    """Extrai a tabela consolidada de projetos sem inferir campos ausentes."""
    rows: list[dict] = []
    for page_number, page_text in enumerate(page_texts or [], start=1):
        compact = _compact_report_text(page_text)
        if "% Obras" not in compact or "TIR" not in compact:
            continue
        for match in _PROJECT_TABLE_PATTERN.finditer(compact):
            values = match.groupdict()

            def pct(name: str) -> float | None:
                raw = values[name]
                return None if raw == "-" else float(raw.rstrip("%").replace(",", ".")) / 100

            project_name = re.sub(r"\s+", " ", values["project_name"]).strip()
            rows.append({
                "project_key": _normalized_project_key(project_name),
                "project_name": project_name,
                "project_type": re.sub(r"\s+", " ", values["project_type"]).strip(),
                "city": re.sub(r"\s+", " ", values["city"]).strip(),
                "state": values["state"].upper(),
                "stage": None if values["stage"] == "-" else values["stage"],
                "portfolio_weight": pct("portfolio_weight"),
                "construction_progress": pct("construction_progress"),
                "sales_progress": pct("sales_progress"),
                "expected_irr": pct("expected_irr"),
                "expected_result_brl": (
                    _parse_brazilian_number(values["expected_result"]) * 1_000_000
                ),
                "value_nature": "manager_estimate",
                "raw_json": values,
                "page_number": page_number,
                "evidence_text": match.group(0),
                "confidence": .97,
                "validation_status": "pending",
            })
    deduplicated = {row["project_key"]: row for row in rows if row["project_key"]}
    return list(deduplicated.values())


def _extract_document_findings(page_texts: list[str] | None) -> list[dict]:
    """Seleciona alegações de risco; o conteúdo permanece pendente de revisão."""
    findings: list[dict] = []
    for page_number, page_text in enumerate(page_texts or [], start=1):
        compact = _compact_report_text(page_text)
        lowered = compact.lower()
        if not any(term in lowered for term in _RISK_TERMS):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", compact):
            sentence_lower = sentence.lower()
            if not any(term in sentence_lower for term in _RISK_TERMS):
                continue
            if "caráter meramente informativo" in sentence_lower:
                continue
            claim = sentence.strip()
            if len(claim) < 45:
                continue
            if any(pattern.search(claim) for pattern in _GENERIC_RISK_SENTENCE_PATTERNS):
                continue
            if (
                "risco" in sentence_lower
                and not any(
                    term in sentence_lower for term in _RISK_TERMS
                    if term != "risco"
                )
                and not any(
                    term in sentence_lower for term in _MATERIAL_RISK_CONTEXT
                )
            ):
                continue
            findings.append({
                "finding_type": "risk",
                "topic": next(term for term in _RISK_TERMS if term in sentence_lower),
                "claim_text": claim[:2000],
                "page_number": page_number,
                "confidence": .80,
                "validation_status": "pending",
            })
            if len(findings) >= 30:
                return findings
    return findings


def _extract_evidence(text_value: str, page_texts: list[str] | None = None,
                      *, fii_type: str | None = None) -> list[dict]:
    evidence = []
    applicable = _TYPE_METRICS.get(str(fii_type or "").lower())
    sources = list(enumerate(page_texts or [], start=1)) or [(None, text_value)]
    for page_number, page_text in sources:
        for metric, pattern in _METRIC_PATTERNS.items():
            if applicable is not None and metric not in applicable:
                continue
            for match in list(pattern.finditer(page_text))[:20]:
                raw = match.group(1)
                try:
                    normalized = _parse_number(raw, percent=metric in _PERCENT_METRICS)
                except ValueError:
                    continue
                start = max(match.start() - 100, 0)
                end = min(match.end() + 100, len(page_text))
                plausible = (0 <= normalized <= 1 if metric in _PERCENT_METRICS
                             else 0 <= normalized <= 50)
                evidence.append({
                    "metric_name": metric, "raw_value": raw,
                    "normalized_value": normalized,
                    "unit": ("%" if metric in _PERCENT_METRICS else
                             "quantidade" if metric == "property_count" else "anos"),
                    "page_number": page_number, "bbox_json": None,
                    "evidence_text": page_text[start:end].replace("\x00", " "),
                    "confidence": (.92 if plausible and metric in _PROVISIONAL_METRICS
                                   else .80 if plausible else .25),
                    "validation_status": "pending" if plausible else "rejected",
                    "value_nature": "manager_reported",
                    "review_priority": 50,
                })
    evidence.extend(_extract_development_evidence(page_texts))
    deduplicated: dict[tuple, dict] = {}
    for row in evidence:
        key = (row["metric_name"], row["normalized_value"], row["page_number"])
        previous = deduplicated.get(key)
        if previous is None or row["confidence"] > previous["confidence"]:
            deduplicated[key] = row
    return list(deduplicated.values())


def _persist_extended_extractions(
    conn,
    *,
    ticker: str | None,
    document_version_id: int,
    extraction_run_id: int,
    reference_date,
    source_published_at,
    knowledge_at,
    projects: list[dict],
    findings: list[dict],
) -> tuple[int, int]:
    """Persiste projetos/achados apenas quando a migration 046 está aplicada."""
    if not ticker or not conn.execute(text(
        "SELECT to_regclass('market.fii_project_observations') IS NOT NULL"
    )).scalar():
        return 0, 0
    project_count = 0
    for row in projects:
        project_id = conn.execute(text("""
            INSERT INTO market.fii_projects (
                ticker,project_key,project_name,normalized_name,city,state
            ) VALUES (
                :ticker,:project_key,:project_name,:normalized_name,:city,:state
            )
            ON CONFLICT (ticker,project_key) DO UPDATE SET
                project_name=EXCLUDED.project_name,
                normalized_name=EXCLUDED.normalized_name,
                city=COALESCE(EXCLUDED.city,market.fii_projects.city),
                state=COALESCE(EXCLUDED.state,market.fii_projects.state),
                updated_at=now()
            RETURNING id
        """), {
            "ticker": ticker, "project_key": row["project_key"],
            "project_name": row["project_name"],
            "normalized_name": _normalized_project_key(row["project_name"]),
            "city": row.get("city"), "state": row.get("state"),
        }).scalar_one()
        optional_project_fields = (
            "stage", "portfolio_weight", "construction_progress",
            "sales_progress", "expected_irr", "expected_result_brl",
            "vgv_brl", "sellable_area_sqm", "unit_count", "financing_type",
            "market_standard",
        )
        project_params = {
            **row,
            **{key: row.get(key) for key in optional_project_fields},
            "project_id": int(project_id), "version": document_version_id,
            "run": extraction_run_id, "reference": reference_date,
            "published": source_published_at, "knowledge": knowledge_at,
            "raw_json": json.dumps(row.get("raw_json") or {}, ensure_ascii=False),
        }
        result = conn.execute(text("""
            INSERT INTO market.fii_project_observations (
                project_id,document_version_id,extraction_run_id,reference_date,
                source_published_at,knowledge_at,project_type,stage,portfolio_weight,
                construction_progress,sales_progress,expected_irr,expected_result_brl,
                vgv_brl,sellable_area_sqm,unit_count,financing_type,market_standard,
                value_nature,raw_json,page_number,evidence_text,confidence,
                validation_status,validation_method
            ) VALUES (
                :project_id,:version,:run,:reference,:published,:knowledge,
                :project_type,:stage,:portfolio_weight,:construction_progress,
                :sales_progress,:expected_irr,:expected_result_brl,:vgv_brl,
                :sellable_area_sqm,:unit_count,:financing_type,:market_standard,
                :value_nature,CAST(:raw_json AS jsonb),:page_number,:evidence_text,
                :confidence,:validation_status,'pending'
            )
            ON CONFLICT (project_id,reference_date,document_version_id) DO NOTHING
        """), project_params)
        project_count += max(int(result.rowcount or 0), 0)
    finding_count = 0
    for row in findings:
        result = conn.execute(text("""
            INSERT INTO market.fii_document_findings (
                document_version_id,extraction_run_id,ticker,reference_date,
                finding_type,topic,claim_text,page_number,confidence,
                validation_status,validation_method
            ) VALUES (
                :version,:run,:ticker,:reference,:finding_type,:topic,:claim_text,
                :page_number,:confidence,:validation_status,'pending'
            )
            ON CONFLICT DO NOTHING
        """), {
            **row, "version": document_version_id, "run": extraction_run_id,
            "ticker": ticker, "reference": reference_date,
        })
        finding_count += max(int(result.rowcount or 0), 0)
    return project_count, finding_count


def _provisional_candidates(evidence: list[dict], *, extraction_confidence: float,
                            layout_changed: bool, enabled: bool = False) -> list[dict]:
    """Rota legada, desabilitada por padrão; revisão humana é o gate atual."""
    if not enabled:
        return []
    if extraction_confidence < .75 or layout_changed:
        return []
    grouped: dict[str, list[dict]] = {}
    for row in evidence:
        if (row.get("metric_name") in _PROVISIONAL_METRICS
                and float(row.get("confidence") or 0) >= .90
                and row.get("validation_status") != "rejected"):
            grouped.setdefault(str(row["metric_name"]), []).append(row)
    selected: list[dict] = []
    for rows in grouped.values():
        values = {round(float(row["normalized_value"]), 8) for row in rows}
        if len(values) == 1:
            selected.append(max(rows, key=lambda row: float(row["confidence"])))
    return selected


def _parser_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _layout_signature(page_texts: list[str], text_value: str) -> str:
    sample = "\n".join(page_texts[:3]) if page_texts else text_value[:12000]
    normalized = re.sub(r"\d+(?:[.,]\d+)?", "#", sample.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _storage(content: bytes, sha: str, suffix: str, *,
             retain_binary: bool = True) -> tuple[str, str | None, bool]:
    """Armazena por conteudo ou preserva somente hash+URL para backfills extensos.

    O modo ``source_hash`` continua auditavel porque a versao guarda SHA-256,
    tamanho e MIME, enquanto a tabela de documentos preserva a URL publica. Um
    arquivo que ja esteja no cache nunca e removido por esta funcao.
    """
    if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
        return "remote_only", None, False
    storage_key = f"{sha[:2]}/{sha}{suffix}"
    target = _cache_root() / storage_key
    existed = target.exists()
    if existed:
        return "local_cache", storage_key, True
    if not retain_binary:
        return "source_hash", None, False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return "local_cache", storage_key, False


_RESTORABLE_DOCUMENT_STATUSES = {
    "pending", "completed", "needs_review", "failed",
}


def _previous_document_status(value: object) -> str:
    status = str(value or "pending")
    return status if status in _RESTORABLE_DOCUMENT_STATUSES else "pending"


def _release_worker_claims(engine, worker: str,
                           previous_statuses: dict[int, str] | None = None) -> int:
    released = 0
    with engine.begin() as conn:
        for document_id, previous_status in (previous_statuses or {}).items():
            result = conn.execute(text("""
                UPDATE market.fii_documents SET processing_status=:status,
                    processing_started_at=NULL,processing_worker=NULL
                WHERE id=:id AND processing_status='processing'
                  AND processing_worker=:worker
            """), {
                "status": _previous_document_status(previous_status),
                "id": int(document_id), "worker": worker,
            })
            released += max(int(result.rowcount or 0), 0)
        result = conn.execute(text("""
            UPDATE market.fii_documents SET processing_status='pending',
                processing_started_at=NULL,processing_worker=NULL
            WHERE processing_status='processing' AND processing_worker=:worker
        """), {"worker": worker})
        released += max(int(result.rowcount or 0), 0)
    return released


def _defer_circuit_claim(engine, *, document_id: int, worker: str,
                         previous_status: str, retry_at: datetime) -> int:
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE market.fii_documents
               SET processing_status=:status,processing_started_at=NULL,
                   processing_worker=NULL,next_retry_at=:retry
             WHERE id=:id AND processing_status='processing'
               AND processing_worker=:worker
        """), {
            "status": _previous_document_status(previous_status),
            "retry": retry_at, "id": int(document_id), "worker": worker,
        })
    return max(int(result.rowcount or 0), 0)


def process_pending_documents(limit: int = 25, *, tickers: list[str] | None = None,
                              recent_months: int = 24,
                              max_batch_bytes: int = 250 * 1024 * 1024,
                              max_document_bytes: int = 30 * 1024 * 1024,
                              min_free_bytes: int = 5 * 1024 * 1024 * 1024,
                              retain_binary: bool = True,
                              download_timeout: int = 60,
                              download_attempts: int = 3,
                              host_failure_threshold: int = 3,
                              host_cooldown_minutes: int = 30,
                              max_documents_per_host: int = 3) -> dict:
    engine = _engine()
    result = {"selected": 0, "downloaded": 0, "unchanged": 0,
              "extracted": 0, "needs_review": 0, "failed": 0,
              "oversized": 0, "released": 0, "bytes_processed": 0,
              "provisional_promoted": 0, "projects_extracted": 0,
              "findings_extracted": 0, "project_conflicts": 0,
              "attempted": 0, "transient_failed": 0,
              "circuit_deferred": 0, "circuit_opened_hosts": 0}
    if engine is None:
        return {**result, "failed": -1, "blocker": "banco indisponível"}
    cache_root = _cache_root()
    free_bytes = shutil.disk_usage(cache_root).free
    result["free_bytes_before"] = free_bytes
    if free_bytes < max(int(min_free_bytes), 0):
        return {**result, "failed": -1,
                "blocker": "reserva mínima de armazenamento não atendida"}
    normalized_tickers = sorted({str(value).upper().replace(".SA", "")
                                 for value in (tickers or []) if value})
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(int(recent_months), 0) * 31)).date()
    worker = f"{os.getenv('GITHUB_RUN_ID') or 'local'}:{uuid.uuid4()}"
    with engine.begin() as conn:
        if not conn.execute(text(
                "SELECT to_regclass('market.fii_documents') IS NOT NULL")).scalar():
            return {**result, "failed": -1, "blocker": "migration 024 pendente"}
        conn.execute(text("""
            INSERT INTO market.fii_parser_versions
                (parser_name, parser_version, schema_version, code_sha256, status, activated_at)
            VALUES (:name, :version, :schema, :sha, 'active', now())
            ON CONFLICT (parser_name, parser_version) DO UPDATE SET
                code_sha256=EXCLUDED.code_sha256, status='active', activated_at=now()
        """), {"name": PARSER_NAME, "version": PARSER_VERSION,
                 "schema": SCHEMA_VERSION, "sha": _parser_hash()})
        if not conn.execute(text("""
            SELECT EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema='market' AND table_name='fii_documents'
              AND column_name='processing_status')
        """)).scalar():
            return {**result, "failed": -1, "blocker": "migration 029 pendente"}
        docs = [dict(row) for row in conn.execute(text("""
            WITH ranked AS (
              SELECT d.id,d.processing_status AS previous_status,
                     d.reference_date,
                     CASE WHEN f.score_version IS NOT NULL AND f.price>0
                                    AND f.liquidez_diaria>0 THEN 0
                          WHEN f.price>0 AND f.liquidez_diaria>0 THEN 1 ELSE 2 END
                          AS selection_priority,
                     row_number() OVER (
                       PARTITION BY lower(split_part(split_part(
                           d.source_url,'://',2),'/',1))
                       ORDER BY CASE WHEN f.score_version IS NOT NULL AND f.price>0
                                          AND f.liquidez_diaria>0 THEN 0
                                     WHEN f.price>0 AND f.liquidez_diaria>0
                                          THEN 1 ELSE 2 END,
                                CASE WHEN d.document_type='RELAT GERENCIAL'
                                     THEN 0 ELSE 1 END,
                                d.reference_date DESC NULLS LAST,d.id DESC
                     ) AS host_rank
                FROM market.fii_documents d
              LEFT JOIN market.fiis f ON f.ticker=d.ticker
              WHERE NOT EXISTS (
                SELECT 1 FROM market.fii_extraction_runs r
                WHERE r.document_version_id=d.current_version_id
                  AND r.parser_name=:name AND r.parser_version=:parser
                  AND r.status IN ('passed','needs_review')
              ) AND (
                d.processing_status IN ('pending','completed','needs_review','failed')
                OR (d.processing_status='processing' AND
                    d.processing_started_at<now()-interval '2 hours')
              )
                AND (d.next_retry_at IS NULL OR d.next_retry_at<=now())
                AND d.source_url LIKE 'https://%'
                AND d.natural_key NOT LIKE 'manual-pilot:%'
                AND (:ticker_filter=false OR d.ticker=ANY(CAST(:tickers AS text[])))
                AND (:recent_months=0 OR d.reference_date IS NULL OR d.reference_date>=:cutoff)
                AND NOT EXISTS (
                  SELECT 1 FROM market.fii_audit_events a
                   WHERE a.event_type='document_host_circuit_opened'
                     AND a.entity_type='fii_document_host'
                     AND a.entity_id=lower(split_part(split_part(
                         d.source_url,'://',2),'/',1))
                     AND a.created_at > now()
                         - (:cooldown_minutes * interval '1 minute')
                )
            ), candidates AS (
              SELECT d.id,r.previous_status
                FROM ranked r
                JOIN market.fii_documents d ON d.id=r.id
               WHERE r.host_rank<=:max_per_host
               ORDER BY r.selection_priority,r.reference_date DESC NULLS LAST,d.id DESC
               LIMIT :limit FOR UPDATE OF d SKIP LOCKED
            )
            UPDATE market.fii_documents d
            SET processing_status='processing', processing_started_at=now(),
                processing_worker=:worker, processing_attempts=processing_attempts+1,
                next_retry_at=NULL, last_error=NULL
            FROM candidates c WHERE c.id=d.id
            RETURNING d.*,c.previous_status
        """), {"limit": max(int(limit), 1), "name": PARSER_NAME,
                 "parser": PARSER_VERSION, "worker": worker,
                 "ticker_filter": bool(normalized_tickers),
                 "tickers": normalized_tickers, "recent_months": max(int(recent_months), 0),
                 "cutoff": cutoff,
                 "cooldown_minutes": max(int(host_cooldown_minutes), 1),
                 "max_per_host": max(int(max_documents_per_host), 1)}).mappings().all()]
        selected_tickers = sorted({str(row.get("ticker")) for row in docs if row.get("ticker")})
        type_map = {}
        official_hosts: dict[str, list[str]] = {}
        if selected_tickers:
            type_map = dict(conn.execute(text(
                "SELECT ticker,tipo FROM market.fiis WHERE ticker=ANY(CAST(:tickers AS text[]))"
            ), {"tickers": selected_tickers}).all())
            if conn.execute(text(
                "SELECT to_regclass('market.fii_document_sources') IS NOT NULL"
            )).scalar():
                for ticker_value, allowed_host in conn.execute(text("""
                    SELECT ticker,allowed_host FROM market.fii_document_sources
                    WHERE enabled AND ticker=ANY(CAST(:tickers AS text[]))
                """), {"tickers": selected_tickers}).all():
                    official_hosts.setdefault(str(ticker_value), []).append(
                        str(allowed_host)
                    )
    result["selected"] = len(docs)
    previous_statuses = {
        int(row["id"]): _previous_document_status(row.get("previous_status"))
        for row in docs
    }
    circuit = _HostCircuitBreaker(host_failure_threshold)
    for doc in docs:
        host = ""
        try:
            host = _download_host(str(doc.get("source_url") or ""))
            if circuit.is_open(host):
                retry_at = datetime.now(timezone.utc) + timedelta(
                    minutes=max(int(host_cooldown_minutes), 1)
                )
                result["circuit_deferred"] += _defer_circuit_claim(
                    engine, document_id=int(doc["id"]), worker=worker,
                    previous_status=previous_statuses[int(doc["id"])],
                    retry_at=retry_at,
                )
                continue
            result["attempted"] += 1
            attempt_number = max(int(doc.get("processing_attempts") or 1), 1)
            adaptive_timeout = max(int(download_timeout), 5) * min(attempt_number, 3)
            allowed_host = None
            if str(doc.get("natural_key") or "").startswith("official-ri:"):
                from data_pipeline.market.fii_ri_documents import _host_allowed
                candidates = official_hosts.get(str(doc.get("ticker") or ""), [])
                allowed_host = next((
                    host for host in candidates
                    if _host_allowed(str(doc["source_url"]), host)
                ), None)
                if not allowed_host:
                    raise ValueError(
                        "documento RI sem fonte oficial habilitada para o host"
                    )
            content, mime = _download(str(doc["source_url"]),
                                      timeout=adaptive_timeout,
                                      max_bytes=max(int(max_document_bytes), 1),
                                      attempts=max(int(download_attempts), 1),
                                      allowed_host=allowed_host,
                                      max_elapsed_seconds=(
                                          adaptive_timeout
                                          * max(int(download_attempts), 1) + 15
                                      ))
            circuit.success(host)
            if result["bytes_processed"] + len(content) > max(int(max_batch_bytes), 1):
                result["budget_exhausted"] = True
                break
            result["bytes_processed"] += len(content)
            sha = hashlib.sha256(content).hexdigest()
            suffix = ".pdf" if content[:4] == b"%PDF" or "pdf" in mime.lower() else ".bin"
            storage_backend, storage_key, existed = _storage(
                content, sha, suffix, retain_binary=bool(retain_binary))
            if not existed:
                result["downloaded"] += 1
            else:
                result["unchanged"] += 1
            with engine.begin() as conn:
                existing = conn.execute(text("""
                    SELECT id FROM market.fii_document_versions
                    WHERE document_id=:doc AND content_sha256=:sha
                """), {"doc": doc["id"], "sha": sha}).scalar()
                if existing:
                    version_id = int(existing)
                else:
                    previous = conn.execute(text("""
                        SELECT id, revision_no FROM market.fii_document_versions
                        WHERE document_id=:doc ORDER BY revision_no DESC LIMIT 1
                    """), {"doc": doc["id"]}).mappings().first()
                    version_id = int(conn.execute(text("""
                        INSERT INTO market.fii_document_versions (
                            document_id, revision_no, content_sha256, storage_backend,
                            storage_key, mime_type, byte_size, supersedes_id
                        ) VALUES (:doc, :revision, :sha, :backend, :key, :mime, :size, :previous)
                        RETURNING id
                    """), {"doc": doc["id"], "revision": int(previous["revision_no"]) + 1 if previous else 1,
                             "sha": sha, "key": storage_key, "mime": mime,
                             "backend": storage_backend,
                             "size": len(content), "previous": int(previous["id"]) if previous else None}).scalar())
                    conn.execute(text("UPDATE market.fii_documents SET current_version_id=:version WHERE id=:doc"),
                                 {"version": version_id, "doc": doc["id"]})
                already = conn.execute(text("""
                    SELECT id FROM market.fii_extraction_runs
                    WHERE document_version_id=:version AND parser_name=:name
                      AND parser_version=:parser AND status IN ('passed','needs_review')
                """), {"version": version_id, "name": PARSER_NAME,
                         "parser": PARSER_VERSION}).scalar()
            if already:
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE market.fii_documents SET processing_status='completed',
                            processing_started_at=NULL,processing_worker=NULL,last_error=NULL
                        WHERE id=:id AND processing_worker=:worker
                    """), {"id": doc["id"], "worker": worker})
                continue
            if suffix != ".pdf":
                raise ValueError(f"tipo documental não suportado: {mime}")
            extracted, pages, method, page_texts = _extract_pdf_text(content)
            signature = _layout_signature(page_texts, extracted)
            fii_type = str(type_map.get(str(doc.get("ticker"))) or "").lower() or None
            evidence = _extract_evidence(extracted, page_texts, fii_type=fii_type)
            projects = _extract_development_projects(page_texts)
            findings = _extract_document_findings(page_texts)
            confidence = min(1.0, len(extracted) / max(pages * 800, 1))
            with engine.begin() as conn:
                previous_layout = conn.execute(text("""
                    SELECT r.layout_signature
                    FROM market.fii_extraction_runs r
                    JOIN market.fii_document_versions v ON v.id=r.document_version_id
                    WHERE v.document_id=:document AND v.id<>:version
                      AND r.parser_name=:name AND r.layout_signature IS NOT NULL
                      AND r.status IN ('passed','needs_review')
                    ORDER BY r.finished_at DESC NULLS LAST,r.id DESC LIMIT 1
                """), {"document": doc["id"], "version": version_id,
                         "name": PARSER_NAME}).scalar()
                layout_changed = bool(previous_layout and previous_layout != signature)
                if layout_changed:
                    confidence = max(confidence - .15, 0.0)
                provisional = _provisional_candidates(
                    evidence, extraction_confidence=confidence,
                    layout_changed=layout_changed,
                )
                status = "needs_review" if evidence or confidence < .60 or layout_changed else "passed"
                run_id = conn.execute(text("""
                    INSERT INTO market.fii_extraction_runs (
                        document_version_id, parser_name, parser_version, status,
                        text_method, confidence, layout_signature, metrics_json, finished_at
                    ) VALUES (:version, :name, :parser, :status, :method, :confidence,
                              :layout, CAST(:metrics AS jsonb), now()) RETURNING id
                """), {"version": version_id, "name": PARSER_NAME,
                         "parser": PARSER_VERSION, "status": status, "method": method,
                         "confidence": confidence, "layout": signature,
                         "metrics": json.dumps({"characters": len(extracted), "pages": pages,
                                                "evidence_count": len(evidence),
                                                "project_count": len(projects),
                                                "finding_count": len(findings),
                                                "provisional_count": len(provisional),
                                                "layout_changed": layout_changed,
                                                "fii_type_profile": fii_type or "unknown"})}).scalar()
                conn.execute(text("UPDATE market.fii_document_versions SET page_count=:pages WHERE id=:id"),
                             {"pages": pages, "id": version_id})
                extended_evidence = conn.execute(text("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema='market'
                          AND table_name='fii_extraction_evidence'
                          AND column_name='value_nature'
                    )
                """)).scalar()
                for row in evidence:
                    insert_sql = """
                        INSERT INTO market.fii_extraction_evidence (
                            extraction_run_id, metric_name, raw_value, normalized_value,
                            unit, page_number, bbox_json, evidence_text, confidence,
                            validation_status, validation_method
                            {extended_columns}
                        ) VALUES (:run, :metric, :raw, CAST(:normalized AS jsonb), :unit,
                                  :page, CAST(:bbox AS jsonb), :evidence, :confidence, :status,
                                  :validation_method {extended_values})
                        RETURNING id
                    """.format(
                        extended_columns=(
                            ", value_nature, review_priority" if extended_evidence else ""
                        ),
                        extended_values=(
                            ", :value_nature, :review_priority" if extended_evidence else ""
                        ),
                    )
                    evidence_id = conn.execute(text(insert_sql), {
                             "run": run_id, "metric": row["metric_name"], "raw": row["raw_value"],
                             "normalized": json.dumps(row["normalized_value"]), "unit": row["unit"],
                             "page": row["page_number"], "bbox": json.dumps(row["bbox_json"]),
                              "evidence": row["evidence_text"], "confidence": row["confidence"],
                              "status": row["validation_status"],
                              "value_nature": row.get("value_nature", "manager_reported"),
                              "review_priority": int(row.get("review_priority", 50)),
                              "validation_method": ("parser_rule" if row["validation_status"] == "rejected"
                                                    else "pending")}).scalar()
                    row["evidence_id"] = int(evidence_id)
                observed_at = doc.get("first_observed_at") or datetime.now(timezone.utc)
                reference = doc.get("reference_date") or observed_at.date()
                published_at = doc.get("source_published_at")
                project_count, finding_count = _persist_extended_extractions(
                    conn,
                    ticker=str(doc.get("ticker") or "") or None,
                    document_version_id=int(version_id),
                    extraction_run_id=int(run_id),
                    reference_date=reference,
                    source_published_at=published_at,
                    knowledge_at=observed_at,
                    projects=projects,
                    findings=findings,
                )
                result["projects_extracted"] += project_count
                result["findings_extracted"] += finding_count
                provisional_observations = []
                for row in provisional if doc.get("ticker") else []:
                    observation = metric_observation(
                        ticker=str(doc.get("ticker") or ""),
                        metric_name=str(row["metric_name"]),
                        value=float(row["normalized_value"]),
                        reference_date=reference,
                        available_at=observed_at,
                        source="public_fii_report_provisional_v1",
                        vintage=f"document:{reference}:{sha[:16]}",
                        source_published_at=published_at,
                        availability_quality=("verified_publication" if published_at
                                              else "first_observed_proxy"),
                        metadata={
                            "document_id": int(doc["id"]),
                            "document_version_id": version_id,
                            "extraction_run_id": int(run_id),
                            "parser_name": PARSER_NAME,
                            "parser_version": PARSER_VERSION,
                            "page_number": row.get("page_number"),
                            "evidence_id": row.get("evidence_id"),
                            "evidence_confidence": row.get("confidence"),
                            "validation_status": "provisional_requires_human_review",
                            "rule": "single_explicit_value_and_stable_layout",
                        },
                    )
                    observation["source_url"] = str(doc.get("source_url") or "")
                    provisional_observations.append(observation)
                if provisional_observations:
                    result["provisional_promoted"] += repo.upsert(
                        conn, "fii_metric_observations", provisional_observations)
                conn.execute(text("""
                    UPDATE market.fii_documents
                    SET processing_status=:status, processing_started_at=NULL,
                        processing_worker=NULL,next_retry_at=NULL,last_error=NULL
                    WHERE id=:id AND processing_worker=:worker
                """), {"status": "needs_review" if status == "needs_review" else "completed",
                         "id": doc["id"], "worker": worker})
            result["extracted"] += 1
            result["needs_review"] += int(status == "needs_review")
        except DocumentTooLargeError as exc:
            logger.warning("Documento FII %s excedeu o limite: %s", doc.get("id"), exc)
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE market.fii_documents
                    SET processing_status='failed',processing_started_at=NULL,
                        processing_worker=NULL,next_retry_at=now()+interval '30 days',
                        last_error=:error
                    WHERE id=:id AND processing_worker=:worker
                """), {"error": str(exc)[:1000], "id": doc.get("id"),
                         "worker": worker})
                conn.execute(text("""
                    INSERT INTO market.fii_audit_events (
                        event_type,entity_type,entity_id,parser_version,payload_json
                    ) VALUES ('document_rejected_size','fii_document',:id,:parser,
                              CAST(:payload AS jsonb))
                """), {"id": str(doc.get("id")), "parser": PARSER_VERSION,
                         "payload": json.dumps({"message": str(exc)[:500],
                                                "limit_bytes": max_document_bytes})})
            result["oversized"] += 1
        except Exception as exc:
            logger.warning("Documento FII %s falhou: %s", doc.get("id"), exc)
            transient = _is_transient_download_error(exc)
            opened_now = circuit.failure(host, transient=transient)
            result["transient_failed"] += int(transient)
            result["circuit_opened_hosts"] += int(opened_now)
            try:
                with engine.begin() as conn:
                    retry_at = datetime.now(timezone.utc) + timedelta(
                        hours=min(2 ** max(int(doc.get("processing_attempts") or 1), 1), 24))
                    conn.execute(text("""
                        UPDATE market.fii_documents
                        SET processing_status='failed',processing_started_at=NULL,
                            processing_worker=NULL,next_retry_at=:retry,last_error=:error
                        WHERE id=:id AND processing_worker=:worker
                    """), {"retry": retry_at, "error": str(exc)[:1000],
                             "id": doc.get("id"), "worker": worker})
                    conn.execute(text("""
                        INSERT INTO market.fii_audit_events (
                            event_type, entity_type, entity_id, parser_version, payload_json
                        ) VALUES ('document_download_failed', 'fii_document', :id, :parser,
                                  CAST(:payload AS jsonb))
                    """), {"id": str(doc.get("id")), "parser": PARSER_VERSION,
                             "payload": json.dumps({"error_type": type(exc).__name__,
                                                    "message": str(exc)[:500]})})
                    if opened_now:
                        conn.execute(text("""
                            INSERT INTO market.fii_audit_events (
                                event_type,entity_type,entity_id,parser_version,payload_json
                            ) VALUES (
                                'document_host_circuit_opened','fii_document_host',
                                :host,:parser,CAST(:payload AS jsonb)
                            )
                        """), {
                            "host": host, "parser": PARSER_VERSION,
                            "payload": json.dumps({
                                "failure_threshold": max(int(host_failure_threshold), 1),
                                "cooldown_minutes": max(int(host_cooldown_minutes), 1),
                                "error_type": type(exc).__name__,
                            }),
                        })
            except Exception:
                logger.debug("Falha ao auditar erro documental", exc_info=True)
            result["failed"] += 1
    result["released"] = _release_worker_claims(
        engine, worker, previous_statuses=previous_statuses,
    )
    result["failure_rate"] = (
        result["failed"] / result["attempted"] if result["attempted"] else 0.0
    )
    result["selection_failure_rate"] = (
        result["failed"] / result["selected"] if result["selected"] else 0.0
    )
    if result["projects_extracted"]:
        try:
            from data_pipeline.market.fii_project_reconciliation import (
                detect_project_conflicts,
            )
            reconciliation = detect_project_conflicts(tickers=normalized_tickers)
            result["project_conflicts"] = int(reconciliation.get("conflicts") or 0)
        except Exception:
            logger.warning("Reconciliação de projetos FII falhou", exc_info=True)
    return result

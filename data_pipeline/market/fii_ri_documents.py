"""Coleta segura e auditável de documentos oficiais de FIIs.

O coletor navega apenas em hosts previamente cadastrados na allowlist
``market.fii_document_sources``. Ele descobre PDFs e os cadastra na fila já
existente; download, hash, versionamento e extração continuam a cargo de
``fii_documents.process_pending_documents``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import lru_cache
import hashlib
import ipaddress
import json
import re
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
import requests
from sqlalchemy import text

from data_pipeline.utils.db_utils import get_pipeline_engine


USER_AGENT = "DashboardFinanceiro/1.0 (+official-fii-document-audit)"
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
_REPORT_TERMS = (
    "relatorio", "relatório", "gerencial", "mensal", "trimestral",
    "informe", "resultado", "portfolio", "portfólio",
)


@dataclass(frozen=True)
class DiscoveredDocument:
    url: str
    title: str
    reference_date: date | None
    source_published_at: datetime | None = None
    document_type: str = "RELAT GERENCIAL"

    @property
    def natural_key(self) -> str:
        digest = hashlib.sha256(self.url.encode("utf-8")).hexdigest()
        return f"official-ri:{digest}"


def _infer_document_type(searchable: str) -> str:
    normalized = str(searchable).lower()
    if "informe" in normalized and not any(
        term in normalized for term in ("relatorio", "relatório", "gerencial")
    ):
        return "INFORME RI"
    if "resultado" in normalized and not any(
        term in normalized for term in ("relatorio", "relatório", "gerencial")
    ):
        return "RESULTADO RI"
    return "RELAT GERENCIAL"


def _normalized_host(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return str(parsed.hostname or "").rstrip(".").lower()


def _host_allowed(host: str, allowed_host: str) -> bool:
    host = _normalized_host(host)
    allowed = _normalized_host(allowed_host)
    return bool(host and allowed and (host == allowed or host.endswith(f".{allowed}")))


@lru_cache(maxsize=256)
def _assert_public_host(host: str) -> None:
    """Bloqueia destinos locais/privados, inclusive quando resolvidos por DNS."""
    normalized = _normalized_host(host)
    if not normalized or normalized in {"localhost", "localhost.localdomain"}:
        raise ValueError("host oficial inválido")
    try:
        literal = ipaddress.ip_address(normalized)
        addresses = [literal]
    except ValueError:
        addresses = []
        for item in socket.getaddrinfo(normalized, 443, type=socket.SOCK_STREAM):
            try:
                addresses.append(ipaddress.ip_address(item[4][0]))
            except ValueError:
                continue
    if not addresses:
        raise ValueError(f"host oficial sem resolução pública: {normalized}")
    if any(not address.is_global for address in addresses):
        raise ValueError(f"host oficial resolve para endereço não público: {normalized}")


def validate_official_url(url: str, allowed_host: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https":
        raise ValueError("fontes oficiais exigem HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("URL oficial não pode conter credenciais")
    if not _host_allowed(parsed.hostname or "", allowed_host):
        raise ValueError("URL fora do host oficial permitido")
    _assert_public_host(parsed.hostname or "")
    normalized = parsed._replace(fragment="")
    return urlunparse(normalized)


def _safe_get(
    url: str,
    *,
    allowed_host: str,
    timeout: int = 30,
    max_bytes: int = MAX_HTML_BYTES,
    session: requests.Session | None = None,
    accept: str = "text/html,application/xhtml+xml",
) -> tuple[bytes, str, str]:
    client = session or requests.Session()
    current = validate_official_url(url, allowed_host)
    for _ in range(MAX_REDIRECTS + 1):
        response = client.get(
            current,
            timeout=timeout,
            stream=True,
            allow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": accept},
        )
        try:
            if response.is_redirect or response.is_permanent_redirect:
                target = response.headers.get("Location")
                if not target:
                    raise ValueError("redirecionamento oficial sem destino")
                current = validate_official_url(urljoin(current, target), allowed_host)
                continue
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise ValueError("página oficial excede o limite de coleta")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("página oficial excedeu o limite durante a coleta")
                chunks.append(chunk)
            return (
                b"".join(chunks),
                str(response.headers.get("Content-Type") or "").split(";")[0].lower(),
                current,
            )
        finally:
            response.close()
    raise ValueError("excesso de redirecionamentos na fonte oficial")


def _infer_reference_date(value: str) -> date | None:
    text_value = str(value)
    patterns = (
        (r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(?!\d)", "ymd"),
        (r"(?<!\d)(20\d{2})[-_/](0[1-9]|1[0-2])(?:[-_/]([0-3]\d))?(?!\d)", "ymd"),
        (r"(?<!\d)([0-3]\d)[-_/](0[1-9]|1[0-2])[-_/](20\d{2})(?!\d)", "dmy"),
    )
    for pattern, order in patterns:
        match = re.search(pattern, text_value)
        if not match:
            continue
        try:
            if order == "ymd":
                values = match.groups()
                year, month = values[:2]
                day = values[2] if len(values) > 2 else None
            else:
                day, month, year = match.groups()
            return date(int(year), int(month), int(day or 1))
        except ValueError:
            continue
    month_names = {
        "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
        "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
    }
    match = re.search(
        r"\b(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)[a-zç]*\D{0,8}(20\d{2})\b",
        text_value.lower(),
    )
    if match:
        return date(int(match.group(2)), month_names[match.group(1)], 1)
    return None


def discover_pdf_links(
    html: bytes | str,
    *,
    page_url: str,
    allowed_host: str,
    ticker: str | None = None,
    ticker_aliases: list[str] | None = None,
    single_fund_page: bool = False,
    limit: int = 250,
) -> list[DiscoveredDocument]:
    """Extrai links PDF do próprio host e devolve ordem determinística."""
    validate_official_url(page_url, allowed_host)
    soup = BeautifulSoup(html, "html.parser")
    ticker_value = str(ticker or "").upper().replace(".SA", "")
    identity_terms = {
        ticker_value.lower(),
        *(str(value).strip().lower() for value in (ticker_aliases or []) if value),
    } - {""}
    found: dict[str, DiscoveredDocument] = {}
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme != "https" or not _host_allowed(parsed.hostname or "", allowed_host):
            continue
        label = " ".join(anchor.get_text(" ", strip=True).split())
        searchable = f"{label} {parsed.path} {parsed.query}".lower()
        scoped_parent = anchor.find_parent(attrs={"data-content": True})
        scoped_identity = (
            str(scoped_parent.get("data-content") or "").lower()
            if scoped_parent is not None else ""
        )
        is_pdf = parsed.path.lower().endswith(".pdf") or ".pdf?" in absolute.lower()
        if not is_pdf or not any(term in searchable for term in _REPORT_TERMS):
            continue
        if identity_terms and not single_fund_page and not any(
            term in searchable or term in scoped_identity for term in identity_terms
        ):
            continue
        normalized = urlunparse(parsed._replace(fragment=""))
        try:
            normalized = validate_official_url(normalized, allowed_host)
        except ValueError:
            continue
        found[normalized] = DiscoveredDocument(
            url=normalized,
            title=label or parsed.path.rsplit("/", 1)[-1],
            reference_date=_infer_reference_date(
                f"{label} {parsed.path.rsplit('/', 1)[-1]}"
            ),
            document_type=_infer_document_type(searchable),
        )
        if len(found) >= max(1, min(int(limit), 1000)):
            break
    return sorted(
        found.values(),
        key=lambda item: (item.reference_date or date.min, item.url),
        reverse=True,
    )


def discover_wordpress_media(
    payload: bytes | str | list[dict[str, Any]],
    *,
    allowed_host: str,
    ticker: str,
    ticker_aliases: list[str] | None = None,
    limit: int = 250,
) -> list[DiscoveredDocument]:
    if not isinstance(payload, list):
        payload = json.loads(
            payload.decode("utf-8") if isinstance(payload, bytes) else payload
        )
    if not isinstance(payload, list):
        raise ValueError("resposta WordPress de mídia não é uma lista")
    identities = {
        str(ticker).lower().replace(".sa", ""),
        *(str(value).strip().lower() for value in (ticker_aliases or []) if value),
    } - {""}
    found: dict[str, DiscoveredDocument] = {}
    for item in payload[:max(1, min(int(limit), 1000))]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("source_url") or "").strip()
        slug = str(item.get("slug") or "")
        title_value = item.get("title") or {}
        title = str(
            title_value.get("rendered") if isinstance(title_value, dict)
            else title_value
        )
        searchable = f"{slug} {title} {url}".lower()
        if not url.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        if not any(term in searchable for term in _REPORT_TERMS):
            continue
        if identities and not any(identity in searchable for identity in identities):
            continue
        try:
            normalized = validate_official_url(url, allowed_host)
        except ValueError:
            continue
        published = None
        raw_published = item.get("date_gmt") or item.get("date")
        if raw_published:
            try:
                published = datetime.fromisoformat(
                    str(raw_published).replace("Z", "+00:00")
                )
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
            except ValueError:
                published = None
        found[normalized] = DiscoveredDocument(
            url=normalized,
            title=title or slug or normalized.rsplit("/", 1)[-1],
            reference_date=_infer_reference_date(f"{slug} {title} {url.rsplit('/', 1)[-1]}"),
            source_published_at=published,
            document_type=_infer_document_type(searchable),
        )
    return sorted(
        found.values(),
        key=lambda item: (
            item.reference_date or date.min,
            item.source_published_at or datetime.min.replace(tzinfo=timezone.utc),
            item.url,
        ),
        reverse=True,
    )


def download_official_document(
    url: str,
    *,
    allowed_host: str,
    timeout: int = 60,
    max_bytes: int = 30 * 1024 * 1024,
) -> tuple[bytes, str]:
    content, mime, _ = _safe_get(
        url,
        allowed_host=allowed_host,
        timeout=timeout,
        max_bytes=max_bytes,
        accept="application/pdf,application/octet-stream;q=0.9,*/*;q=0.1",
    )
    if not content:
        raise ValueError("documento oficial vazio")
    return content, mime or "application/octet-stream"


def register_document_source(
    *,
    ticker: str | None,
    source_type: str,
    official_name: str,
    base_url: str,
    documents_url: str,
    allowed_host: str,
    cnpj: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("banco indisponível")
    base = validate_official_url(base_url, allowed_host)
    documents = validate_official_url(documents_url, allowed_host)
    normalized_ticker = str(ticker or "").upper().replace(".SA", "") or None
    with engine.begin() as conn:
        source_id = conn.execute(text("""
            INSERT INTO market.fii_document_sources (
                ticker,cnpj,source_type,official_name,base_url,documents_url,
                allowed_host,metadata_json
            ) VALUES (
                :ticker,:cnpj,:source_type,:official_name,:base_url,:documents_url,
                :allowed_host,CAST(:metadata AS jsonb)
            )
            ON CONFLICT (source_type,documents_url) DO UPDATE SET
                ticker=EXCLUDED.ticker,cnpj=EXCLUDED.cnpj,
                official_name=EXCLUDED.official_name,base_url=EXCLUDED.base_url,
                allowed_host=EXCLUDED.allowed_host,
                metadata_json=EXCLUDED.metadata_json,updated_at=now()
            RETURNING id
        """), {
            "ticker": normalized_ticker, "cnpj": cnpj, "source_type": source_type,
            "official_name": official_name, "base_url": base,
            "documents_url": documents, "allowed_host": _normalized_host(allowed_host),
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        }).scalar_one()
    return int(source_id)


def collect_document_source(
    source_id: int,
    *,
    timeout: int = 30,
    polite_delay_seconds: float = 0.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Descobre documentos e os cadastra; nunca processa nem promove métricas."""
    engine = get_pipeline_engine()
    if engine is None:
        raise RuntimeError("banco indisponível")
    with engine.connect() as conn:
        source = conn.execute(text("""
            SELECT * FROM market.fii_document_sources
            WHERE id=:id AND enabled
        """), {"id": int(source_id)}).mappings().first()
    if not source:
        raise LookupError(f"fonte oficial {source_id} não encontrada ou desabilitada")

    checked_at = datetime.now(timezone.utc)
    try:
        if polite_delay_seconds > 0:
            time.sleep(min(float(polite_delay_seconds), 10.0))
        content, mime, final_url = _safe_get(
            str(source["documents_url"]),
            allowed_host=str(source["allowed_host"]),
            timeout=timeout,
            session=session,
        )
        aliases = list((source["metadata_json"] or {}).get("ticker_aliases", []))
        if mime == "application/json":
            documents = discover_wordpress_media(
                content,
                allowed_host=str(source["allowed_host"]),
                ticker=str(source["ticker"] or ""),
                ticker_aliases=aliases,
            )
        else:
            if mime and mime not in {"text/html", "application/xhtml+xml"}:
                raise ValueError(f"página de documentos retornou MIME inesperado: {mime}")
            documents = discover_pdf_links(
                content,
                page_url=final_url,
                allowed_host=str(source["allowed_host"]),
                ticker=source["ticker"],
                ticker_aliases=aliases,
                single_fund_page=bool((source["metadata_json"] or {}).get(
                    "single_fund_page", False
                )),
            )
        inserted = 0
        with engine.begin() as conn:
            for document in documents:
                result = conn.execute(text("""
                    INSERT INTO market.fii_documents (
                        ticker,document_type,natural_key,reference_date,
                        source_published_at,first_observed_at,source_url,processing_status
                    ) VALUES (
                        :ticker,:document_type,:natural_key,:reference_date,
                        :published,:observed,:source_url,'pending'
                    )
                    ON CONFLICT (document_type,natural_key) DO UPDATE SET
                        ticker=COALESCE(market.fii_documents.ticker,EXCLUDED.ticker),
                        reference_date=COALESCE(
                            market.fii_documents.reference_date,EXCLUDED.reference_date
                        ),
                        source_published_at=COALESCE(
                            market.fii_documents.source_published_at,
                            EXCLUDED.source_published_at
                        ),
                        source_url=EXCLUDED.source_url
                    RETURNING (xmax = 0) AS was_inserted
                """), {
                    "ticker": source["ticker"], "document_type": document.document_type,
                    "natural_key": document.natural_key,
                    "reference_date": document.reference_date,
                    "published": document.source_published_at,
                    "observed": checked_at, "source_url": document.url,
                }).scalar()
                inserted += int(bool(result))
            conn.execute(text("""
                UPDATE market.fii_document_sources
                SET last_checked_at=:checked,last_success_at=:checked,last_error=NULL,
                    updated_at=now()
                WHERE id=:id
            """), {"checked": checked_at, "id": int(source_id)})
        return {
            "source_id": int(source_id), "discovered": len(documents),
            "inserted": inserted, "already_known": len(documents) - inserted,
            "checked_at": checked_at.isoformat(), "status": "ok",
        }
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE market.fii_document_sources
                SET last_checked_at=:checked,last_error=:error,updated_at=now()
                WHERE id=:id
            """), {
                "checked": checked_at, "error": f"{type(exc).__name__}: {exc}"[:1000],
                "id": int(source_id),
            })
        raise


def collect_due_document_sources(
    *, limit: int = 25, timeout: int = 30,
    polite_delay_seconds: float = .25,
) -> dict[str, Any]:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "selected": 0, "sources": []}
    with engine.connect() as conn:
        if not conn.execute(text(
            "SELECT to_regclass('market.fii_document_sources') IS NOT NULL"
        )).scalar():
            return {
                "status": "blocked", "selected": 0, "sources": [],
                "reason": "migration 046 pendente",
            }
        source_ids = list(conn.execute(text("""
            SELECT id FROM market.fii_document_sources
            WHERE enabled
              AND (
                last_checked_at IS NULL
                OR last_checked_at
                   <= now()-(collection_interval_hours * interval '1 hour')
              )
            ORDER BY last_checked_at NULLS FIRST,id
            LIMIT :limit
        """), {"limit": max(1, min(int(limit), 500))}).scalars())
    reports = []
    for source_id in source_ids:
        try:
            reports.append(collect_document_source(
                int(source_id),
                timeout=timeout,
                polite_delay_seconds=polite_delay_seconds,
            ))
        except Exception as exc:
            reports.append({
                "source_id": int(source_id), "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"[:1000],
            })
    failed = sum(report.get("status") == "failed" for report in reports)
    return {
        "status": "partial" if failed else "completed",
        "selected": len(source_ids), "failed": failed, "sources": reports,
        "discovered": sum(int(report.get("discovered") or 0) for report in reports),
        "inserted": sum(int(report.get("inserted") or 0) for report in reports),
    }

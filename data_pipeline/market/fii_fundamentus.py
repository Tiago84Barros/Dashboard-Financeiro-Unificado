"""Descoberta auxiliar de relatórios FII indexados pelo Fundamentus.

O Fundamentus é usado somente como catálogo. Os documentos aceitos precisam
apontar diretamente para o endpoint HTTPS do Fundos.NET, que permanece como
fonte canônica. Este módulo não baixa PDFs, não extrai evidências e não altera
scores.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from calendar import monthrange
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy import text

from data_pipeline.utils.db_utils import get_pipeline_engine

DISCOVERY_VERSION = "fundamentus_fnet_index_v1"
FUNDAMENTUS_HOST = "www.fundamentus.com.br"
FUNDOSNET_HOST = "fnet.bmfbovespa.com.br"
FUNDAMENTUS_PATH = "/fii_relatorios.php"
FUNDOSNET_PATH = "/fnet/publico/downloadDocumento"
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
USER_AGENT = "DashboardFinanceiro/1.0 (+fii-document-discovery-audit)"
_TICKER_RE = re.compile(r"^[A-Z]{4}[0-9]{2}$")
_MONTH_RE = re.compile(r"(?<!\d)(0[1-9]|1[0-2])/(20\d{2})(?!\d)")


@dataclass(frozen=True)
class FundamentusReportCandidate:
    """Referência a um documento oficial descoberta em um índice externo."""

    ticker: str
    document_id: int
    reference_date: date
    discovery_url: str
    source_url: str
    document_type: str = "RELAT GERENCIAL"

    @property
    def natural_key(self) -> str:
        return f"{self.ticker}|{self.document_type}|{self.document_id}"


@dataclass(frozen=True)
class FundamentusDiscoveryResult:
    ticker: str
    discovery_url: str
    fetched_at: datetime
    page_sha256: str
    candidates: tuple[FundamentusReportCandidate, ...]
    rejected_links: int = 0


def normalize_ticker(ticker: str) -> str:
    value = str(ticker or "").strip().upper().replace(".SA", "")
    if not _TICKER_RE.fullmatch(value):
        raise ValueError("ticker FII inválido; esperado formato AAAA11")
    return value


def fundamentus_reports_url(ticker: str) -> str:
    value = normalize_ticker(ticker)
    return urlunparse((
        "https", FUNDAMENTUS_HOST, FUNDAMENTUS_PATH, "",
        urlencode({"papel": value}), "",
    ))


def validate_fundamentus_url(url: str, *, ticker: str | None = None) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https":
        raise ValueError("o índice Fundamentus exige HTTPS")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("URL Fundamentus contém credenciais ou porta não permitida")
    if str(parsed.hostname or "").lower() != FUNDAMENTUS_HOST:
        raise ValueError("URL fora do host Fundamentus permitido")
    if parsed.path != FUNDAMENTUS_PATH:
        raise ValueError("caminho Fundamentus não permitido")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"papel"} or len(query["papel"]) != 1:
        raise ValueError("consulta Fundamentus inválida")
    page_ticker = normalize_ticker(query["papel"][0])
    if ticker is not None and page_ticker != normalize_ticker(ticker):
        raise ValueError("ticker divergente na URL Fundamentus")
    return fundamentus_reports_url(page_ticker)


def canonicalize_fundosnet_url(url: str) -> tuple[str, int]:
    """Valida o destino oficial e remove parâmetros não canônicos."""
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https":
        raise ValueError("documentos Fundos.NET exigem HTTPS")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("URL Fundos.NET contém credenciais ou porta não permitida")
    if str(parsed.hostname or "").lower() != FUNDOSNET_HOST:
        raise ValueError("documento fora do host Fundos.NET permitido")
    if parsed.path != FUNDOSNET_PATH or parsed.fragment:
        raise ValueError("endpoint Fundos.NET não permitido")
    query = parse_qs(parsed.query, keep_blank_values=True)
    allowed = {"id", "CodigoTipoInstituicao"}
    if not set(query).issubset(allowed) or len(query.get("id", [])) != 1:
        raise ValueError("consulta Fundos.NET inválida")
    raw_id = query["id"][0]
    if not re.fullmatch(r"[1-9][0-9]{0,11}", raw_id):
        raise ValueError("identificador Fundos.NET inválido")
    institution = query.get("CodigoTipoInstituicao")
    if institution is not None and institution != ["1"]:
        raise ValueError("tipo de instituição Fundos.NET inválido")
    document_id = int(raw_id)
    canonical = urlunparse((
        "https", FUNDOSNET_HOST, FUNDOSNET_PATH, "",
        urlencode({"id": document_id}), "",
    ))
    return canonical, document_id


def _reference_month_end(value: str) -> date | None:
    match = _MONTH_RE.search(str(value))
    if not match:
        return None
    month, year = int(match.group(1)), int(match.group(2))
    return date(year, month, monthrange(year, month)[1])


def parse_fundamentus_reports(
    html: bytes | str,
    *,
    ticker: str,
    discovery_url: str | None = None,
    fetched_at: datetime | None = None,
    limit: int = 300,
) -> FundamentusDiscoveryResult:
    """Extrai somente links oficiais de linhas com mês de referência explícito."""
    normalized_ticker = normalize_ticker(ticker)
    page_url = validate_fundamentus_url(
        discovery_url or fundamentus_reports_url(normalized_ticker),
        ticker=normalized_ticker,
    )
    raw = html if isinstance(html, bytes) else str(html).encode("utf-8")
    if not raw:
        raise ValueError("página Fundamentus vazia")
    soup = BeautifulSoup(html, "html.parser")
    found: dict[int, FundamentusReportCandidate] = {}
    rejected = 0
    maximum = max(1, min(int(limit), 1000))
    for row in soup.find_all("tr"):
        row_text = " ".join(row.get_text(" ", strip=True).split())
        reference_date = _reference_month_end(row_text)
        anchors = row.find_all("a", href=True)
        if not anchors:
            continue
        for anchor in anchors:
            href = str(anchor.get("href") or "").strip()
            if "downloadDocumento" not in href:
                continue
            if reference_date is None:
                rejected += 1
                continue
            try:
                canonical, document_id = canonicalize_fundosnet_url(href)
            except ValueError:
                rejected += 1
                continue
            found[document_id] = FundamentusReportCandidate(
                ticker=normalized_ticker,
                document_id=document_id,
                reference_date=reference_date,
                discovery_url=page_url,
                source_url=canonical,
            )
            if len(found) >= maximum:
                break
        if len(found) >= maximum:
            break
    candidates = tuple(sorted(
        found.values(),
        key=lambda item: (item.reference_date, item.document_id),
        reverse=True,
    ))
    return FundamentusDiscoveryResult(
        ticker=normalized_ticker,
        discovery_url=page_url,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        page_sha256=hashlib.sha256(raw).hexdigest(),
        candidates=candidates,
        rejected_links=rejected,
    )


def _fetch_index(
    url: str,
    *,
    timeout: int = 15,
    attempts: int = 2,
    max_bytes: int = MAX_INDEX_BYTES,
    session: requests.Session | None = None,
    retry_delay_seconds: float = 0.5,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[bytes, str]:
    client = session or requests.Session()
    current = validate_fundamentus_url(url)
    last_error: Exception | None = None
    total_attempts = max(1, min(int(attempts), 3))
    for attempt in range(total_attempts):
        try:
            redirects = 0
            while True:
                response = client.get(
                    current,
                    timeout=max(1, min(int(timeout), 60)),
                    stream=True,
                    allow_redirects=False,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml",
                    },
                )
                try:
                    if response.is_redirect or response.is_permanent_redirect:
                        redirects += 1
                        if redirects > MAX_REDIRECTS:
                            raise ValueError("excesso de redirecionamentos no Fundamentus")
                        target = response.headers.get("Location")
                        if not target:
                            raise ValueError("redirecionamento Fundamentus sem destino")
                        current = validate_fundamentus_url(urljoin(current, target))
                        continue
                    response.raise_for_status()
                    content_type = str(response.headers.get("Content-Type") or "").lower()
                    if content_type and not any(
                        value in content_type for value in ("text/html", "application/xhtml+xml")
                    ):
                        raise ValueError("MIME inesperado no índice Fundamentus")
                    declared = response.headers.get("Content-Length")
                    if declared and int(declared) > max_bytes:
                        raise ValueError("índice Fundamentus excede o limite de tamanho")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("índice Fundamentus excedeu o limite de tamanho")
                        chunks.append(chunk)
                    return b"".join(chunks), current
                finally:
                    response.close()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            last_error = exc
            if attempt + 1 < total_attempts:
                sleeper(max(0.0, min(float(retry_delay_seconds), 5.0)))
                continue
            raise
    raise RuntimeError("falha inesperada ao coletar índice Fundamentus") from last_error


def discover_fundamentus_reports(
    ticker: str,
    *,
    timeout: int = 15,
    attempts: int = 2,
    max_links: int = 300,
    session: requests.Session | None = None,
) -> FundamentusDiscoveryResult:
    normalized_ticker = normalize_ticker(ticker)
    url = fundamentus_reports_url(normalized_ticker)
    fetched_at = datetime.now(timezone.utc)
    content, final_url = _fetch_index(
        url, timeout=timeout, attempts=attempts, session=session,
    )
    return parse_fundamentus_reports(
        content,
        ticker=normalized_ticker,
        discovery_url=final_url,
        fetched_at=fetched_at,
        limit=max_links,
    )


def _candidate_rows(result: FundamentusDiscoveryResult) -> list[dict[str, Any]]:
    return [{
        "ticker": item.ticker,
        "kind": item.document_type,
        "natural_key": item.natural_key,
        "reference_date": item.reference_date.isoformat(),
        "observed": result.fetched_at.isoformat(),
        "url": item.source_url,
        "document_id": item.document_id,
        "discovery_url": item.discovery_url,
        "page_sha256": result.page_sha256,
    } for item in result.candidates]


def persist_discovery(
    result: FundamentusDiscoveryResult,
    *,
    engine=None,
    write: bool = False,
) -> dict[str, Any]:
    """Compara ou insere lacunas sem modificar documentos já conhecidos."""
    target = engine or get_pipeline_engine()
    if target is None:
        raise RuntimeError("banco indisponível")
    rows = _candidate_rows(result)
    if not rows:
        return {"discovered": 0, "existing": 0, "new": 0, "inserted": 0}
    encoded = json.dumps(rows, ensure_ascii=False)
    incoming_cte = """
        WITH incoming AS (
            SELECT * FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS i(
                ticker text, kind text, natural_key text, reference_date date,
                observed timestamptz, url text, document_id bigint,
                discovery_url text, page_sha256 text
            )
        )
    """
    with target.connect() as conn:
        identity = conn.execute(text(incoming_cte + """
            SELECT
              (SELECT count(*) FROM incoming i
               JOIN market.fii_documents d
                 ON d.document_type=i.kind AND d.natural_key=i.natural_key
              ) AS existing,
              (SELECT count(*) FROM incoming i
               JOIN market.fii_documents d ON d.source_url=i.url
               WHERE d.ticker IS DISTINCT FROM i.ticker
              ) AS conflicts
        """), {"rows": encoded}).mappings().one()
    existing = int(identity["existing"] or 0)
    conflicts = int(identity["conflicts"] or 0)
    new_count = len(rows) - existing
    identity_verified = existing > 0 and conflicts == 0
    if not write or new_count <= 0:
        return {
            "discovered": len(rows), "existing": existing,
            "new": new_count, "inserted": 0,
            "identity_verified": identity_verified,
            "identity_conflicts": conflicts,
        }
    if conflicts:
        raise ValueError("IDs Fundos.NET colidem com ticker diferente no catálogo local")
    if not existing:
        raise ValueError("página sem sobreposição oficial para validar a identidade do FII")

    inserted: list[dict[str, Any]] = []
    with target.begin() as conn:
        locked = bool(conn.execute(text(
            "SELECT pg_try_advisory_xact_lock(hashtext('fii_fundamentus_discovery'))"
        )).scalar())
        if not locked:
            raise RuntimeError("outro coletor Fundamentus está ativo")
        inserted = [dict(row) for row in conn.execute(text(incoming_cte + """
            INSERT INTO market.fii_documents (
                ticker,document_type,natural_key,reference_date,
                first_observed_at,source_url,processing_status
            )
            SELECT ticker,kind,natural_key,reference_date,observed,url,'pending'
            FROM incoming
            ON CONFLICT (document_type,natural_key) DO NOTHING
            RETURNING id,ticker,natural_key,reference_date,source_url
        """), {"rows": encoded}).mappings().all()]
        by_key = {row["natural_key"]: row for row in rows}
        audit_rows = []
        for document in inserted:
            source = by_key[str(document["natural_key"])]
            audit_rows.append({
                "entity_id": str(document["id"]),
                "payload": {
                    "discovery_role": "third_party_index_only",
                    "discovery_url": source["discovery_url"],
                    "canonical_source": "Fundos.NET",
                    "canonical_source_url": document["source_url"],
                    "fundosnet_document_id": source["document_id"],
                    "ticker": document["ticker"],
                    "reference_date": str(document["reference_date"]),
                    "reference_date_basis": "fundamentus_month_label",
                    "page_sha256": source["page_sha256"],
                    "score_eligible": False,
                },
            })
        if audit_rows:
            conn.execute(text("""
                WITH incoming AS (
                    SELECT * FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS i(
                        entity_id text,payload jsonb
                    )
                )
                INSERT INTO market.fii_audit_events (
                    event_type,entity_type,entity_id,actor_type,actor_id,
                    parser_version,payload_json
                )
                SELECT 'fundamentus_document_discovered','fii_document',entity_id,
                       'service','fii_fundamentus_discovery',:version,payload
                FROM incoming
            """), {
                "rows": json.dumps(audit_rows, ensure_ascii=False),
                "version": DISCOVERY_VERSION,
            })
    return {
        "discovered": len(rows), "existing": existing,
        "new": new_count, "inserted": len(inserted),
        "identity_verified": True, "identity_conflicts": 0,
    }


def select_pilot_tickers(*, engine=None, limit: int = 20) -> list[str]:
    """Escolhe FIIs conhecidos, sem inferir ou cadastrar novos tickers."""
    target = engine or get_pipeline_engine()
    if target is None:
        raise RuntimeError("banco indisponível")
    maximum = max(1, min(int(limit), 20))
    with target.connect() as conn:
        rows = conn.execute(text("""
            SELECT ticker
            FROM market.fii_documents
            WHERE ticker ~ '^[A-Z]{4}[0-9]{2}$'
            GROUP BY ticker
            ORDER BY count(*) DESC,ticker
            LIMIT :limit
        """), {"limit": maximum}).scalars().all()
    return [normalize_ticker(value) for value in rows]

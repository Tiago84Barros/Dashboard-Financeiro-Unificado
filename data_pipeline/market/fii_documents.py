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
import re
import time
from datetime import datetime, timezone

import requests
from sqlalchemy import text

logger = logging.getLogger(__name__)

PARSER_NAME = "fii_public_report"
PARSER_VERSION = "1.0.0"
SCHEMA_VERSION = "fii-evidence-v1"

_METRIC_PATTERNS = {
    "wault_years": re.compile(r"\bWAULT\b[^\d]{0,40}(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:anos?|years?)", re.I),
    "vacancia_fisica": re.compile(r"vac[aâ]ncia\s+f[ií]sica[^\d]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "vacancia_financeira": re.compile(r"vac[aâ]ncia\s+financeira[^\d]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "ltv": re.compile(r"\bLTV\b[^\d]{0,35}(\d{1,3}(?:[.,]\d{1,2})?)\s*%", re.I),
    "duration_anos": re.compile(r"\bduration\b[^\d]{0,35}(\d{1,2}(?:[.,]\d{1,2})?)\s*(?:anos?|years?)", re.I),
}


def _engine():
    from data_pipeline.utils.db_utils import get_pipeline_engine
    return get_pipeline_engine()


def _cache_root() -> Path:
    configured = os.getenv("FII_DOCUMENT_CACHE", "").strip()
    root = Path(configured) if configured else Path("local_staging") / "fii_documents"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _download(url: str, timeout: int = 60) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(
                url, timeout=timeout, allow_redirects=True,
                headers={"User-Agent": "DashboardFinanceiro/1.0 (+fii-document-audit)"})
            response.raise_for_status()
            content = response.content
            mime = str(response.headers.get("Content-Type") or
                       "application/octet-stream").split(";")[0]
            if not content:
                raise ValueError("documento vazio")
            return content, mime
        except (requests.Timeout, requests.ConnectionError,
                requests.exceptions.HTTPError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None and status < 500 and status != 429:
                break
            if attempt < 2:
                time.sleep(2 ** attempt)
    assert last_error is not None
    raise last_error


def _extract_pdf_text(content: bytes, page_limit: int = 120) -> tuple[str, int, str]:
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
    return extracted, pages, method


def _parse_number(raw: str, *, percent: bool = False) -> float:
    value = float(raw.replace(".", "").replace(",", ".") if "," in raw else raw)
    return value / 100.0 if percent else value


def _extract_evidence(text_value: str) -> list[dict]:
    evidence = []
    for metric, pattern in _METRIC_PATTERNS.items():
        for match in list(pattern.finditer(text_value))[:20]:
            raw = match.group(1)
            try:
                normalized = _parse_number(raw, percent=metric in {
                    "vacancia_fisica", "vacancia_financeira", "ltv"})
            except ValueError:
                continue
            start, end = max(match.start() - 100, 0), min(match.end() + 100, len(text_value))
            plausible = (0 <= normalized <= 1 if metric in {
                "vacancia_fisica", "vacancia_financeira", "ltv"} else 0 <= normalized <= 50)
            evidence.append({
                "metric_name": metric, "raw_value": raw,
                "normalized_value": normalized, "unit": "%" if metric in {
                    "vacancia_fisica", "vacancia_financeira", "ltv"} else "anos",
                "page_number": None, "bbox_json": None,
                "evidence_text": text_value[start:end].replace("\x00", " "),
                "confidence": .80 if plausible else .25,
                "validation_status": "pending" if plausible else "rejected",
            })
    return evidence


def _parser_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def process_pending_documents(limit: int = 25) -> dict:
    engine = _engine()
    result = {"selected": 0, "downloaded": 0, "unchanged": 0,
              "extracted": 0, "needs_review": 0, "failed": 0}
    if engine is None:
        return {**result, "failed": -1, "blocker": "banco indisponível"}
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
        docs = conn.execute(text("""
            SELECT d.* FROM market.fii_documents d
            WHERE d.current_version_id IS NULL OR NOT EXISTS (
                SELECT 1 FROM market.fii_extraction_runs r
                WHERE r.document_version_id=d.current_version_id
                  AND r.parser_name=:name AND r.parser_version=:parser
                  AND r.status IN ('passed','needs_review')
            )
            ORDER BY CASE WHEN d.document_type='RELAT GERENCIAL' THEN 0 ELSE 1 END,
                     d.reference_date DESC NULLS LAST, d.id DESC
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
        """), {"limit": max(int(limit), 1), "name": PARSER_NAME,
                 "parser": PARSER_VERSION}).mappings().all()
    result["selected"] = len(docs)
    for doc in docs:
        try:
            content, mime = _download(str(doc["source_url"]))
            sha = hashlib.sha256(content).hexdigest()
            suffix = ".pdf" if content[:4] == b"%PDF" or "pdf" in mime.lower() else ".bin"
            storage_key = f"{sha[:2]}/{sha}{suffix}"
            target = _cache_root() / storage_key
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(content)
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
                        ) VALUES (:doc, :revision, :sha, 'local_cache', :key, :mime, :size, :previous)
                        RETURNING id
                    """), {"doc": doc["id"], "revision": int(previous["revision_no"]) + 1 if previous else 1,
                             "sha": sha, "key": storage_key, "mime": mime,
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
                continue
            if suffix != ".pdf":
                raise ValueError(f"tipo documental não suportado: {mime}")
            extracted, pages, method = _extract_pdf_text(content)
            signature = hashlib.sha256(re.sub(r"\s+", " ", extracted[:6000]).encode("utf-8")).hexdigest()
            evidence = _extract_evidence(extracted)
            confidence = min(1.0, len(extracted) / max(pages * 800, 1))
            status = "needs_review" if evidence else "passed"
            with engine.begin() as conn:
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
                                                "evidence_count": len(evidence)})}).scalar()
                conn.execute(text("UPDATE market.fii_document_versions SET page_count=:pages WHERE id=:id"),
                             {"pages": pages, "id": version_id})
                for row in evidence:
                    conn.execute(text("""
                        INSERT INTO market.fii_extraction_evidence (
                            extraction_run_id, metric_name, raw_value, normalized_value,
                            unit, page_number, bbox_json, evidence_text, confidence,
                            validation_status
                        ) VALUES (:run, :metric, :raw, CAST(:normalized AS jsonb), :unit,
                                  :page, CAST(:bbox AS jsonb), :evidence, :confidence, :status)
                    """), {"run": run_id, "metric": row["metric_name"], "raw": row["raw_value"],
                             "normalized": json.dumps(row["normalized_value"]), "unit": row["unit"],
                             "page": row["page_number"], "bbox": json.dumps(row["bbox_json"]),
                             "evidence": row["evidence_text"], "confidence": row["confidence"],
                             "status": row["validation_status"]})
            result["extracted"] += 1
            result["needs_review"] += int(bool(evidence))
        except Exception as exc:
            logger.warning("Documento FII %s falhou: %s", doc.get("id"), exc)
            try:
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO market.fii_audit_events (
                            event_type, entity_type, entity_id, parser_version, payload_json
                        ) VALUES ('document_download_failed', 'fii_document', :id, :parser,
                                  CAST(:payload AS jsonb))
                    """), {"id": str(doc.get("id")), "parser": PARSER_VERSION,
                             "payload": json.dumps({"error_type": type(exc).__name__,
                                                    "message": str(exc)[:500]})})
            except Exception:
                logger.debug("Falha ao auditar erro documental", exc_info=True)
            result["failed"] += 1
    return result

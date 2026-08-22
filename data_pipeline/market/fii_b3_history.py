"""Histórico oficial B3 (COTAHIST) para universo sem viés de sobrevivência.

Os arquivos não são ajustados por proventos. Eles servem como security master,
preço negociado e evidência de que o ticker existia na data; o backtest combina
retornos com proventos separadamente quando disponíveis e reporta a cobertura.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from data_pipeline.market.repository import save_raw_payload
from data_pipeline.utils.db_utils import get_pipeline_engine

URLS = (
    "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{year}.ZIP",
    "https://www.b3.com.br/pesquisapregao/download?filelist=COTAHIST_A{year}.ZIP",
)
CACHE_ROOT = Path("local_staging/fii_b3_cotahist")
SOURCE = "b3_cotahist"
PARSER_NAME = "b3_cotahist_fixed_width"
PARSER_VERSION = "1.1.0"
PARSER_SCHEMA_VERSION = "cotahist-layout-2020-r2"
_LOGGER = logging.getLogger(__name__)
_BATCH_SIZE = 1_000


def _money(line: str, start: int, end: int) -> float | None:
    raw = line[start:end].strip()
    try:
        return int(raw) / 100.0 if raw else None
    except ValueError:
        return None


def _integer(line: str, start: int, end: int) -> int | None:
    raw = line[start:end].strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def fetch_year(year: int, timeout: int = 180) -> tuple[bytes, str, dict[str, str]]:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    cache = CACHE_ROOT / f"COTAHIST_A{year}.ZIP"
    session = requests.Session()
    session.headers["User-Agent"] = "DashboardFinanceiro/1.0 (+b3-cotahist)"
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), respect_retry_after_header=True,
    )))
    errors: list[str] = []
    for template in URLS:
        url = template.format(year=int(year))
        try:
            response = session.get(url, timeout=timeout)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            content = response.content
            if not content.startswith(b"PK"):
                errors.append(f"{url}: resposta não ZIP")
                continue
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(content)
            headers = {key: str(value) for key, value in response.headers.items()
                       if key.lower() in {"etag", "last-modified", "content-length"}}
            return content, url, headers
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
    if cache.exists():
        return cache.read_bytes(), URLS[0].format(year=int(year)), {"cache-fallback": "true"}
    raise RuntimeError("; ".join(errors) or f"COTAHIST {year} indisponível")


def parse_cotahist(content: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(content)) as zipped:
        names = [name for name in zipped.namelist() if name.upper().endswith(".TXT")]
        if not names:
            return []
        raw_lines = zipped.read(names[0]).decode("latin-1").splitlines()
    rows: list[dict] = []
    for line in raw_lines:
        if len(line) < 242 or line[0:2] != "01":
            continue
        bdi, market = line[10:12], line[24:27]
        ticker = line[12:24].strip().upper()
        # BDI 12 identifica cotas de fundos imobiliários no arquivo oficial.
        if bdi != "12" or market != "010" or not ticker:
            continue
        raw_date = line[2:10]
        try:
            trade_date = datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
        except ValueError:
            continue
        rows.append({
            "ticker": ticker, "trade_date": trade_date,
            "issuer_short_name": line[27:39].strip() or None,
            "specification": line[39:49].strip() or None,
            "isin": line[230:242].strip() or None,
            "open": _money(line, 56, 69), "high": _money(line, 69, 82),
            "low": _money(line, 82, 95), "average": _money(line, 95, 108),
            "close": _money(line, 108, 121),
            "trades": _integer(line, 147, 152), "quantity": _integer(line, 152, 170),
            "financial_volume": _money(line, 170, 188),
        })
    return rows


def _parser_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _register_parser(conn) -> None:
    conn.execute(text("""
        INSERT INTO market.fii_parser_versions (
            parser_name,parser_version,schema_version,code_sha256,status,activated_at
        ) VALUES (:name,:version,:schema,:sha,'active',now())
        ON CONFLICT (parser_name,parser_version) DO UPDATE SET
            schema_version=EXCLUDED.schema_version,code_sha256=EXCLUDED.code_sha256,
            status='active',activated_at=now()
    """), {"name": PARSER_NAME, "version": PARSER_VERSION,
             "schema": PARSER_SCHEMA_VERSION, "sha": _parser_hash()})


def ingest_b3_history(*, years: int = 10) -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "errors": ["banco indisponível"]}
    current = datetime.now(timezone.utc).year
    report = {"status": "completed", "archives": 0, "skipped": 0,
              "rows": 0, "tickers": set(), "errors": []}
    with engine.begin() as conn:
        columns = {row[0] for row in conn.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='market' AND table_name='fii_b3_archive_loads'
        """))}
        if not {"parser_name", "parser_version"}.issubset(columns):
            return {**report, "status": "failed",
                    "errors": ["migration 037 pendente"], "tickers": 0}
        _register_parser(conn)
    for year in range(current - max(int(years), 1) + 1, current + 1):
        sha: str | None = None
        try:
            content, url, headers = fetch_year(year)
            rows = parse_cotahist(content)
            collected = datetime.now(timezone.utc)
            sha = hashlib.sha256(content).hexdigest()
            with engine.connect() as conn:
                completed = conn.execute(text("""
                    SELECT status='completed'
                    FROM market.fii_b3_archive_loads
                    WHERE archive_year=:year AND archive_sha256=:sha
                      AND parser_name=:parser AND parser_version=:version
                """), {"year": year, "sha": sha, "parser": PARSER_NAME,
                         "version": PARSER_VERSION}).scalar()
            if completed:
                report["archives"] += 1
                report["skipped"] += 1
                report["rows"] += len(rows)
                report["tickers"].update(row["ticker"] for row in rows)
                _LOGGER.info("COTAHIST %s — arquivo já concluído, ignorado", year)
                continue
            with engine.begin() as conn:
                raw_id = save_raw_payload(
                    conn, None, "b3/cotahist", {"year": year, "sha256": sha, "rows": len(rows)},
                    request_params={"year": year}, response_headers=headers,
                    collected_at=collected, source=SOURCE,
                    request_fingerprint=hashlib.sha256(url.encode()).hexdigest(),
                )
                conn.execute(text("""
                    INSERT INTO market.fii_b3_archive_loads (
                        archive_year,archive_sha256,source_url,expected_rows,loaded_rows,
                        status,raw_payload_id,started_at,updated_at,error_message,
                        parser_name,parser_version
                    ) VALUES (:year,:sha,:url,:expected,0,'running',:raw,now(),now(),NULL,
                              :parser,:version)
                    ON CONFLICT (archive_year,archive_sha256,parser_name,parser_version)
                    DO UPDATE SET
                        source_url=EXCLUDED.source_url,
                        expected_rows=EXCLUDED.expected_rows,
                        status='running',raw_payload_id=EXCLUDED.raw_payload_id,
                        updated_at=now(),error_message=NULL
                """), {"year": year, "sha": sha, "url": url,
                        "expected": sum(1 for row in rows
                                        if row.get("close") and row["close"] > 0),
                        "raw": raw_id, "parser": PARSER_NAME,
                        "version": PARSER_VERSION})
            payload = [{**row, "source_url": url, "raw_payload_id": raw_id,
                        "collected_at": collected.isoformat(), "archive_sha256": sha}
                       for row in rows if row.get("close") and row["close"] > 0]
            for offset in range(0, len(payload), _BATCH_SIZE):
                batch = payload[offset:offset + _BATCH_SIZE]
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO market.fii_b3_security_history (
                            ticker,trade_date,issuer_short_name,specification,isin,open,high,low,
                            average,close,trades,quantity,financial_volume,source,source_url,
                            raw_payload_id,collected_at,archive_sha256
                        ) SELECT ticker,trade_date,issuer_short_name,specification,isin,open,high,low,
                            average,close,trades,quantity,financial_volume,:source,source_url,
                            raw_payload_id,collected_at,archive_sha256
                        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
                            ticker text,trade_date date,issuer_short_name text,specification text,
                            isin text,open numeric,high numeric,low numeric,average numeric,
                            close numeric,trades integer,quantity bigint,financial_volume numeric,
                            source_url text,raw_payload_id bigint,collected_at timestamptz,
                            archive_sha256 text
                        ) ON CONFLICT (ticker,trade_date,archive_sha256) DO NOTHING
                    """), {"source": SOURCE, "rows": json.dumps(batch, ensure_ascii=False)})
                _LOGGER.info("COTAHIST %s — %s/%s linhas persistidas",
                             year, min(offset + len(batch), len(payload)), len(payload))
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE market.fii_b3_archive_loads
                        SET loaded_rows=:loaded,updated_at=now()
                        WHERE archive_year=:year AND archive_sha256=:sha
                          AND parser_name=:parser AND parser_version=:version
                    """), {"loaded": min(offset + len(batch), len(payload)),
                            "year": year, "sha": sha, "parser": PARSER_NAME,
                            "version": PARSER_VERSION})
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE market.fii_b3_archive_loads
                    SET loaded_rows=:loaded,status='completed',updated_at=now(),completed_at=now()
                    WHERE archive_year=:year AND archive_sha256=:sha
                      AND parser_name=:parser AND parser_version=:version
                """), {"loaded": len(payload), "year": year, "sha": sha,
                         "parser": PARSER_NAME, "version": PARSER_VERSION})
            report["archives"] += 1
            report["rows"] += len(rows)
            report["tickers"].update(row["ticker"] for row in rows)
        except Exception as exc:
            _LOGGER.exception("Falha no COTAHIST %s", year)
            if sha:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("""
                            UPDATE market.fii_b3_archive_loads
                            SET status='failed',updated_at=now(),error_message=:error
                            WHERE archive_year=:year AND archive_sha256=:sha
                              AND parser_name=:parser AND parser_version=:version
                        """), {"year": year, "sha": sha,
                                 "parser": PARSER_NAME, "version": PARSER_VERSION,
                                 "error": str(exc)[:500]})
                except Exception:
                    pass
            report["errors"].append({"year": year, "error": str(exc)[:500]})
    report["tickers"] = len(report["tickers"])
    if report["archives"] == 0:
        report["status"] = "failed"
    elif report["errors"]:
        report["status"] = "partial"
    return report

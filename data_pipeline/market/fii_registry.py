"""Cadastro regulatório de FIIs e classes, incluindo fundos cancelados.

Fonte pública: cadastro de fundos/classes da CVM. O arquivo é versionado por
hash e nunca substitui observações anteriores. Datas de registro/cancelamento
são fatos regulatórios; ``collected_at`` registra quando esta versão foi vista.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

import pandas as pd
from sqlalchemy import text

from data_pipeline.market.repository import save_raw_payload
from data_pipeline.utils.db_utils import get_pipeline_engine


URL = "https://dados.cvm.gov.br/dados/FI/CAD/DADOS/registro_fundo_classe.zip"
CACHE = Path("local_staging/fii_cvm_registry/registro_fundo_classe.zip")
SOURCE = "cvm_registro_fundo_classe"


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _date(value) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = pd.to_datetime(raw, errors="coerce",
                            dayfirst=not bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw)))
    return parsed.date().isoformat() if pd.notna(parsed) else None


def fetch_registry(timeout: int = 120) -> tuple[bytes, dict[str, str], datetime]:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    session.headers["User-Agent"] = "DashboardFinanceiro/1.0 (+cvm-registry-pit)"
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=.8, status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}), respect_retry_after_header=True,
    )))
    try:
        response = session.get(URL, timeout=timeout)
        response.raise_for_status()
        content = response.content
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(content)
        headers = {key: str(value) for key, value in response.headers.items()
                   if key.lower() in {"etag", "last-modified", "content-length"}}
    except requests.RequestException:
        if not CACHE.exists():
            raise
        content, headers = CACHE.read_bytes(), {"cache-fallback": "true"}
    return content, headers, datetime.now(timezone.utc)


def parse_registry(content: bytes) -> list[dict]:
    frames: dict[str, pd.DataFrame] = {}
    with zipfile.ZipFile(io.BytesIO(content)) as zipped:
        for name in zipped.namelist():
            lower = name.lower()
            if not lower.endswith(".csv"):
                continue
            with zipped.open(name) as stream:
                frames[Path(name).stem.lower()] = pd.read_csv(
                    stream, sep=";", encoding="latin-1", dtype=str,
                    keep_default_na=False, low_memory=False,
                )
    funds = frames.get("registro_fundo", pd.DataFrame())
    classes = frames.get("registro_classe", pd.DataFrame())
    if funds.empty:
        return []
    fund_by_id = {str(row.get("ID_Registro_Fundo") or ""): row
                  for row in funds.to_dict("records")}
    rows: list[dict] = []
    sources = classes.to_dict("records") if not classes.empty else funds.to_dict("records")
    for item in sources:
        parent = fund_by_id.get(str(item.get("ID_Registro_Fundo") or ""), {})
        combined = {**parent, **item}
        type_text = " ".join(str(combined.get(key) or "") for key in
                             ("Tipo_Fundo", "Tipo_Classe", "Classificacao")).upper()
        if "FII" not in type_text and "IMOBILI" not in type_text:
            continue
        cnpj = _digits(combined.get("CNPJ_Classe") or combined.get("CNPJ_Fundo"))
        if len(cnpj) != 14:
            continue
        rows.append({
            "cnpj": cnpj,
            "cvm_code": str(combined.get("Codigo_CVM") or "").strip() or None,
            "fund_registry_id": str(combined.get("ID_Registro_Fundo") or "").strip() or None,
            "class_registry_id": str(combined.get("ID_Registro_Classe") or "").strip() or None,
            "legal_name": str(combined.get("Denominacao_Social") or "").strip() or None,
            "fund_type": str(combined.get("Tipo_Fundo") or combined.get("Tipo_Classe") or "").strip() or None,
            "classification": str(combined.get("Classificacao") or "").strip() or None,
            "status": str(combined.get("Situacao") or "").strip() or "NAO_INFORMADA",
            "registration_date": _date(combined.get("Data_Registro")),
            "constitution_date": _date(combined.get("Data_Constituicao")),
            "start_date": _date(combined.get("Data_Inicio") or combined.get("Data_Registro")),
            "cancellation_date": _date(combined.get("Data_Cancelamento")),
            "manager_identifier": _digits(combined.get("CPF_CNPJ_Gestor")) or None,
            "manager_name": str(combined.get("Gestor") or "").strip() or None,
            "administrator_identifier": _digits(combined.get("CNPJ_Administrador")) or None,
            "administrator_name": str(combined.get("Administrador") or "").strip() or None,
            "raw_json": combined,
        })
    return rows


def ingest_registry() -> dict:
    engine = get_pipeline_engine()
    if engine is None:
        return {"status": "failed", "error": "banco indisponível"}
    content, headers, collected_at = fetch_registry()
    sha = hashlib.sha256(content).hexdigest()
    parsed = parse_registry(content)
    with engine.begin() as conn:
        raw_id = save_raw_payload(
            conn, None, "cvm/registro_fundo_classe", {"sha256": sha, "rows": len(parsed)},
            request_params={}, response_headers=headers, collected_at=collected_at,
            source=SOURCE, request_fingerprint=hashlib.sha256(URL.encode()).hexdigest(),
        )
        ticker_map = {_digits(cnpj): ticker for ticker, cnpj in conn.execute(text(
            "SELECT ticker, cnpj FROM market.fiis WHERE cnpj IS NOT NULL"
        )).fetchall() if _digits(cnpj)}
        payload = []
        for row in parsed:
            semantic = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            payload.append({
                **row, "ticker": ticker_map.get(row["cnpj"]),
                "reference_date": collected_at.date().isoformat(),
                "collected_at": collected_at.isoformat(), "raw_payload_id": raw_id,
                "content_hash": hashlib.sha256(semantic.encode()).hexdigest(),
                "raw_json": json.dumps(row["raw_json"], ensure_ascii=False),
            })
        if payload:
            conn.execute(text("""
                INSERT INTO market.fii_registry_observations (
                    cnpj,cvm_code,fund_registry_id,class_registry_id,ticker,legal_name,
                    fund_type,classification,status,registration_date,constitution_date,
                    start_date,cancellation_date,manager_identifier,manager_name,
                    administrator_identifier,administrator_name,reference_date,collected_at,
                    source,raw_payload_id,content_hash,raw_json
                ) SELECT cnpj,cvm_code,fund_registry_id,class_registry_id,ticker,legal_name,
                    fund_type,classification,status,registration_date,constitution_date,
                    start_date,cancellation_date,manager_identifier,manager_name,
                    administrator_identifier,administrator_name,reference_date,collected_at,
                    :source,raw_payload_id,content_hash,raw_json
                FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(
                    cnpj text,cvm_code text,fund_registry_id text,class_registry_id text,
                    ticker text,legal_name text,fund_type text,classification text,status text,
                    registration_date date,constitution_date date,start_date date,
                    cancellation_date date,manager_identifier text,manager_name text,
                    administrator_identifier text,administrator_name text,reference_date date,
                    collected_at timestamptz,raw_payload_id bigint,content_hash text,raw_json jsonb
                ) ON CONFLICT (cnpj, reference_date, content_hash) DO NOTHING
            """), {"source": SOURCE, "rows": json.dumps(payload, ensure_ascii=False)})
    return {"status": "completed", "rows": len(parsed),
            "linked_tickers": sum(bool(row.get("ticker")) for row in payload), "sha256": sha}

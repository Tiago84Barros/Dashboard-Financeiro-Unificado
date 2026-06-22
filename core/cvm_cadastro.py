"""
core/cvm_cadastro.py
Cadastro oficial da CVM para o mapa ticker -> codigo_cvm e metadados de empresa.

Fontes (CVM Dados Abertos, públicas, sem auth):
  • cad_cia_aberta.csv .................. CNPJ -> CD_CVM, nome, setor, situação
  • FCA valor_mobiliario (zip anual) ... CNPJ -> Codigo_Negociacao (ticker)

A junção é por CNPJ: ticker (FCA) ⟶ CNPJ ⟶ codigo_cvm + metadados (cad).

Parsers PUROS e testáveis; IO de download isolado.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile

logger = logging.getLogger(__name__)

CAD_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"
FCA_ZIP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{year}.zip"

_TICKER_RE = re.compile(r"^[A-Z]{4}\d{1,2}$")


def cnpj_digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _decode(content: bytes) -> str | None:
    for enc in ("latin-1", "utf-8-sig", "utf-8"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _rows(content: bytes):
    text = _decode(content)
    if text is None:
        return []
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def parse_cad(content: bytes) -> dict[str, dict]:
    """Retorna {cnpj_digits: {codigo_cvm, name, sector, situacao, cnpj}}."""
    out: dict[str, dict] = {}
    for r in _rows(content):
        cd = r.get("CD_CVM")
        cnpj = cnpj_digits(r.get("CNPJ_CIA"))
        if not cnpj or not cd:
            continue
        try:
            cod = int(str(cd).strip())
        except ValueError:
            continue
        out[cnpj] = {
            "codigo_cvm": cod,
            "name": (r.get("DENOM_COMERC") or r.get("DENOM_SOCIAL") or "").strip(),
            "sector": (r.get("SETOR_ATIV") or "").strip() or None,
            "situacao": (r.get("SIT") or "").strip() or None,
            "cnpj": (r.get("CNPJ_CIA") or "").strip() or None,
        }
    return out


def parse_fca_valmob(content: bytes) -> list[dict]:
    """Retorna [{cnpj_digits, ticker, mercado, segmento}] só de ações/units na bolsa."""
    out: list[dict] = []
    for r in _rows(content):
        ticker = (r.get("Codigo_Negociacao") or "").strip().upper()
        if not _TICKER_RE.match(ticker):
            continue
        vm = (r.get("Valor_Mobiliario") or "").lower()
        if not ("ações" in vm or "acoes" in vm or "unit" in vm or "certificado de depósito de ações" in vm):
            continue
        out.append({
            "cnpj": cnpj_digits(r.get("CNPJ_Companhia")),
            "ticker": ticker,
            "mercado": (r.get("Mercado") or "").strip(),
            "segmento": (r.get("Segmento") or "").strip() or None,
        })
    return out


def build_map(cad: dict[str, dict], fca: list[dict]) -> tuple[dict[str, int], dict[int, dict]]:
    """
    Junta por CNPJ. Retorna:
      ticker_to_cod: {ticker: codigo_cvm}
      companies:     {codigo_cvm: {codigo_cvm, name, sector, cnpj, segment}}
    """
    ticker_to_cod: dict[str, int] = {}
    companies: dict[int, dict] = {}
    for row in fca:
        meta = cad.get(row["cnpj"])
        if not meta:
            continue
        cod = meta["codigo_cvm"]
        ticker_to_cod[row["ticker"]] = cod
        companies.setdefault(cod, {
            "codigo_cvm": cod,
            "name": (meta.get("name") or row["ticker"])[:300],
            "cnpj": meta.get("cnpj"),
            "sector": meta.get("sector"),
            "segment": row.get("segmento"),
        })
    return ticker_to_cod, companies


# ── IO ────────────────────────────────────────────────────────────────────────

def fetch_cad(timeout: int = 120) -> bytes | None:
    import requests
    try:
        r = requests.get(CAD_URL, headers={"User-Agent": "DashboardFinanceiro/1.0"}, timeout=timeout)
        return r.content if r.status_code == 200 else None
    except Exception as exc:
        logger.warning("fetch_cad: %s", exc)
        return None


def fetch_fca_valmob(year: int, timeout: int = 120) -> bytes | None:
    """Baixa o zip anual do FCA e extrai o CSV valor_mobiliario."""
    import requests
    try:
        r = requests.get(FCA_ZIP.format(year=year),
                        headers={"User-Agent": "DashboardFinanceiro/1.0"}, timeout=timeout)
        if r.status_code != 200:
            return None
        z = zipfile.ZipFile(io.BytesIO(r.content))
        for n in z.namelist():
            if "valor_mobiliario" in n:
                return z.read(n)
    except Exception as exc:
        logger.warning("fetch_fca_valmob %s: %s", year, exc)
    return None

"""
core/cvm_ipe.py
Coletor nativo de documentos CVM/IPE para o app4 (substitui o sync do app1).

Fonte primária: CVM Dados Abertos — dataset IPE (Informações Periódicas e
Eventuais), em lote (CSV anual), sem raspar página a página:
  https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_<ano>.csv

Cada linha do IPE traz metadados de um documento (empresa, categoria, tipo,
assunto, data, link de download no ENET). Mapeamos `Codigo_CVM` → ticker usando
o que já existe em `docs_corporativos`, mantendo só empresas do nosso universo.

Funções puras (parse/filtro/chunk/hash) testáveis sem rede; IO de download
isolado em `fetch_ipe_csv`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

IPE_BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS"
SOURCE_NAME = "CVM/IPE"

# Categorias IPE relevantes (match por substring, case-insensitive) — foco em
# informação sensível a preço. Configurável pelo job.
RELEVANT_CATEGORIES = (
    "fato relevante",
    "comunicado ao mercado",
    "dados econômico-financeiros",
    "dados economico-financeiros",
    "press-release",
    "press release",
    "aviso aos acionistas",
    "assembleia",
    "calendário de eventos",
    "calendario de eventos",
    "reunião da administração",
    "reuniao da administracao",
    "política de",
    "politica de",
)

_HEADER = "User-Agent: Mozilla/5.0 (compatible; DashboardFinanceiro/1.0)"


def ipe_csv_url(year: int) -> str:
    return f"{IPE_BASE}/ipe_cia_aberta_{int(year)}.csv"


def _to_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _to_date(v) -> date | None:
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_ipe_csv(content: bytes) -> list[dict]:
    """
    Converte o CSV do IPE (;-separado) numa lista de dicts normalizados.
    Tolera latin-1/utf-8 e nomes de coluna em qualquer ordem.
    """
    if not content:
        return []
    text = None
    for enc in ("latin-1", "utf-8-sig", "utf-8"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return []

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    out: list[dict] = []
    for row in reader:
        norm = {(k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
                for k, v in row.items()}
        out.append({
            "codigo_cvm": _to_int(norm.get("codigo_cvm")),
            "nome": norm.get("nome_companhia") or "",
            "categoria": norm.get("categoria") or "",
            "tipo": norm.get("tipo") or "",
            "especie": norm.get("especie") or "",
            "assunto": norm.get("assunto") or "",
            "modalidade": (norm.get("modalidade") or "").upper(),
            "versao": norm.get("versao") or "",
            "data_referencia": _to_date(norm.get("data_referencia")),
            "data_entrega": _to_date(norm.get("data_entrega")),
            "url": norm.get("link_download") or "",
        })
    return out


def is_relevant_category(categoria: str, categorias: tuple[str, ...] | None = None) -> bool:
    cat = (categoria or "").lower()
    for c in (categorias or RELEVANT_CATEGORIES):
        if c in cat:
            return True
    return False


def filter_docs(
    rows: list[dict],
    codigo_to_ticker: dict[int, str],
    categorias: tuple[str, ...] | None = None,
) -> list[dict]:
    """
    Mantém apenas documentos de empresas do universo (codigo_cvm mapeado a ticker),
    de categorias relevantes e não cancelados. Anexa o ticker resolvido.
    """
    out: list[dict] = []
    for r in rows:
        cod = r.get("codigo_cvm")
        tk = codigo_to_ticker.get(cod) if cod is not None else None
        if not tk:
            continue
        if r.get("modalidade") == "CA":  # documento cancelado
            continue
        if not is_relevant_category(r.get("categoria", ""), categorias):
            continue
        if not r.get("url"):
            continue
        out.append({**r, "ticker": tk})
    return out


def metadata_text(doc: dict) -> str:
    """Texto-resumo do documento (usado como chunk mínimo, RAG-visível)."""
    partes = [
        f"Empresa: {doc.get('ticker','')} ({doc.get('nome','')})",
        f"Categoria: {doc.get('categoria','')}",
    ]
    if doc.get("tipo"):
        partes.append(f"Tipo: {doc['tipo']}")
    if doc.get("especie"):
        partes.append(f"Espécie: {doc['especie']}")
    if doc.get("assunto"):
        partes.append(f"Assunto: {doc['assunto']}")
    dt = doc.get("data_entrega") or doc.get("data_referencia")
    if dt:
        partes.append(f"Data: {dt}")
    return " | ".join(partes)


def sha256(*parts) -> str:
    h = hashlib.sha256()
    h.update("||".join(str(p) for p in parts).encode("utf-8"))
    return h.hexdigest()


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Divide texto longo em pedaços com sobreposição. Texto curto → 1 chunk."""
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= size:
        return [t]
    chunks: list[str] = []
    start = 0
    step = max(1, size - overlap)
    while start < len(t):
        chunks.append(t[start:start + size])
        start += step
    return chunks


# ── IO (rede) ─────────────────────────────────────────────────────────────────

def fetch_ipe_csv(year: int, timeout: int = 60) -> bytes | None:
    """Baixa o CSV anual do IPE. Retorna None em falha (sem levantar)."""
    try:
        import requests
        url = ipe_csv_url(year)
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "DashboardFinanceiro/1.0 (+data-quality)"})
        if resp.status_code == 200 and resp.content:
            return resp.content
        logger.warning("fetch_ipe_csv %s: HTTP %s", year, resp.status_code)
    except Exception as exc:
        logger.warning("fetch_ipe_csv %s falhou: %s", year, exc)
    return None

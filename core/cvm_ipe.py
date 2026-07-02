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
# informação sensível a preço e fundamento. Ficam DE FORA (alto volume, baixo
# valor narrativo): posições art.11 ("Valores Mobiliários negociados e detidos"),
# Reunião da Administração, Calendário, Estatuto, Regimentos e Políticas de
# governança. Assembleias (AGO/AGE) ENTRAM — aprovam dividendos, aumento de
# capital, M&A e eleição da administração. Configurável pelo job via `categorias`.
RELEVANT_CATEGORIES = (
    # Eventos materiais / sensíveis a preço
    "fato relevante",
    "comunicado ao mercado",
    # Resultados e dados financeiros
    "dados econômico-financeiros",
    "dados economico-financeiros",
    "press-release",
    "press release",
    # Remuneração ao acionista (proventos)
    "aviso aos acionistas",
    "proventos",                       # "Relatório Proventos"
    # Assembleias — deliberações materiais (dividendos, capital, M&A, governança)
    "assembleia",
    # Estrutura de capital / dívida
    "oferta de distribuição",
    "oferta de distribuicao",
    "debêntures",                      # "Escrituras e aditamentos de debêntures"
    "debentures",
    # Eventos críticos / controle
    "recuperação judicial",
    "recuperacao judicial",
    "oferta pública de aç",            # OPA — "OPA - Edital de Oferta Pública de Ações"
    "oferta publica de aç",
)

_HEADER = "User-Agent: Mozilla/5.0 (compatible; DashboardFinanceiro/1.0)"


def ipe_csv_url(year: int) -> str:
    return f"{IPE_BASE}/ipe_cia_aberta_{int(year)}.csv"


def ipe_zip_url(year: int) -> str:
    return f"{IPE_BASE}/ipe_cia_aberta_{int(year)}.zip"


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


# ── Extração de texto completo (PDF / HTML / ZIP) ─────────────────────────────

class DocFetchError(Exception):
    """Falha ao baixar um documento do ENET."""


class RateLimited(DocFetchError):
    """Servidor sinalizou bloqueio/limite (HTTP 429/503) — aciona disjuntor."""


def is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, RateLimited)


def _clean_text(t: str) -> str:
    import re
    t = re.sub(r"[ \t ]+", " ", t or "")
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _pdf_text(content: bytes) -> str:
    import io as _io
    # 1) pypdf — rápido (2–5x vs pdfplumber) e qualidade equivalente para PDFs de
    #    texto (Fato Relevante, resultados). É o extrator primário.
    try:
        import pypdf
        reader = pypdf.PdfReader(_io.BytesIO(content))
        txt = "\n".join((p.extract_text() or "") for p in reader.pages[:60])
        if len(txt.strip()) >= 200:
            return _clean_text(txt)
    except Exception as exc:
        logger.debug("pypdf falhou (%s) — tentando pdfplumber", exc)
    # 2) pdfplumber — reserva (melhor em layouts complexos/tabelas ou PDFs que o
    #    pypdf não lê).
    try:
        import pdfplumber
        out = []
        with pdfplumber.open(_io.BytesIO(content)) as pdf:
            for page in pdf.pages[:60]:  # teto de páginas por documento
                out.append(page.extract_text() or "")
        return _clean_text("\n".join(out))
    except Exception as exc:
        logger.warning("pdf extract falhou: %s", exc)
        return ""


def _html_text(content: bytes) -> str:
    try:
        from bs4 import BeautifulSoup
        for enc in ("utf-8", "latin-1"):
            try:
                html = content.decode(enc)
                break
            except UnicodeDecodeError:
                html = None
        if html is None:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return _clean_text(soup.get_text(" "))
    except Exception as exc:
        logger.warning("html extract falhou: %s", exc)
        return ""


def _zip_text(content: bytes) -> str:
    try:
        import io as _io
        import zipfile
        parts = []
        with zipfile.ZipFile(_io.BytesIO(content)) as zf:
            for name in zf.namelist()[:20]:
                low = name.lower()
                try:
                    data = zf.read(name)
                except Exception:
                    continue
                if low.endswith(".pdf") or data[:4] == b"%PDF":
                    parts.append(_pdf_text(data))
                elif low.endswith((".htm", ".html", ".txt", ".xml")):
                    parts.append(_html_text(data))
        return _clean_text("\n".join(p for p in parts if p))
    except Exception as exc:
        logger.warning("zip extract falhou: %s", exc)
        return ""


def extract_text(content: bytes) -> str:
    """Extrai texto de um documento (detecta PDF, ZIP ou HTML)."""
    if not content:
        return ""
    if content[:4] == b"%PDF":
        return _pdf_text(content)
    if content[:2] == b"PK":
        return _zip_text(content)
    return _html_text(content)


def fetch_document(url: str, timeout: int = 45) -> bytes:
    """
    Baixa um documento do ENET. Levanta RateLimited em 429/503 (para o disjuntor)
    e DocFetchError em outras falhas. Nunca retorna vazio silenciosamente.
    """
    import requests
    try:
        resp = requests.get(url, timeout=timeout,
                            headers={"User-Agent": "DashboardFinanceiro/1.0 (+data-quality)"})
    except Exception as exc:
        raise DocFetchError(str(exc)) from exc
    if resp.status_code in (429, 503):
        raise RateLimited(f"HTTP {resp.status_code}")
    if resp.status_code != 200 or not resp.content:
        raise DocFetchError(f"HTTP {resp.status_code}")
    return resp.content


# ── IO (rede) ─────────────────────────────────────────────────────────────────

def fetch_ipe_csv(year: int, timeout: int = 60) -> bytes | None:
    """
    Baixa o dataset anual do IPE e retorna os BYTES do CSV. Retorna None em falha.

    A CVM passou a distribuir o arquivo como .zip (contendo o .csv); o .csv direto
    agora dá 404. Tenta o .zip primeiro (extrai o CSV interno) e cai no .csv legado
    como reserva, mantendo compatibilidade caso a CVM volte ao formato antigo.
    """
    import requests
    headers = {"User-Agent": "DashboardFinanceiro/1.0 (+data-quality)"}

    # 1) Formato atual: ZIP contendo o CSV.
    try:
        resp = requests.get(ipe_zip_url(year), timeout=timeout, headers=headers)
        if resp.status_code == 200 and resp.content:
            import io as _io
            import zipfile
            try:
                zf = zipfile.ZipFile(_io.BytesIO(resp.content))
                name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
                if name:
                    return zf.read(name)
                logger.warning("fetch_ipe_csv %s: zip sem .csv interno", year)
            except zipfile.BadZipFile:
                return resp.content  # servido como .zip mas já é o CSV cru
        else:
            logger.warning("fetch_ipe_csv zip %s: HTTP %s", year, resp.status_code)
    except Exception as exc:
        logger.warning("fetch_ipe_csv zip %s falhou: %s", year, exc)

    # 2) Legado: CSV direto.
    try:
        resp = requests.get(ipe_csv_url(year), timeout=timeout, headers=headers)
        if resp.status_code == 200 and resp.content:
            return resp.content
        logger.warning("fetch_ipe_csv csv %s: HTTP %s", year, resp.status_code)
    except Exception as exc:
        logger.warning("fetch_ipe_csv csv %s falhou: %s", year, exc)
    return None

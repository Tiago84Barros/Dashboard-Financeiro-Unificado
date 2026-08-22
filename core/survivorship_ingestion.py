"""
core/survivorship_ingestion.py — ingestão de tickers delisted.

Implementa a recomendação C3c (parcial) do parecer da banca examinadora
(2026-05-23): permitir EXTENSÃO da lista curada `DELISTED_BR_2010_2025`
sem editar core/survivorship.py — três fontes suportadas:

  1. JSON/CSV local em data_imports/delisted/ — usuário adiciona casos
     novos ou recentes (mais comum em produção)
  2. Cache/export local B3 (data_imports/delisted/b3) — caminho
     deterministico para API paga ou export institucional
  3. Cadastro aberto CVM — baixa CSV oficial, cacheia e mapeia eventos
     para tickers por arquivo de aliases auditavel

Em produção real seria preciso:
  • Cadastro CVM via portal aberto (sem scraping)
  • B3 Bovespa Data Master API (paga, ~R$ 2.5k/mês)
  • Histórico de OPAs publicado em DOU/CVM

MVP foca em fontes auditaveis: JSON/CSV local, cache B3 local e CVM
oficial com aliases CNPJ/CD_CVM/regex para resolver ticker.
"""
from __future__ import annotations

import csv
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from core.survivorship import (
    DELISTED_BR_2010_2025,
    DelistedTicker,
)

logger = logging.getLogger(__name__)

# Diretório padrão para arquivos de ingestão (relativo ao repo)
INGESTION_DIR_DEFAULT = "data_imports/delisted"
B3_CACHE_DIR_DEFAULT = "data_imports/delisted/b3"
CVM_CAD_CIA_ABERTA_URL = (
    "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/"
    "cad_cia_aberta.csv"
)
CVM_CACHE_DEFAULT = "data/cache/cvm/cad_cia_aberta.csv"
CVM_ALIAS_DEFAULT = "data_imports/delisted/cvm_ticker_aliases.csv"


# ──────────────────────────────────────────────────────────────────────────
# 1. Ingestão via JSON/CSV local
# ──────────────────────────────────────────────────────────────────────────

def _parse_date_flex(s: str) -> date | None:
    """Aceita 'YYYY-MM-DD' ou 'DD/MM/YYYY'."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def load_from_json(path: Path | str) -> list[DelistedTicker]:
    """Carrega lista de delisted de um JSON.

    Formato esperado (lista de objetos):
      [{"ticker": "ABCD3", "nome": "Empresa X",
        "data_delisting": "2023-05-15", "motivo": "opa",
        "ultimo_preco": 12.5}, ...]
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Falha ao ler %s: %s", p, exc)
        return []
    out: list[DelistedTicker] = []
    for item in data if isinstance(data, list) else []:
        try:
            d = _parse_date_flex(item.get("data_delisting", ""))
            if d is None:
                continue
            out.append(DelistedTicker(
                ticker=str(item["ticker"]).upper(),
                nome=str(item.get("nome", item["ticker"])),
                data_delisting=d,
                motivo=str(item.get("motivo", "fechamento_capital")),
                ultimo_preco=float(item.get("ultimo_preco", 0.0)),
            ))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Item ignorado em %s: %s — %s", p, item, exc)
    return out


def load_from_csv(path: Path | str) -> list[DelistedTicker]:
    """Carrega de CSV com header: ticker,nome,data_delisting,motivo,ultimo_preco"""
    p = Path(path)
    if not p.exists():
        return []
    out: list[DelistedTicker] = []
    try:
        with p.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = _parse_date_flex(row.get("data_delisting", ""))
                if d is None:
                    continue
                try:
                    out.append(DelistedTicker(
                        ticker=str(row["ticker"]).upper().strip(),
                        nome=str(row.get("nome", row["ticker"])).strip(),
                        data_delisting=d,
                        motivo=str(row.get("motivo", "fechamento_capital")).strip(),
                        ultimo_preco=float(row.get("ultimo_preco", 0.0)),
                    ))
                except (KeyError, ValueError, TypeError):
                    continue
    except Exception as exc:
        logger.warning("Falha ao ler CSV %s: %s", p, exc)
    return out


def load_all_local(dir_path: Path | str = INGESTION_DIR_DEFAULT
                   ) -> list[DelistedTicker]:
    """Varre dir_path por *.json e *.csv e concatena resultados."""
    d = Path(dir_path)
    if not d.exists() or not d.is_dir():
        return []
    out: list[DelistedTicker] = []
    for f in sorted(d.glob("*.json")):
        if ".example" in f.name.lower():
            continue
        out.extend(load_from_json(f))
    for f in sorted(d.glob("*.csv")):
        name = f.name.lower()
        if ".example" in name or "ticker_alias" in name:
            continue
        out.extend(load_from_csv(f))
    return out


def _cache_fresh(path: Path, ttl_days: int) -> bool:
    if ttl_days <= 0 or not path.exists():
        return False
    age_seconds = datetime.now().timestamp() - path.stat().st_mtime
    return age_seconds <= ttl_days * 24 * 3600


def _download_to_cache(
    url: str,
    cache_path: Path | str,
    ttl_days: int = 7,
    timeout: int = 30,
) -> Path | None:
    """Downloads a public CSV with simple TTL cache and atomic replace."""
    path = Path(cache_path)
    if _cache_fresh(path, ttl_days):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            content = resp.read()
        if len(content) < 1024:
            logger.warning("Download curto demais para %s (%s bytes)", url, len(content))
            return path if path.exists() else None
        tmp.write_bytes(content)
        tmp.replace(path)
        return path
    except (OSError, URLError, TimeoutError) as exc:
        logger.warning("Falha ao baixar %s: %s", url, exc)
        return path if path.exists() else None
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _clean_id(value: object) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _row_first(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


# ──────────────────────────────────────────────────────────────────────────
# 2. Scrape B3 (stub — graceful failure)
# ──────────────────────────────────────────────────────────────────────────

def load_b3_cache_dir(dir_path: Path | str = B3_CACHE_DIR_DEFAULT) -> list[DelistedTicker]:
    """Loads B3 delisting exports saved locally as JSON/CSV."""
    return load_all_local(dir_path)


def try_fetch_b3_delisted_recent(
    cache_dir: Path | str = B3_CACHE_DIR_DEFAULT,
) -> list[DelistedTicker]:
    """Returns B3 delistings from a maintained local cache.

    No unauthenticated remote scrape is attempted here. When B3 Data Master or
    an internal export is available, place it in cache_dir using the same
    schema accepted by load_from_json/load_from_csv.
    """
    return load_b3_cache_dir(cache_dir)


# ──────────────────────────────────────────────────────────────────────────
# 3. CVM IN 480 (stub similar)
# ──────────────────────────────────────────────────────────────────────────

def download_cvm_cadastro(
    cache_path: Path | str = CVM_CACHE_DEFAULT,
    ttl_days: int = 7,
    url: str = CVM_CAD_CIA_ABERTA_URL,
) -> Path | None:
    """Downloads the official CVM open-data company registry."""
    return _download_to_cache(url, cache_path, ttl_days=ttl_days)


def load_cvm_cancelamentos_raw(
    cache_path: Path | str = CVM_CACHE_DEFAULT,
    ttl_days: int = 7,
    url: str = CVM_CAD_CIA_ABERTA_URL,
) -> list[dict[str, str]]:
    """Loads CVM companies with cancelled registration from official CSV."""
    path = download_cvm_cadastro(cache_path=cache_path, ttl_days=ttl_days, url=url)
    if path is None or not Path(path).exists():
        return []

    rows: list[dict[str, str]] = []
    for enc in ("latin-1", "utf-8-sig"):
        try:
            with Path(path).open(encoding=enc, newline="") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    dt_cancel = _row_first(row, "DT_CANCEL", "data_cancelamento")
                    sit = _row_first(row, "SIT", "SITUACAO").lower()
                    if dt_cancel or "cancel" in sit:
                        rows.append(row)
            break
        except UnicodeDecodeError:
            rows = []
            continue
        except Exception as exc:
            logger.warning("Falha ao ler cadastro CVM %s: %s", path, exc)
            return []
    return rows


def load_cvm_ticker_aliases(
    path: Path | str = CVM_ALIAS_DEFAULT,
) -> list[dict[str, str]]:
    """Loads optional ticker aliases for CVM rows.

    Expected columns: ticker and at least one of cnpj_cia, cd_cvm, or
    nome_regex. Optional columns: data_delisting, motivo, ultimo_preco.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        with p.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            return [{str(k).strip(): str(v).strip() for k, v in row.items()}
                    for row in reader]
    except Exception as exc:
        logger.warning("Falha ao ler aliases CVM %s: %s", p, exc)
        return []


def _alias_matches_cvm_row(alias: dict[str, str], cvm_row: dict[str, str]) -> bool:
    cnpj_alias = _clean_id(_row_first(alias, "cnpj_cia", "cnpj", "CNPJ_CIA"))
    cd_alias = _clean_id(_row_first(alias, "cd_cvm", "CD_CVM", "codigo_cvm"))
    if cnpj_alias and cnpj_alias == _clean_id(_row_first(cvm_row, "CNPJ_CIA")):
        return True
    if cd_alias and cd_alias == _clean_id(_row_first(cvm_row, "CD_CVM")):
        return True

    name_pattern = _row_first(alias, "nome_regex", "denom_regex", "nome")
    if name_pattern:
        haystack = " ".join([
            _row_first(cvm_row, "DENOM_SOCIAL"),
            _row_first(cvm_row, "DENOM_COMERC"),
        ])
        try:
            if re.search(name_pattern, haystack, flags=re.IGNORECASE):
                return True
        except re.error:
            if name_pattern.lower() in haystack.lower():
                return True
    return False


def _parse_float_flex(value: object, default: float = 0.0) -> float:
    try:
        s = str(value or "").strip().replace(".", "").replace(",", ".")
        return float(s) if s else default
    except (TypeError, ValueError):
        return default


def load_cvm_cancelamentos(
    cache_path: Path | str = CVM_CACHE_DEFAULT,
    alias_path: Path | str = CVM_ALIAS_DEFAULT,
    ttl_days: int = 7,
) -> list[DelistedTicker]:
    """Maps official CVM cancelled registries to tickers using aliases.

    CVM's public company registry does not expose the B3 ticker. The official
    data is therefore used as the event source, while alias_path provides the
    auditable ticker mapping (CNPJ/CD_CVM/regex -> ticker).
    """
    raw_rows = load_cvm_cancelamentos_raw(cache_path=cache_path, ttl_days=ttl_days)
    aliases = load_cvm_ticker_aliases(alias_path)
    if not raw_rows or not aliases:
        return []

    out: list[DelistedTicker] = []
    for row in raw_rows:
        for alias in aliases:
            if not _alias_matches_cvm_row(alias, row):
                continue
            ticker = _row_first(alias, "ticker", "Ticker", "TICKER").upper()
            if not ticker:
                continue
            d = _parse_date_flex(
                _row_first(alias, "data_delisting", "dt_delisting")
                or _row_first(row, "DT_CANCEL")
            )
            if d is None:
                continue
            motivo = (
                _row_first(alias, "motivo")
                or _row_first(row, "MOTIVO_CANCEL")
                or "cancelamento_registro_cvm"
            )
            nome = (
                _row_first(alias, "nome")
                or _row_first(row, "DENOM_COMERC")
                or _row_first(row, "DENOM_SOCIAL")
                or ticker
            )
            out.append(DelistedTicker(
                ticker=ticker.replace(".SA", ""),
                nome=nome,
                data_delisting=d,
                motivo=str(motivo).lower().replace(" ", "_"),
                ultimo_preco=_parse_float_flex(_row_first(alias, "ultimo_preco")),
            ))
    return out


def try_fetch_cvm_cancelamentos(
    cache_path: Path | str = CVM_CACHE_DEFAULT,
    alias_path: Path | str = CVM_ALIAS_DEFAULT,
    ttl_days: int = 7,
) -> list[DelistedTicker]:
    """Fetches CVM cancellations from official open data plus ticker aliases."""
    return load_cvm_cancelamentos(
        cache_path=cache_path,
        alias_path=alias_path,
        ttl_days=ttl_days,
    )


# ──────────────────────────────────────────────────────────────────────────
# Merge — combina curada + locais + (futuro) scrape
# ──────────────────────────────────────────────────────────────────────────

def merge_delisted_sources(
    base: list[DelistedTicker] | None = None,
    locais: list[DelistedTicker] | None = None,
    b3: list[DelistedTicker] | None = None,
    cvm: list[DelistedTicker] | None = None,
) -> list[DelistedTicker]:
    """Mescla múltiplas fontes priorizando data_delisting mais antiga
    (case mais conservador para backtest survivorship-free).

    Em caso de conflito (mesmo ticker em fontes diferentes), mantém a
    entrada com data_delisting mais antiga — assumindo que dataset
    base curado pode estar mais correto sobre o evento original.
    """
    base   = base   if base   is not None else list(DELISTED_BR_2010_2025)
    locais = locais if locais is not None else []
    b3     = b3     if b3     is not None else []
    cvm    = cvm    if cvm    is not None else []

    pool: dict[str, DelistedTicker] = {}
    # Ordem: base > locais > b3 > cvm (prioridade decrescente em empate)
    for source in (cvm, b3, locais, base):
        for d in source:
            existing = pool.get(d.ticker)
            if existing is None or d.data_delisting < existing.data_delisting:
                pool[d.ticker] = d
    return list(pool.values())


def universo_delisted_total(
    data_ref: date | None = None,
    incluir_b3: bool = False,
    incluir_cvm: bool = False,
    dir_local: Path | str = INGESTION_DIR_DEFAULT,
    b3_cache_dir: Path | str = B3_CACHE_DIR_DEFAULT,
    cvm_cache_path: Path | str = CVM_CACHE_DEFAULT,
    cvm_alias_path: Path | str = CVM_ALIAS_DEFAULT,
) -> list[DelistedTicker]:
    """API principal: lista de delisted até data_ref combinando todas as
    fontes ativas.
    """
    if data_ref is None:
        data_ref = date.today()
    locais = load_all_local(dir_local)
    b3   = try_fetch_b3_delisted_recent(b3_cache_dir) if incluir_b3 else []
    cvm  = (
        try_fetch_cvm_cancelamentos(
            cache_path=cvm_cache_path,
            alias_path=cvm_alias_path,
        )
        if incluir_cvm else []
    )
    todos = merge_delisted_sources(locais=locais, b3=b3, cvm=cvm)
    return [d for d in todos if d.data_delisting <= data_ref]


def resumo_ingestao(
    dir_local: Path | str = INGESTION_DIR_DEFAULT,
    incluir_b3: bool = False,
    incluir_cvm: bool = False,
    b3_cache_dir: Path | str = B3_CACHE_DIR_DEFAULT,
    cvm_cache_path: Path | str = CVM_CACHE_DEFAULT,
    cvm_alias_path: Path | str = CVM_ALIAS_DEFAULT,
) -> dict:
    """Sumário das fontes carregadas para diagnóstico."""
    locais = load_all_local(dir_local)
    b3 = try_fetch_b3_delisted_recent(b3_cache_dir) if incluir_b3 else []
    cvm_raw = (
        load_cvm_cancelamentos_raw(cache_path=cvm_cache_path)
        if incluir_cvm else []
    )
    cvm = (
        load_cvm_cancelamentos(
            cache_path=cvm_cache_path,
            alias_path=cvm_alias_path,
        )
        if incluir_cvm else []
    )
    todos = merge_delisted_sources(locais=locais, b3=b3, cvm=cvm)
    return {
        "curados":       len(DELISTED_BR_2010_2025),
        "locais":        len(locais),
        "b3_cache":      len(b3),
        "cvm_canceladas": len(cvm_raw),
        "cvm_mapeadas":  len(cvm),
        "total_unicos":  len(todos),
        "dir_local":     str(Path(dir_local)),
        "dir_b3_cache":  str(Path(b3_cache_dir)),
        "cvm_cache":     str(Path(cvm_cache_path)),
        "cvm_aliases":   str(Path(cvm_alias_path)),
        "nota":          ("Para extensao manual, crie data_imports/delisted/"
                          "extras.json ou data_imports/delisted/b3/*.csv. "
                          "CVM baixa cadastro oficial, mas ticker exige alias."),
    }

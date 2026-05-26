"""
core/survivorship_ingestion.py — ingestão de tickers delisted.

Implementa a recomendação C3c (parcial) do parecer da banca examinadora
(2026-05-23): permitir EXTENSÃO da lista curada `DELISTED_BR_2010_2025`
sem editar core/survivorship.py — três fontes suportadas:

  1. JSON/CSV local em data_imports/delisted/ — usuário adiciona casos
     novos ou recentes (mais comum em produção)
  2. Cache de scrape B3 (b3.com.br/listings) — stub de scraping com
     graceful failure (CGV/anti-bot rejeita scraping ingênuo)
  3. CVM IN 480 (cvm.gov.br) — stub similar; produção pública requer
     login institucional

Em produção real seria preciso:
  • Cadastro CVM via portal aberto (sem scraping)
  • B3 Bovespa Data Master API (paga, ~R$ 2.5k/mês)
  • Histórico de OPAs publicado em DOU/CVM

MVP foca no caminho #1 (mais útil e estável) com infraestrutura
pronta pra plugar #2/#3 quando viável.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import date, datetime
from pathlib import Path

from core.survivorship import (
    DELISTED_BR_2010_2025, DelistedTicker, universo_delisted_ate,
)

logger = logging.getLogger(__name__)

# Diretório padrão para arquivos de ingestão (relativo ao repo)
INGESTION_DIR_DEFAULT = "data_imports/delisted"


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
        out.extend(load_from_json(f))
    for f in sorted(d.glob("*.csv")):
        out.extend(load_from_csv(f))
    return out


# ──────────────────────────────────────────────────────────────────────────
# 2. Scrape B3 (stub — graceful failure)
# ──────────────────────────────────────────────────────────────────────────

def try_fetch_b3_delisted_recent() -> list[DelistedTicker]:
    """Tenta buscar listing recente de delistings em b3.com.br.

    Stub: B3 usa CDN + JS dinâmico; scraping ingênuo não funciona.
    Produção real requer:
      a) API Bovespa Data Master (paga, ~R$ 2.5k/mês)
      b) Selenium + proxy residencial (frágil + lento)
      c) Parceria/credencial institucional

    Retorna [] sempre — usuário deve usar load_all_local com JSON/CSV
    para extensão prática.
    """
    return []


# ──────────────────────────────────────────────────────────────────────────
# 3. CVM IN 480 (stub similar)
# ──────────────────────────────────────────────────────────────────────────

def try_fetch_cvm_cancelamentos() -> list[DelistedTicker]:
    """Tenta buscar cancelamentos de registro CVM (IN 480).

    Stub: portal aberto da CVM tem CAPTCHA + RUSO; scraping fora dos
    termos. Em produção, usar IBGE-style download manual mensal
    publicado em cvm.gov.br/dados-abertos.

    Retorna [] sempre.
    """
    return []


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
) -> list[DelistedTicker]:
    """API principal: lista de delisted até data_ref combinando todas as
    fontes ativas. Por default, NÃO chama B3/CVM (stubs).
    """
    if data_ref is None:
        data_ref = date.today()
    locais = load_all_local(dir_local)
    b3   = try_fetch_b3_delisted_recent() if incluir_b3 else []
    cvm  = try_fetch_cvm_cancelamentos() if incluir_cvm else []
    todos = merge_delisted_sources(locais=locais, b3=b3, cvm=cvm)
    return [d for d in todos if d.data_delisting <= data_ref]


def resumo_ingestao(dir_local: Path | str = INGESTION_DIR_DEFAULT) -> dict:
    """Sumário das fontes carregadas para diagnóstico."""
    locais = load_all_local(dir_local)
    todos = merge_delisted_sources(locais=locais)
    return {
        "curados":       len(DELISTED_BR_2010_2025),
        "locais":        len(locais),
        "scrape_b3":     0,  # stub
        "scrape_cvm":    0,  # stub
        "total_unicos":  len(todos),
        "dir_local":     str(Path(dir_local)),
        "nota":          ("Para extensao manual, crie data_imports/delisted/"
                          "extras.json (ou .csv) com novos casos."),
    }

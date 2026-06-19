"""
data_pipeline/quality/scheduler.py
Scheduler incremental e seguro do saneamento.

- Processa N empresas por execução (cursor persistente) e rotaciona o universo;
  ao terminar o ciclo, reinicia. NÃO aumenta a frequência de scraping.
- Prioridade: carteira → nunca auditadas → auditadas há mais tempo → restante.
- Anti-bloqueio: atraso aleatório, retry com backoff exponencial, pausa em
  bloqueio — sem comportamento agressivo.

Núcleo (prioritize/rotate) é PURO e testável sem banco/rede.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable

logger = logging.getLogger(__name__)

_CURSOR_TABLE = "data_quality_cursor"
_CURSOR_KEY = "audit_universe"

# Defaults seguros (configuráveis por execução)
DEFAULT_BATCH = 50
DEFAULT_DELAY = 1.5          # s entre consultas
DEFAULT_JITTER = 0.6        # fração de variação aleatória
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2.0       # s base do backoff exponencial


# ─────────────────────────────────────────────────────────────────────────────
# Núcleo PURO
# ─────────────────────────────────────────────────────────────────────────────

def prioritize(
    universe: list[str],
    carteira: set[str] | None = None,
    last_audited: dict[str, float] | None = None,
) -> list[str]:
    """
    Ordena o universo por prioridade:
      1) empresas da carteira
      2) nunca auditadas (sem timestamp)
      3) auditadas há mais tempo (timestamp menor primeiro)
      4) restante
    `last_audited`: {ticker: epoch_segundos da última auditoria}.
    """
    carteira = {str(t).upper() for t in (carteira or set())}
    last_audited = last_audited or {}
    uniq = list(dict.fromkeys(str(t).upper() for t in universe if t))

    def sort_key(tk: str):
        in_cart = 0 if tk in carteira else 1
        ts = last_audited.get(tk)
        never = 0 if ts is None else 1
        ts_val = ts if ts is not None else 0.0
        return (in_cart, never, ts_val, tk)

    return sorted(uniq, key=sort_key)


def rotate(ordered: list[str], cursor: int, n: int) -> tuple[list[str], int]:
    """
    Retorna (lote de até n tickers a partir do cursor, novo_cursor).
    Faz wrap-around: ao chegar no fim, recomeça do início (novo ciclo).
    """
    if not ordered:
        return [], 0
    n = max(1, min(n, len(ordered)))
    start = cursor % len(ordered)
    end = start + n
    if end <= len(ordered):
        batch = ordered[start:end]
        new_cursor = end % len(ordered)
    else:
        wrap = end - len(ordered)
        batch = ordered[start:] + ordered[:wrap]
        new_cursor = wrap
    return batch, new_cursor


# ─────────────────────────────────────────────────────────────────────────────
# Anti-bloqueio
# ─────────────────────────────────────────────────────────────────────────────

def jitter_seconds(base: float = DEFAULT_DELAY, jitter: float = DEFAULT_JITTER) -> float:
    """Atraso com variação aleatória: base ± base*jitter (nunca negativo)."""
    delta = base * jitter
    return max(0.0, base + random.uniform(-delta, delta))


def sleep_jittered(base: float = DEFAULT_DELAY, jitter: float = DEFAULT_JITTER) -> None:
    time.sleep(jitter_seconds(base, jitter))


def with_backoff(
    fn: Callable,
    retries: int = DEFAULT_RETRIES,
    base: float = DEFAULT_BACKOFF,
    on_block: Callable[[Exception], bool] | None = None,
):
    """
    Executa fn com retry exponencial + jitter. Se `on_block(exc)` retornar True
    (bloqueio/CAPTCHA detectado), pausa mais longa antes de tentar de novo.
    Levanta a última exceção se esgotar as tentativas.
    """
    last_exc: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = base * (2 ** attempt) + random.uniform(0, base)
            if on_block and on_block(exc):
                wait *= 3  # pausa automática mais longa em caso de bloqueio
            logger.warning("with_backoff tentativa %d falhou (%s); aguardando %.1fs",
                           attempt + 1, type(exc).__name__, wait)
            if attempt < retries - 1:
                time.sleep(wait)
    if last_exc:
        raise last_exc


# ─────────────────────────────────────────────────────────────────────────────
# IO: cursor persistente + montagem do lote
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_cursor_table(conn) -> None:
    from sqlalchemy import text
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_CURSOR_TABLE} (
            chave TEXT PRIMARY KEY,
            posicao INTEGER NOT NULL DEFAULT 0,
            ciclo INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))


def get_cursor() -> int:
    from sqlalchemy import text
    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        return 0
    try:
        with engine.begin() as conn:
            _ensure_cursor_table(conn)
            pos = conn.execute(
                text(f"SELECT posicao FROM {_CURSOR_TABLE} WHERE chave = :k"),
                {"k": _CURSOR_KEY},
            ).scalar()
            return int(pos or 0)
    except Exception as exc:
        logger.warning("get_cursor: %s", exc)
        return 0


def save_cursor(pos: int, wrapped: bool = False) -> None:
    from sqlalchemy import text
    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            _ensure_cursor_table(conn)
            conn.execute(text(f"""
                INSERT INTO {_CURSOR_TABLE} (chave, posicao, ciclo, updated_at)
                VALUES (:k, :p, :c, NOW())
                ON CONFLICT (chave) DO UPDATE SET
                    posicao = EXCLUDED.posicao,
                    ciclo = {_CURSOR_TABLE}.ciclo + :inc,
                    updated_at = NOW()
            """), {"k": _CURSOR_KEY, "p": int(pos), "c": 0, "inc": 1 if wrapped else 0})
    except Exception as exc:
        logger.warning("save_cursor: %s", exc)


def _load_universe() -> list[str]:
    try:
        from core import b3_db as _db
        df = _db.load_setores()
        if df is not None and not df.empty and "ticker" in df.columns:
            return sorted(df["ticker"].astype(str).str.upper().unique().tolist())
    except Exception as exc:
        logger.warning("_load_universe: %s", exc)
    return []


def _load_carteira() -> set[str]:
    try:
        from sqlalchemy import text
        from core.database import get_engine
        engine = get_engine()
        if engine is None:
            return set()
        with engine.connect() as conn:
            exists = conn.execute(text("""
                SELECT EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='b3_portfolio_model_items')
            """)).scalar()
            if not exists:
                return set()
            rows = conn.execute(text(
                'SELECT DISTINCT ticker FROM b3_portfolio_model_items'
            )).fetchall()
            return {str(r[0]).upper().replace(".SA", "") for r in rows if r[0]}
    except Exception as exc:
        logger.warning("_load_carteira: %s", exc)
        return set()


def _load_last_audited() -> dict[str, float]:
    try:
        from sqlalchemy import text
        from core.database import get_engine
        engine = get_engine()
        if engine is None:
            return {}
        with engine.connect() as conn:
            exists = conn.execute(text("""
                SELECT EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='data_quality_scores')
            """)).scalar()
            if not exists:
                return {}
            rows = conn.execute(text("""
                SELECT ticker, MAX(EXTRACT(EPOCH FROM last_audited_at)) AS ts
                FROM data_quality_scores GROUP BY ticker
            """)).fetchall()
            return {str(r[0]).upper(): float(r[1]) for r in rows if r[0] and r[1] is not None}
    except Exception as exc:
        logger.warning("_load_last_audited: %s", exc)
        return {}


def next_batch(n: int = DEFAULT_BATCH) -> tuple[list[str], int, bool]:
    """
    Monta o próximo lote a auditar. Retorna (tickers, novo_cursor, ciclo_reiniciado).
    Avança e persiste o cursor.
    """
    universe = _load_universe()
    if not universe:
        return [], 0, False
    ordered = prioritize(universe, _load_carteira(), _load_last_audited())
    cursor = get_cursor()
    batch, new_cursor = rotate(ordered, cursor, n)
    wrapped = new_cursor <= cursor and cursor != 0
    save_cursor(new_cursor, wrapped=wrapped)
    return batch, new_cursor, wrapped

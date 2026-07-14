"""
data_pipeline/market/integrity.py
Verificações de integridade de market.* contra os payloads brutos.

Núcleo do detector de ECOS DE DIVIDENDOS (bug CEB, PR #55): re-deriva dos
payloads brutos as linhas de cashDividends que o normalizador atual descarta
(core.brapi.dedup_cash_dividends) e verifica se alguma ainda existe em
market.dividends. Uso:
  • check_dividend_echoes()  — chamado ao fim de cada daily/annual; qualquer
    eco encontrado vira linha em market.data_quality_logs (painel de qualidade)
    em vez de corromper silenciosamente somas 12m e DY.
  • scripts/fix_dividends_class_mix.py — usa as mesmas funções para a limpeza.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import text

from core.brapi import dedup_cash_dividends
from data_pipeline.market import normalize as nz
from data_pipeline.market import repository as repo

logger = logging.getLogger(__name__)

BATCH_PAYLOADS = 300

# extrai só o cashDividends no servidor (payload pode ser results[] ou quote)
Q_PAYLOADS = """
    SELECT id, ticker,
           COALESCE(payload_json#>'{results,0,dividendsData,cashDividends}',
                    payload_json#>'{dividendsData,cashDividends}') AS cds,
           COALESCE(payload_json#>>'{results,0,symbol}',
                    payload_json->>'symbol') AS sym
    FROM market.brapi_raw_payloads
    WHERE endpoint='quote' AND request_status='success'
      AND payload_json IS NOT NULL AND id > :last {tk_filter}
    ORDER BY id LIMIT :lim
"""

_ALVO = """
        SELECT (j->>0)       AS ticker,
               (j->>1)::date AS event_date,
               (j->>2)       AS type,
               (j->>3)::numeric AS amount
        FROM jsonb_array_elements(CAST(:keys AS jsonb)) j
"""

SQL_MATCH = f"""
    SELECT d.ticker, d.event_date, d.type, d.amount
    FROM market.dividends d
    JOIN ({_ALVO}) a
      ON d.ticker = a.ticker AND d.event_date = a.event_date
     AND d.type = a.type AND abs(d.amount - a.amount) <= 0.0000005
    WHERE d.source = 'brapi.dev'
"""

SQL_DELETE = f"""
    DELETE FROM market.dividends d
    USING ({_ALVO}) a
    WHERE d.source = 'brapi.dev'
      AND d.ticker = a.ticker AND d.event_date = a.event_date
      AND d.type = a.type AND abs(d.amount - a.amount) <= 0.0000005
    RETURNING d.ticker, d.event_date, d.type, d.amount
"""


def dividend_drop_keys(items: list[dict], tickers: set[str]) -> set[tuple]:
    """Chaves (ticker, event_date_iso, type, amount_str) que o normalizador
    ATUAL descarta de um cashDividends, na forma exata em que o normalizador
    antigo as gravou em market.dividends."""
    kept_ids = {id(x) for x in dedup_cash_dividends(items)}
    keys: set[tuple] = set()
    for it in items:
        if id(it) in kept_ids:
            continue
        amount = nz._f(it.get("rate"))
        if amount is None or amount <= 0:
            continue                       # o normalizador também pulava
        ex = nz._as_date(it.get("lastDatePrior") or it.get("approvedOn"))
        pay = nz._sane_payment_date(nz._as_date(it.get("paymentDate")), ex)
        event = pay or ex
        if event is None:
            continue
        typ = str(it.get("label") or "DIVIDENDO")[:40]
        # NUMERIC(18,6) do Postgres arredonda half-up; round() do Python é
        # banker's (1.6657445 -> 1.665744) e erraria a chave por 1e-6.
        amt = str(Decimal(str(amount)).quantize(Decimal("0.000001"),
                                                rounding=ROUND_HALF_UP))
        for tk in tickers:
            keys.add((tk, event.isoformat(), typ, amt))
    return keys


def iter_payload_cash_dividends(conn, tickers: list[str] | None = None,
                                latest_only: bool = False):
    """Gera (payload_id, {tickers atribuíveis}, items) dos payloads quote.

    latest_only=True considera só o payload mais recente por ticker (checagem
    recorrente); False varre todos (limpeza histórica). O conjunto de tickers
    cobre o da coluna (requisitado/B3) e o symbol da brapi (renormalizações
    antigas gravaram sob ele nos casos de alias — ver market.ticker_alias).
    """
    tk_filter = ""
    params: dict = {}
    if tickers:
        tk_filter = "AND ticker = ANY(:tks)"
        params["tks"] = [t.upper().replace(".SA", "") for t in tickers]
    if latest_only:
        # 1 payload (o mais recente) por ticker; cursor de paginação = ticker
        q = Q_PAYLOADS.replace("SELECT id, ticker,",
                               "SELECT DISTINCT ON (ticker) id, ticker,") \
                      .replace("AND payload_json IS NOT NULL AND id > :last",
                               "AND payload_json IS NOT NULL AND ticker > :last") \
                      .replace("ORDER BY id LIMIT :lim",
                               "ORDER BY ticker, id DESC LIMIT :lim")
        cursor: object = ""
    else:
        q = Q_PAYLOADS
        cursor = 0
    q = q.replace("{tk_filter}", tk_filter)
    while True:
        rows = conn.execute(text(q), {"last": cursor, "lim": BATCH_PAYLOADS,
                                      **params}).fetchall()
        if not rows:
            break
        for pid, tk, cds, sym in rows:
            cursor = str(tk or "") if latest_only else max(cursor, pid)
            items = json.loads(cds) if isinstance(cds, str) else (cds or [])
            if not items:
                continue
            tks = {t for t in (str(tk or "").upper().replace(".SA", ""),
                               str(sym or "").upper().replace(".SA", "")) if t}
            yield pid, tks, items
        if len(rows) < BATCH_PAYLOADS:
            break


def check_dividend_echoes(engine, tickers: list[str] | None = None,
                          persist: bool = True) -> dict:
    """Detector recorrente: procura em market.dividends linhas que o
    normalizador atual descartaria (ecos de classe/fonte CSV da brapi).

    O estado saudável é 0 — a ingestão descarta os ecos na entrada. Achados
    indicam regressão (formato novo da brapi, ingestão fora da main etc.) e
    são gravados em market.data_quality_logs (severity=warn) para aparecerem
    no painel de qualidade. Retorna {"linhas_eco", "tickers"}.
    """
    keys: set[tuple] = set()
    with engine.connect() as conn:
        for _pid, tks, items in iter_payload_cash_dividends(
                conn, tickers, latest_only=True):
            keys |= dividend_drop_keys(items, tks)
        por_ticker: dict[str, int] = {}
        ordered = sorted(keys)
        for i in range(0, len(ordered), 1000):
            chunk = json.dumps([list(k) for k in ordered[i:i + 1000]])
            for r in conn.execute(text(SQL_MATCH), {"keys": chunk}).fetchall():
                por_ticker[r[0]] = por_ticker.get(r[0], 0) + 1
    total = sum(por_ticker.values())
    if total and persist:
        with engine.begin() as conn:
            for tk, n in sorted(por_ticker.items()):
                repo.log_quality(
                    conn, ticker=tk, table_name="dividends",
                    field_name="amount", issue_type="dividend_class_echo",
                    new_value=f"{n} linha(s) de eco de classe/fonte CSV no banco",
                    severity="warn")
        logger.warning("integrity: %d eco(s) de dividendos em %d ticker(s) — "
                       "rode scripts/fix_dividends_class_mix.py --apply",
                       total, len(por_ticker))
    return {"linhas_eco": total, "tickers": por_ticker}

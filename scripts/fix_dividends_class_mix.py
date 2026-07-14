# -*- coding: utf-8 -*-
"""
Limpeza de market.dividends: ecos de outra classe/fonte CSV da brapi.

Contexto (2026-07): em empresas multi-classe (CEBR5/6, BRSR5/6, UNIP5/6...)
a brapi mescla no cashDividends de CADA classe as linhas de TODAS as classes
vindas de uma fonte CSV secundária — remarks 'csv:payment_date_estimated' ou
'unconfirmed-by-third-party', paymentDate estimado (= data-ex) e assetIssued
carimbado com o ISIN do próprio ticker (não discrimina). Como a UNIQUE de
market.dividends inclui amount, o eco coexiste com a linha correta e a soma
12m dobra (ex.: CEBR5 2025 somava 9,41 em vez de 4,4811 do Fato Relevante
CVM 12/08/2025). A mesma fonte também repete eventos parcelados/escala errada.

A mesma fonte ainda desloca a data-ex do eco (±1 dia em CCRO3/GUAR3/PETZ3...,
10 dias em AXIA5), escapando do casamento exato por (data-ex, label): AXIA5
somava 8,38 em 12m em vez de ~4,01. O dedup cobre isso com as regras B/C
(auto-eco por rate quase igual a confirmada em ±15d + queda do cluster).

O normalizador (core.brapi.dedup_cash_dividends, usado por
data_pipeline.market.normalize.dividend_rows) passou a descartar essas
entradas na ingestão; este script remove o que JÁ FOI gravado, re-derivando
de TODOS os payloads brutos exatamente as linhas que o normalizador atual
descartaria e apagando-as de market.dividends (source='brapi.dev').

Uso:
  python scripts/fix_dividends_class_mix.py             # dry-run (só relata)
  python scripts/fix_dividends_class_mix.py --apply     # deleta + reprocessa DY
  python scripts/fix_dividends_class_mix.py --apply --no-reprocess
  python scripts/fix_dividends_class_mix.py --tickers CEBR5 CEBR6
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from core.brapi import dedup_cash_dividends
from data_pipeline.market import normalize as nz

BATCH_PAYLOADS = 300
BATCH_KEYS = 1000

# extrai só o cashDividends no servidor (payload pode ser results[] ou quote)
_Q_PAYLOADS = """
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

_SQL_DRY = f"""
    SELECT d.ticker, d.event_date, d.type, d.amount
    FROM market.dividends d
    JOIN ({_ALVO}) a
      ON d.ticker = a.ticker AND d.event_date = a.event_date
     AND d.type = a.type AND abs(d.amount - a.amount) <= 0.0000005
    WHERE d.source = 'brapi.dev'
"""

_SQL_DELETE = f"""
    DELETE FROM market.dividends d
    USING ({_ALVO}) a
    WHERE d.source = 'brapi.dev'
      AND d.ticker = a.ticker AND d.event_date = a.event_date
      AND d.type = a.type AND abs(d.amount - a.amount) <= 0.0000005
    RETURNING d.ticker, d.event_date, d.type, d.amount
"""


def derive_drop_keys(items: list[dict], tickers: set[str]) -> set[tuple]:
    """Chaves (ticker, event_date, type, amount) que o normalizador ATUAL
    descarta, na forma exata em que o normalizador ANTIGO as gravou."""
    kept_ids = {id(x) for x in dedup_cash_dividends(items)}
    keys: set[tuple] = set()
    for it in items:
        if id(it) in kept_ids:
            continue
        amount = nz._f(it.get("rate"))
        if amount is None or amount <= 0:
            continue                       # o antigo também pulava
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="executa os DELETEs (default: dry-run)")
    ap.add_argument("--no-reprocess", action="store_true",
                    help="não recalcular métricas (DY) dos tickers afetados após o --apply")
    ap.add_argument("--tickers", nargs="*", default=None, help="restringe a estes tickers")
    args = ap.parse_args()

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        print("ERRO: banco não configurado (DATABASE_URL/SUPABASE_UNIFICADO_URL).")
        return 1

    tk_filter = ""
    params_extra = {}
    if args.tickers:
        tk_filter = "AND ticker = ANY(:tks)"
        params_extra["tks"] = [t.upper().replace(".SA", "") for t in args.tickers]

    # 1) varre TODOS os payloads quote e re-deriva as chaves a apagar
    all_keys: set[tuple] = set()
    last, n_payloads = 0, 0
    with engine.connect() as conn:
        while True:
            rows = conn.execute(
                text(_Q_PAYLOADS.replace("{tk_filter}", tk_filter)),
                {"last": last, "lim": BATCH_PAYLOADS, **params_extra}).fetchall()
            if not rows:
                break
            for pid, tk, cds, sym in rows:
                last = pid
                n_payloads += 1
                items = json.loads(cds) if isinstance(cds, str) else (cds or [])
                if not items:
                    continue
                # ingest grava sob o ticker requisitado (payload.ticker); o
                # renormalize histórico gravou sob o symbol da brapi — cobre os dois
                tks = {t for t in (str(tk or "").upper().replace(".SA", ""),
                                   str(sym or "").upper().replace(".SA", "")) if t}
                all_keys |= derive_drop_keys(items, tks)
    print(f"payloads varridos: {n_payloads}; chaves candidatas a DELETE: {len(all_keys)}")

    if not all_keys:
        print("nada a fazer.")
        return 0

    # 2) casa com o banco (dry-run) ou deleta (--apply), em lotes
    ordered = sorted(all_keys)
    por_ticker: dict[str, int] = defaultdict(int)
    total = 0
    sql = _SQL_DELETE if args.apply else _SQL_DRY
    for i in range(0, len(ordered), BATCH_KEYS):
        chunk = json.dumps([list(k) for k in ordered[i:i + BATCH_KEYS]])
        if args.apply:
            with engine.begin() as conn:
                hit = conn.execute(text(sql), {"keys": chunk}).fetchall()
        else:
            with engine.connect() as conn:
                hit = conn.execute(text(sql), {"keys": chunk}).fetchall()
        for r in hit:
            por_ticker[r[0]] += 1
            total += 1

    verbo = "DELETADAS" if args.apply else "seriam deletadas (dry-run)"
    print(f"\nlinhas {verbo}: {total} em {len(por_ticker)} tickers")
    for tk in sorted(por_ticker, key=lambda t: -por_ticker[t]):
        print(f"  {tk}: {por_ticker[tk]}")

    # 3) recalcula métricas derivadas (DY anual/ttm, Payout) dos afetados
    if args.apply and por_ticker and not args.no_reprocess:
        print("\nreprocessando métricas dos tickers afetados...")
        from data_pipeline.market import ingest
        prog = ingest.reprocess_metrics(tickers=sorted(por_ticker))
        print(f"reprocess_metrics: {prog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

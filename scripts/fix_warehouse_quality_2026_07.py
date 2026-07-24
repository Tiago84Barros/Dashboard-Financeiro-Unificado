# -*- coding: utf-8 -*-
"""
Correções de qualidade apontadas pela auditoria percentual de 2026-07-23
(docs/auditoria_percentual_2026-07-23.md). Quatro frentes, todas com backup
CSV prévio das linhas afetadas (reversível):

  1. market.historical_prices — remove candles vazios (close nulo/<=0), que a
     brapi devolve para meses sem negociação de FIIs/ETFs ilíquidos (1.526
     linhas; apenas 1 tinha volume). A ingestão passou a pular esses candles
     (data_pipeline/market/normalize.py::price_rows) e um CHECK impede o retorno.
  2. market.dividends — remove proventos com amount <= 0 (18 linhas legadas de
     antes da guarda da ingestão) e duplicatas exatas por
     (ticker, ex_date, type, amount) — mesmas linhas com payment_date estimado
     vs confirmado; mantém a mais recente (maior id).
  3. market_us.income_statements / balance_sheets / cash_flow_statements —
     corrige fiscal_year gravado como serial-Excel (ex.: 43465 = 2018; PRTH e
     TNET). Se a linha corrigida colidir com uma já correta, a serial é
     removida (é o mesmo período). O parser foi corrigido na origem
     (data_pipeline/us/edgar_facts.py::_sane_fiscal_year) e um CHECK impede o
     retorno.
  4. market.fiis — fecha lacunas do cadastro sem rede via
     data_pipeline.market.fii_ingest.enrich_cadastro_gaps():
     segmento <- segmento_cvm; vacancia <- última observação PIT.

Uso:
  python scripts/fix_warehouse_quality_2026_07.py            # dry-run (relata)
  python scripts/fix_warehouse_quality_2026_07.py --apply    # aplica com backup
  python scripts/fix_warehouse_quality_2026_07.py --apply --no-constraints

Aponte DATABASE_URL para o banco alvo (armazém local ou Supabase). Os backups
vão para migration/backup/fix_quality_2026_07/ (fora do controle de versão).
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

BACKUP_DIR = ROOT / "migration" / "backup" / "fix_quality_2026_07"

# Linhas problemáticas por frente: (rótulo, SELECT das afetadas, DELETE/UPDATE).
PRICES_SELECT = """
    SELECT * FROM market.historical_prices
    WHERE close IS NULL OR close <= 0
"""
PRICES_DELETE = "DELETE FROM market.historical_prices WHERE close IS NULL OR close <= 0"

DIV_INVALID_SELECT = "SELECT * FROM market.dividends WHERE amount IS NULL OR amount <= 0"
DIV_INVALID_DELETE = "DELETE FROM market.dividends WHERE amount IS NULL OR amount <= 0"

# Duplicata exata: mesma (ticker, ex_date, type, amount); difere só no
# payment_date estimado vs confirmado. Mantém a linha mais recente (maior id).
DIV_DUP_SELECT = """
    SELECT d.* FROM market.dividends d
    JOIN (
        SELECT ticker, ex_date, type, amount, MAX(id) AS keep_id
        FROM market.dividends
        GROUP BY 1, 2, 3, 4
        HAVING COUNT(*) > 1
    ) g ON g.ticker = d.ticker AND g.ex_date IS NOT DISTINCT FROM d.ex_date
       AND g.type = d.type AND g.amount = d.amount AND d.id <> g.keep_id
"""
DIV_DUP_DELETE = """
    DELETE FROM market.dividends d
    USING (
        SELECT ticker, ex_date, type, amount, MAX(id) AS keep_id
        FROM market.dividends
        GROUP BY 1, 2, 3, 4
        HAVING COUNT(*) > 1
    ) g
    WHERE g.ticker = d.ticker AND g.ex_date IS NOT DISTINCT FROM d.ex_date
      AND g.type = d.type AND g.amount = d.amount AND d.id <> g.keep_id
"""

_US_TABLES = ("income_statements", "balance_sheets", "cash_flow_statements")
# > 2027: além dos seriais-Excel, remove resíduo do parser v3 com períodos
# futuros (PMT-PA FY2028/2029, fatos de vencimento tratados como exercício).
_FY_BAD = "(fiscal_year > 2027 OR fiscal_year < 1990)"
# Serial-Excel: dias desde 1899-12-30. Faixa 20000–80000 cobre 1954–2118.
_FY_FIX_EXPR = ("EXTRACT(YEAR FROM DATE '1899-12-30' "
                "+ fiscal_year * INTERVAL '1 day')::int")


def _us_select(table: str) -> str:
    return f"SELECT * FROM market_us.{table} WHERE {_FY_BAD}"


def _us_update(table: str) -> str:
    # Corrige onde o período corrigido ainda não existe; o resto (colisão com
    # linha já correta) é removido como duplicata do mesmo período.
    return f"""
        UPDATE market_us.{table} t
           SET fiscal_year = {_FY_FIX_EXPR}
         WHERE {_FY_BAD}
           AND fiscal_year BETWEEN 20000 AND 80000
           AND NOT EXISTS (
               SELECT 1 FROM market_us.{table} x
                WHERE x.company_id = t.company_id AND x.period = t.period
                  AND x.fiscal_quarter = t.fiscal_quarter
                  AND x.fiscal_year = {_FY_FIX_EXPR.replace('fiscal_year', 't.fiscal_year')}
           )
    """


def _us_delete(table: str) -> str:
    return f"DELETE FROM market_us.{table} WHERE {_FY_BAD}"


CONSTRAINTS = [
    ("market.historical_prices", "chk_historical_prices_close_positive",
     "CHECK (close IS NOT NULL AND close > 0)"),
    ("market.dividends", "chk_dividends_amount_positive",
     "CHECK (amount > 0)"),
] + [
    (f"market_us.{t}", f"chk_{t}_fiscal_year_sane",
     "CHECK (fiscal_year BETWEEN 1990 AND 2100)")
    for t in _US_TABLES
]


def _backup(conn, label: str, select_sql: str) -> int:
    rows = conn.execute(text(select_sql)).mappings().all()
    if not rows:
        return 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"{label}_{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
    print(f"  backup: {len(rows)} linha(s) -> {path}")
    return len(rows)


def _add_constraint(conn, table: str, name: str, definition: str) -> str:
    exists = conn.execute(text(
        "SELECT 1 FROM pg_constraint WHERE conname = :n AND conrelid = CAST(:t AS regclass)"
    ), {"n": name, "t": table}).scalar()
    if exists:
        return "já existe"
    conn.execute(text(f"ALTER TABLE {table} ADD CONSTRAINT {name} {definition}"))
    return "criada"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--apply", action="store_true", help="aplica (padrão: dry-run)")
    ap.add_argument("--no-constraints", action="store_true",
                    help="não adiciona os CHECKs preventivos")
    ap.add_argument("--no-cadastro", action="store_true",
                    help="pula o preenchimento do cadastro FII")
    args = ap.parse_args()

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        print("ERRO: banco não configurado (DATABASE_URL/SUPABASE_UNIFICADO_URL).")
        return 1

    fronts = [
        ("precos_close_nulo", PRICES_SELECT, [PRICES_DELETE]),
        ("dividendos_amount_invalido", DIV_INVALID_SELECT, [DIV_INVALID_DELETE]),
        ("dividendos_duplicados", DIV_DUP_SELECT, [DIV_DUP_DELETE]),
    ] + [
        (f"us_fiscal_year_{t}", _us_select(t), [_us_update(t), _us_delete(t)])
        for t in _US_TABLES
    ]

    with engine.connect() as conn:
        for label, select_sql, _ in fronts:
            n = conn.execute(
                text(f"SELECT COUNT(*) FROM ({select_sql}) s")).scalar() or 0
            print(f"{label}: {n} linha(s) afetada(s)")

    if not args.apply:
        print("\nDry-run — nada foi alterado. Rode com --apply para corrigir.")
        return 0

    with engine.begin() as conn:
        for label, select_sql, statements in fronts:
            print(f"\n== {label} ==")
            if _backup(conn, label, select_sql) == 0:
                print("  nada a corrigir")
                continue
            for sql in statements:
                r = conn.execute(text(sql))
                print(f"  {r.rowcount} linha(s) processada(s)")
        if not args.no_constraints:
            print("\n== constraints preventivas ==")
            for table, name, definition in CONSTRAINTS:
                print(f"  {table}.{name}: {_add_constraint(conn, table, name, definition)}")

    if not args.no_cadastro:
        print("\n== cadastro FII (segmento/vacância) ==")
        from data_pipeline.market import fii_ingest
        print(f"  {fii_ingest.enrich_cadastro_gaps()}")

    print("\nConcluído. Backups em", BACKUP_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

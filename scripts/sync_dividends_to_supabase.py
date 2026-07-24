# -*- coding: utf-8 -*-
"""
Sincroniza market.dividends do armazém local (fonte da verdade, já passada
pelos scrubs de ecos de classe — PR #55/#57/#58) para o Supabase (vitrine).

Contexto (auditoria 2026-07): o Supabase acumulou ~18 mil linhas de dividendos
que os scrubs locais removeram (ecos da fonte CSV da brapi em multi-classe),
inflando DY/soma 12m. Um truncate+load cego seria perigoso: o workflow diário
grava no Supabase eventos que o local ainda não ingeriu. Por isso o sync é
SELETIVO, por chave canônica (ticker, ex_date, type, amount):

  * ADICIONA no Supabase as linhas que só existem no local (verdade pós-scrub);
  * REMOVE do Supabase as linhas que só existem lá, MAS apenas quando:
      - o ticker tem cobertura local (o scrub avaliou aquele fundo/empresa), e
      - a ex-date tem mais de --recency-days dias (default 60) — eventos
        recentes podem ser legítimos e ainda não ingeridos localmente;
  * PRESERVA e relata o resto (recentes e tickers sem cobertura local).

Linhas removidas vão para backup CSV antes (reversível). Dry-run por padrão.

Uso (você executa, com a SUPABASE_DB_URL no .env; Docker do armazém ligado):
  python scripts/sync_dividends_to_supabase.py            # dry-run
  python scripts/sync_dividends_to_supabase.py --apply
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

BACKUP_DIR = ROOT / "migration" / "backup" / "sync_dividends"
COLUMNS = ("ticker", "payment_date", "ex_date", "amount", "type", "source", "event_date")


def canonical_key(row: dict) -> tuple:
    """Chave canônica de um provento; amount normalizado (0.190000 == 0.19)."""
    amount = row.get("amount")
    if amount is not None:
        amount = Decimal(str(amount)).normalize()
    ex = row.get("ex_date")
    return (str(row.get("ticker") or "").upper(),
            ex.isoformat() if ex else "",
            str(row.get("type") or ""),
            amount)


def plan_sync(local_rows: list[dict], remote_rows: list[dict], *,
              today: date, recency_days: int = 60) -> dict:
    """Plano puro de sincronização (testado em tests/test_sync_dividends.py)."""
    local_by_key = {canonical_key(r): r for r in local_rows}
    remote_keys = {canonical_key(r) for r in remote_rows}
    local_tickers = {k[0] for k in local_by_key}
    cutoff = today - timedelta(days=recency_days)

    to_insert = [row for key, row in local_by_key.items() if key not in remote_keys]
    to_delete, kept_recent, kept_uncovered = [], [], []
    for row in remote_rows:
        key = canonical_key(row)
        if key in local_by_key:
            continue
        if key[0] not in local_tickers:
            kept_uncovered.append(row)
        elif row.get("ex_date") is None or row["ex_date"] >= cutoff:
            kept_recent.append(row)
        else:
            to_delete.append(row)
    return {"to_insert": to_insert, "to_delete": to_delete,
            "kept_recent": kept_recent, "kept_uncovered": kept_uncovered}


def _warehouse_engine():
    from scripts.fix_warehouse_quality_2026_07 import _warehouse_engine as f
    return f()


def _fetch(conn) -> list[dict]:
    rows = conn.execute(text(
        f"SELECT id, {', '.join(COLUMNS)} FROM market.dividends")).mappings().all()
    return [dict(r) for r in rows]


def _fetch_chunked(engine, chunk: int = 5000, retries: int = 3) -> list[dict]:
    """Leitura paginada por id com retry por página — o pooler do Supabase
    (plano Free) derruba conexões longas com result sets grandes."""
    out: list[dict] = []
    last_id = 0
    while True:
        for attempt in range(1, retries + 1):
            try:
                with engine.connect() as conn:
                    rows = conn.execute(text(f"""
                        SELECT id, {', '.join(COLUMNS)} FROM market.dividends
                        WHERE id > :last ORDER BY id LIMIT :chunk
                    """), {"last": last_id, "chunk": chunk}).mappings().all()
                break
            except Exception as exc:
                if attempt == retries:
                    raise
                print(f"  página após id={last_id} falhou ({type(exc).__name__}) — "
                      f"tentativa {attempt + 1}/{retries}")
        if not rows:
            return out
        out.extend(dict(r) for r in rows)
        last_id = rows[-1]["id"]


def _backup(rows: list[dict], label: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = BACKUP_DIR / f"{label}_{stamp}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync market.dividends local -> Supabase")
    ap.add_argument("--apply", action="store_true", help="aplica (padrão: dry-run)")
    ap.add_argument("--recency-days", type=int, default=60,
                    help="não remove linhas só-remotas com ex-date mais recente que isso")
    args = ap.parse_args()

    from core.database import get_engine
    target = get_engine()
    if target is None:
        print("ERRO: destino não configurado (SUPABASE_DB_URL/DATABASE_URL).")
        return 1

    print("lendo armazém local...")
    with _warehouse_engine().connect() as conn:
        local_rows = _fetch(conn)
    print(f"  {len(local_rows)} linha(s) locais")
    print("lendo Supabase (paginado)...")
    remote_rows = _fetch_chunked(target)
    with target.connect() as conn:
        remote_assets = set(conn.execute(
            text("SELECT ticker FROM market.assets")).scalars().all())
    print(f"  {len(remote_rows)} linha(s) remotas")

    plan = plan_sync(local_rows, remote_rows,
                     today=date.today(), recency_days=args.recency_days)
    # FK: só insere tickers que existem em market.assets do destino.
    insertable = [r for r in plan["to_insert"] if r["ticker"] in remote_assets]
    skipped_fk = len(plan["to_insert"]) - len(insertable)

    print(f"\nplano ({args.recency_days}d de guarda de recência):")
    print(f"  adicionar no Supabase:            {len(insertable)}"
          + (f" (+{skipped_fk} puladas: ticker fora de market.assets)" if skipped_fk else ""))
    print(f"  remover ecos antigos do Supabase: {len(plan['to_delete'])}")
    print(f"  preservadas por recência:         {len(plan['kept_recent'])}")
    print(f"  preservadas sem cobertura local:  {len(plan['kept_uncovered'])}")

    if not args.apply:
        print("\nDry-run — nada foi alterado. Rode com --apply para sincronizar.")
        return 0

    if plan["to_delete"]:
        path = _backup(plan["to_delete"], "removidas_supabase")
        print(f"\nbackup das removidas: {path}")

    # Remoção em LOTES com commit por lote: cada DELETE individual custaria uma
    # ida-e-volta à rede (17k linhas = dezenas de minutos) e uma transação única
    # não sobrevive ao pooler instável do plano Free. Idempotente: re-executar
    # continua de onde parou.
    deleted = 0
    ids = [row["id"] for row in plan["to_delete"]]
    for start in range(0, len(ids), 1000):
        chunk = ids[start:start + 1000]
        with target.begin() as conn:
            deleted += conn.execute(
                text("DELETE FROM market.dividends WHERE id = ANY(:ids)"),
                {"ids": chunk}).rowcount
        print(f"  removidas {deleted}/{len(ids)}...")
    inserted = 0
    if insertable:
        with target.begin() as conn:
            for row in insertable:
                inserted += conn.execute(text(f"""
                    INSERT INTO market.dividends ({', '.join(COLUMNS)})
                    VALUES ({', '.join(':' + c for c in COLUMNS)})
                    ON CONFLICT DO NOTHING
                """), {c: row[c] for c in COLUMNS}).rowcount
    print(f"removidas: {deleted} | adicionadas: {inserted}")
    print("Concluído. Rode scripts/report_db_state.py para conferir.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

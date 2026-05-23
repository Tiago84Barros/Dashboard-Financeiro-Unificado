"""
migration/09_dedup_investment_transactions.py
==============================================
Remove linhas duplicadas em investment_transactions.

Contexto (2026-05-22):
  Apos auditoria do PM dos cards, identificamos que varios ativos tem qty
  inflada em 2-3x vs o App 2 Dashboard. A causa: multiplas importacoes da
  mesma transacao com external_ids diferentes (B3 XLSX + migracao SQLite +
  Nomad PDFs todas inseriram o mesmo trade com chaves de idempotencia
  distintas, escapando do dedup por external_id).

Criterio de duplicacao (uma 'transacao' eh unica por):
  (user_id, asset_id, type, transaction_date, quantity, unit_price)

Regra de retencao:
  Quando ha duplicatas, mantem a linha com MENOR created_at (a inserida
  primeiro). As demais sao DELETADAS.

  Nao usa external_id como critério porque ele varia entre fontes da
  mesma operacao real (ex: 'b3neg-XXX', 'migr_app2_YYY', 'xpcsv-ZZZ' para
  a mesma compra de 100 PETR4 em 2024-01-15 a R$ 35,50).

Modos:
  python migration/09_dedup_investment_transactions.py            # dry-run (mostra deletes)
  python migration/09_dedup_investment_transactions.py --apply    # executa DELETE real
  python migration/09_dedup_investment_transactions.py --ticker BBAS3  # filtra por ticker

Idempotencia:
  Pos-execucao, re-rodar e seguro: nao encontra mais duplicatas para deletar.

Pos-dedup obrigatorio:
  python migration/08_compute_portfolio_positions.py --apply
  (recomputa portfolio_positions com os valores corretos)
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def fmt_br(v, d=2):
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def find_duplicate_groups(conn, text, owner_id: str, ticker_filter: str | None = None) -> list[dict]:
    """
    Retorna lista de grupos de duplicacao. Cada grupo eh um dict:
      {
        "ticker": str, "type": str, "date": str, "qty": float, "price": float,
        "rows": [{"id": uuid, "created_at": ts, "external_id": str|None}, ...],
        "keep": uuid (linha a manter — created_at mais antigo),
        "delete": [uuid, ...]
      }
    """
    base_filter = "WHERE it.user_id = :uid"
    params = {"uid": owner_id}
    if ticker_filter:
        base_filter += " AND UPPER(a.ticker) LIKE :tf"
        params["tf"] = f"{ticker_filter.upper()}%"

    rows = conn.execute(text(f"""
        SELECT
            it.id,
            it.user_id,
            it.asset_id,
            it.type,
            it.transaction_date,
            it.quantity,
            it.unit_price,
            it.created_at,
            it.external_id,
            a.ticker
        FROM investment_transactions it
        JOIN assets a ON a.id = it.asset_id
        {base_filter}
        ORDER BY it.transaction_date, it.created_at
    """), params).fetchall()

    # Agrupa por chave de dedup
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        key = (
            str(r.user_id),
            str(r.asset_id),
            str(r.type),
            str(r.transaction_date),
            float(r.quantity or 0),
            float(r.unit_price or 0),
        )
        by_key[key].append({
            "id":          str(r.id),
            "created_at":  r.created_at,
            "external_id": r.external_id,
            "ticker":      r.ticker,
            "type":        r.type,
            "date":        str(r.transaction_date)[:10],
            "qty":         float(r.quantity or 0),
            "price":       float(r.unit_price or 0),
        })

    # Filtra somente grupos com 2+ linhas
    groups = []
    for key, lst in by_key.items():
        if len(lst) <= 1:
            continue
        # Ordena por created_at ASC: primeiro fica
        lst.sort(key=lambda x: x["created_at"])
        keep = lst[0]
        delete = lst[1:]
        groups.append({
            "ticker": keep["ticker"],
            "type":   keep["type"],
            "date":   keep["date"],
            "qty":    keep["qty"],
            "price":  keep["price"],
            "n_copias": len(lst),
            "keep_id":   keep["id"],
            "keep_ext":  keep["external_id"],
            "delete":    delete,
        })

    # Ordena por ticker e data para output legivel
    groups.sort(key=lambda g: (g["ticker"], g["date"]))
    return groups


def run(apply: bool, ticker_filter: str | None) -> int:
    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine
    from sqlalchemy import text

    _ensure_utf8_stdout()
    cfg = MigrationConfig.from_env(dry_run=not apply)

    if not cfg.dest_url:
        print("ERRO: SUPABASE_UNIFICADO_URL nao configurado.")
        return 1
    if not cfg.owner_id:
        print("ERRO: OWNER_USER_ID nao configurado.")
        return 1

    sep = "=" * 70
    mode = "APLICANDO DELETE" if apply else "DRY RUN"
    print(sep)
    print(f"  Dedup investment_transactions  [{mode}]")
    if ticker_filter:
        print(f"  Filtro: ticker LIKE {ticker_filter}%")
    print(sep)
    print()

    engine = make_engine(cfg.dest_url, source_label="dedup_tx", read_only_hint=False)

    with engine.begin() as conn:
        # Contagem antes
        total_antes = conn.execute(text(
            "SELECT COUNT(*) FROM investment_transactions WHERE user_id = :uid"
        ), {"uid": cfg.owner_id}).scalar()
        print(f"  Total investment_transactions (antes): {total_antes}")

        # Detecta grupos
        groups = find_duplicate_groups(conn, text, cfg.owner_id, ticker_filter)

        if not groups:
            print("  0 grupos de duplicatas encontrados — nada a fazer.")
            print(sep)
            return 0

        total_to_delete = sum(len(g["delete"]) for g in groups)
        print(f"  {len(groups)} grupos de duplicatas (total {total_to_delete} linhas a deletar)")
        print()

        # Resumo por ticker
        by_ticker: dict[str, dict] = defaultdict(lambda: {"groups": 0, "delete": 0, "qty_inflada": 0.0})
        for g in groups:
            t = by_ticker[g["ticker"]]
            t["groups"] += 1
            t["delete"] += len(g["delete"])
            # Aproxima "qty inflada" como (n_copias - 1) * qty (ignorando sinal de sell)
            t["qty_inflada"] += (g["n_copias"] - 1) * g["qty"]

        print("  Resumo por ticker:")
        print(f"  {'Ticker':<12} {'Grupos':>8} {'Deletes':>9} {'Qty inflada estimada':>22}")
        for ticker in sorted(by_ticker):
            t = by_ticker[ticker]
            print(f"  {ticker:<12} {t['groups']:>8} {t['delete']:>9} {fmt_br(t['qty_inflada'], 2):>22}")
        print()

        # Detalhamento (limitado a 30 grupos para nao poluir)
        print("  Detalhamento (primeiros 30 grupos):")
        print(f"  {'Ticker':<10} {'Tipo':<6} {'Data':<12} {'Qty':>10} {'Price':>10} {'#cop':>5}  keep_ext...    delete_exts")
        for g in groups[:30]:
            keep_ext = (g["keep_ext"] or "NULL")[:18]
            del_exts = ",".join((d["external_id"] or "NULL")[:14] for d in g["delete"][:3])
            print(f"  {g['ticker']:<10} {g['type']:<6} {g['date']:<12} {fmt_br(g['qty'], 0):>10} "
                  f"R$ {fmt_br(g['price']):>7} {g['n_copias']:>5}  {keep_ext:<20} -> {del_exts}")
        if len(groups) > 30:
            print(f"  ... + {len(groups) - 30} grupos nao mostrados")
        print()

        # Executa DELETE
        if not apply:
            print(f"  [DRY RUN] Nenhuma linha foi deletada.")
            print(f"  Para deletar de verdade:")
            print(f"    python migration/09_dedup_investment_transactions.py --apply")
            print(sep)
            return 0

        # APPLY: deleta em batches. Cast :ids::uuid[] porque psycopg2 envia
        # como text[] e investment_transactions.id é UUID.
        ids_to_delete = [d["id"] for g in groups for d in g["delete"]]
        print(f"  Deletando {len(ids_to_delete)} linhas em batches de 500...")
        deleted = 0
        BATCH = 500
        for i in range(0, len(ids_to_delete), BATCH):
            batch = ids_to_delete[i:i + BATCH]
            result = conn.execute(text(
                "DELETE FROM investment_transactions WHERE id = ANY(CAST(:ids AS uuid[]))"
            ), {"ids": batch})
            deleted += result.rowcount
            print(f"  ... batch {i // BATCH + 1}: {result.rowcount} deletadas")

        # Contagem depois
        total_depois = conn.execute(text(
            "SELECT COUNT(*) FROM investment_transactions WHERE user_id = :uid"
        ), {"uid": cfg.owner_id}).scalar()

        print()
        print(sep)
        print("  RESULTADO")
        print(sep)
        print(f"  Antes:     {total_antes}")
        print(f"  Deletadas: {deleted}")
        print(f"  Depois:    {total_depois}")
        print(f"  Diff:      {total_antes - total_depois}")
        if deleted != total_to_delete:
            print(f"  ATENCAO: esperava deletar {total_to_delete}, deletou {deleted}")
        print()
        print("  Proximo passo (obrigatorio):")
        print("    python migration/08_compute_portfolio_positions.py --apply")
        print(sep)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="Executa DELETE real (sem --apply faz dry-run)")
    parser.add_argument("--ticker", type=str, default=None,
                        help="Filtra por ticker prefix (ex: --ticker BBAS3 pega BBAS3 e BBAS3F)")
    args = parser.parse_args()
    return run(apply=args.apply, ticker_filter=args.ticker)


if __name__ == "__main__":
    sys.exit(main())

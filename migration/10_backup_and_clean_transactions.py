"""
migration/10_backup_and_clean_transactions.py
==============================================
Backup + DELETE total de investment_transactions do owner.

Contexto (2026-05-22):
  Apos dedup que removeu linhas legitimas por engano, decisao foi limpar
  tudo e re-importar do zero a partir dos XLSX da B3.

Fluxo:
  1. Backup completo de investment_transactions WHERE user_id=OWNER em
     artifacts/backup_invtx_TIMESTAMP.csv (todas as colunas)
  2. Mostra contagem antes
  3. DELETE FROM investment_transactions WHERE user_id=OWNER
  4. Confirma contagem zero

Modos:
  python migration/10_backup_and_clean_transactions.py            # dry-run
  python migration/10_backup_and_clean_transactions.py --apply    # backup + DELETE

Recuperacao:
  O CSV gerado em artifacts/ pode ser re-importado se necessario
  (usa external_id como chave, entao re-importacao e idempotente).
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def run(apply: bool) -> int:
    from sqlalchemy import text

    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine

    _ensure_utf8_stdout()
    cfg = MigrationConfig.from_env(dry_run=not apply)

    if not cfg.dest_url or not cfg.owner_id:
        print("ERRO: configuracao incompleta")
        return 1

    sep = "=" * 70
    mode = "BACKUP + DELETE" if apply else "DRY RUN (apenas backup)"
    print(sep)
    print(f"  Backup + Clean investment_transactions  [{mode}]")
    print(sep)
    print()

    engine = make_engine(cfg.dest_url, source_label="backup_clean_tx", read_only_hint=False)

    # Diretorio de backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PROJECT_ROOT / "artifacts"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"backup_invtx_{ts}.csv"

    with engine.begin() as conn:
        # Conta antes
        total_antes = conn.execute(text(
            "SELECT COUNT(*) FROM investment_transactions WHERE user_id = :uid"
        ), {"uid": cfg.owner_id}).scalar()
        print(f"  Linhas em investment_transactions (owner): {total_antes}")

        if total_antes == 0:
            print("  Nada a fazer.")
            return 0

        # ── 1. Backup ───────────────────────────────────────────────────
        print(f"  Backup para: {backup_path}")
        rows = conn.execute(text("""
            SELECT
                it.id, it.user_id, it.asset_id, it.portfolio_id, it.broker,
                it.type, it.quantity, it.unit_price, it.fees,
                it.transaction_date, it.external_id, it.created_at,
                a.ticker, a.name AS asset_name
            FROM investment_transactions it
            JOIN assets a ON a.id = it.asset_id
            WHERE it.user_id = :uid
            ORDER BY it.transaction_date, it.created_at
        """), {"uid": cfg.owner_id}).fetchall()

        with open(backup_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "id", "user_id", "asset_id", "portfolio_id", "broker",
                "type", "quantity", "unit_price", "fees",
                "transaction_date", "external_id", "created_at",
                "ticker", "asset_name",
            ])
            for r in rows:
                w.writerow([
                    r.id, r.user_id, r.asset_id, r.portfolio_id, r.broker,
                    r.type, r.quantity, r.unit_price, r.fees,
                    r.transaction_date, r.external_id, r.created_at,
                    r.ticker, r.asset_name,
                ])
        print(f"  Backup OK: {len(rows)} linhas salvas")
        print()

        # ── 2. DELETE ───────────────────────────────────────────────────
        if not apply:
            print("  [DRY RUN] DELETE seria executado, mas --apply nao foi passado.")
            print()
            print("  Para deletar de verdade:")
            print("    python migration/10_backup_and_clean_transactions.py --apply")
            print(sep)
            return 0

        print("  DELETE FROM investment_transactions WHERE user_id = :uid")
        result = conn.execute(text(
            "DELETE FROM investment_transactions WHERE user_id = :uid"
        ), {"uid": cfg.owner_id})
        print(f"  Linhas deletadas: {result.rowcount}")

        # Verifica
        total_depois = conn.execute(text(
            "SELECT COUNT(*) FROM investment_transactions WHERE user_id = :uid"
        ), {"uid": cfg.owner_id}).scalar()

        print()
        print(sep)
        print("  RESULTADO")
        print(sep)
        print(f"  Antes:     {total_antes}")
        print(f"  Depois:    {total_depois}")
        print(f"  Backup:    {backup_path}")
        print()
        print("  Proximos passos:")
        print("    1. Use o UI do App 4 (ou scripts/import_*.py) para re-upar os XLSX da B3")
        print("    2. python migration/08_compute_portfolio_positions.py --apply")
        print(sep)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Executa backup + DELETE real (sem --apply faz só backup)")
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

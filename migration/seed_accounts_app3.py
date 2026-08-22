"""
migration/seed_accounts_app3.py
================================
Cria as contas necessárias para receber as transações migradas do App 3.

Contas a criar (identificadas na análise do App 3 — 2026-05-14):
  1. Conta Corrente   (checking)    — para payment_type = Conta / Pix  (241 transações)
  2. Cartão C6        (credit_card) — para payment_type = Cartão de crédito, card_name = C6 (10)

Uso:
  python migration/seed_accounts_app3.py             # dry run
  python migration/seed_accounts_app3.py --apply     # cria de verdade
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Contas a criar: (name, type, initial_balance, currency)
ACCOUNTS_TO_CREATE = [
    {
        "name":            "Conta Corrente",
        "type":            "checking",
        "initial_balance": "0.00",
        "currency":        "BRL",
        "note":            "Conta / Pix (241 transacoes App3)",
    },
    {
        "name":            "Cartão C6",
        "type":            "credit_card",
        "initial_balance": "0.00",
        "currency":        "BRL",
        "note":            "Cartao de credito C6 (10 transacoes App3)",
    },
]


def run(apply: bool) -> int:
    from sqlalchemy import text  # noqa: PLC0415

    from migration.config import (
        MigrationConfig,
        _ensure_utf8_stdout,
        make_engine,
    )

    _ensure_utf8_stdout()
    cfg = MigrationConfig.from_env(dry_run=not apply)

    if not cfg.dest_url:
        print("ERRO: SUPABASE_UNIFICADO_URL nao configurado.")
        return 1
    if not cfg.owner_id:
        print("ERRO: OWNER_USER_ID nao configurado.")
        return 1

    sep = "=" * 60
    mode = "APLICANDO" if apply else "DRY RUN"
    print(sep)
    print(f"  Seed de contas App 3 — {mode}")
    print(sep)
    print(f"  user_id (owner): {cfg.owner_id}")
    print()

    engine = make_engine(cfg.dest_url, source_label="seed_accounts", read_only_hint=False)

    created_ids: dict[str, str] = {}
    errors: list[str] = []

    with engine.begin() as conn:
        # Contas já existentes para este usuário
        existing = conn.execute(
            text("SELECT name FROM accounts WHERE user_id = :uid"),
            {"uid": cfg.owner_id},
        ).fetchall()
        existing_names = {r[0].strip().lower() for r in existing}

        for acc in ACCOUNTS_TO_CREATE:
            name = acc["name"]
            already = name.strip().lower() in existing_names

            if already:
                # Buscar o ID existente
                row = conn.execute(
                    text("SELECT id FROM accounts WHERE user_id = :uid AND lower(name) = lower(:name)"),
                    {"uid": cfg.owner_id, "name": name},
                ).fetchone()
                acc_id = str(row[0]) if row else "?"
                created_ids[name] = acc_id
                print(f"  -- (ja existe) {name:<25} id={acc_id}")
                continue

            print(f"  ++ {name:<25} [{acc['type']}]  {acc['note']}", end="")

            if not apply:
                print("  (dry run)")
                continue

            try:
                row = conn.execute(
                    text("""
                        INSERT INTO accounts (user_id, name, type, initial_balance, currency, active)
                        VALUES (:uid, :name, :type, :bal, :cur, true)
                        RETURNING id
                    """),
                    {
                        "uid":  cfg.owner_id,
                        "name": name,
                        "type": acc["type"],
                        "bal":  acc["initial_balance"],
                        "cur":  acc["currency"],
                    },
                ).fetchone()
                acc_id = str(row[0])
                created_ids[name] = acc_id
                print(f"\n     -> id={acc_id}  OK")
            except Exception as e:  # noqa: BLE001
                print(f"\n     ERRO: {e}")
                errors.append(f"{name}: {e}")

    print()
    print(sep)
    if apply and not errors:
        print("  Contas criadas com sucesso!")
        print()
        print("  IDs para o .env (adicionar para usar na migracao):")
        print()
        for name, acc_id in created_ids.items():
            env_key = "ACCOUNT_ID_CC" if "Corrente" in name else "ACCOUNT_ID_C6"
            print(f"    {env_key}={acc_id}   # {name}")
        print()
        print("  Mapeamento payment_type → account_id:")
        if "Conta Corrente" in created_ids:
            print(f"    Conta / Pix  →  {created_ids['Conta Corrente']}")
        if "Cartão C6" in created_ids:
            print(f"    Cartao C6    →  {created_ids['Cartão C6']}")
    elif not apply:
        print(f"  Seriam criadas: {len(ACCOUNTS_TO_CREATE)} contas")
        print()
        print("  Para criar de verdade:")
        print("    python migration/seed_accounts_app3.py --apply")
    else:
        print(f"  Erros: {len(errors)}")
    print(sep)

    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Cria contas do App 3 no banco unificado")
    parser.add_argument("--apply", action="store_true", default=False)
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

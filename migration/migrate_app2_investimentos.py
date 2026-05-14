"""
migration/migrate_app2_investimentos.py
========================================
Migra dados do App 2 (SQLite Investimentos) para o banco unificado.

Ordem de carga (respeita FKs):
  1. financial_institutions  — 7 registros
  2. assets                  — 82 registros (fii→reit, stock→stock)
  3. investment_transactions — 1.054 buy/sell + transfer_in/out/split mapeados
  4. dividends               — 517 proventos (dividend, jcp, rendimento→reit_income)

Mapeamentos:
  assets.type:
    stock  → stock
    fii    → reit
  investment_transactions.type:
    buy          → buy
    sell         → sell
    transfer_in  → buy  (transferência entrada = recebimento de ativos, preço=0)
    transfer_out → sell (transferência saída  = envio de ativos, preço=0)
    split        → buy  (desdobramento = recebimento de ações, preço=0)
  dividends.type:
    dividend   → dividend
    jcp        → jcp
    rendimento → reit_income (provento de FII)

  dividends sem amount_per_unit:
    amount_per_unit = total_amount / quantity  (se quantity > 0)
    quantity = 1, amount_per_unit = total_amount  (se sem quantity)

Uso:
  python migration/migrate_app2_investimentos.py             # dry run
  python migration/migrate_app2_investimentos.py --apply     # migra de verdade
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "migration" / "output"

# ---------------------------------------------------------------------------
# Mapeamentos
# ---------------------------------------------------------------------------

ASSET_CLASS_MAP = {
    "stock": "stock",
    "fii":   "reit",
    "etf":   "etf",
    "bdr":   "stock",
    "crypto": "crypto",
    "fixed_income": "fixed_income",
}

TX_TYPE_MAP = {
    "buy":          "buy",
    "sell":         "sell",
    "transfer_in":  "buy",   # recebimento de ativos sem custo
    "transfer_out": "sell",  # envio de ativos sem custo
    "split":        "buy",   # desdobramento = novas ações recebidas
}

INCOME_TYPE_MAP = {
    "dividend":  "dividend",
    "jcp":       "jcp",
    "rendimento": "reit_income",
    "reit_income": "reit_income",
    "amortizacao": "amortization",
    "amortização": "amortization",
}

INSTITUTION_TYPE_MAP = {
    "XP":      "broker",
    "RICO":    "broker",
    "CLEAR":   "broker",
    "NOMAD":   "bank",
    "INTER":   "bank",
    "TESOURO": "bank",
    "B3":      "broker",
}


def _dec(value, precision=2):
    """Converte para Decimal com precisão controlada."""
    if value is None:
        return None
    try:
        q = Decimal(10) ** -precision
        return str(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def _load(filename):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("records", [])


# ---------------------------------------------------------------------------
# Etapas de migração
# ---------------------------------------------------------------------------

def migrate_institutions(conn, text, apply, owner_id):
    """Insere 7 instituições financeiras."""
    records = _load("03_app2_institutions.json")
    inserted, skipped = 0, 0

    existing = {r[0].lower() for r in conn.execute(
        text("SELECT lower(name) FROM financial_institutions")
    ).fetchall()}

    for r in records:
        name = str(r.get("name") or "").strip()
        code = str(r.get("code") or "").strip().upper()
        inst_type = INSTITUTION_TYPE_MAP.get(code, "broker")
        country = str(r.get("country") or "BR").strip().upper()

        if name.lower() in existing:
            print(f"    -- (ja existe) {name}")
            skipped += 1
            continue

        print(f"    ++ {name:<30} [{inst_type}]", end="")
        if not apply:
            print("  (dry run)")
            continue

        conn.execute(text("""
            INSERT INTO financial_institutions (name, type, active)
            VALUES (:name, :type, true)
            ON CONFLICT DO NOTHING
        """), {"name": name, "type": inst_type})
        print("  OK")
        inserted += 1

    return inserted, skipped


def migrate_assets(conn, text, apply, records):
    """Insere 82 ativos e retorna mapa app2_int_id → canonical_uuid."""
    id_map = {}  # app2_id (int) → canonical UUID
    inserted, skipped = 0, 0

    # Carregar ativos já existentes por ticker
    existing = {r[0].upper(): str(r[1]) for r in conn.execute(
        text("SELECT upper(ticker), id FROM assets")
    ).fetchall()}

    for r in records:
        app2_id = r.get("id")
        ticker = str(r.get("ticker") or "").strip().upper()
        name = str(r.get("name") or ticker).strip()
        raw_type = str(r.get("type") or "").strip().lower()
        asset_class = ASSET_CLASS_MAP.get(raw_type, "stock")
        currency = str(r.get("currency") or "BRL").strip()
        country = str(r.get("country") or "BR").strip().upper()

        if ticker in existing:
            id_map[app2_id] = existing[ticker]
            print(f"    -- (ja existe) {ticker:<12} id={existing[ticker][:8]}...")
            skipped += 1
            continue

        print(f"    ++ {ticker:<12} [{asset_class}]  {name[:30]}", end="")
        if not apply:
            print("  (dry run)")
            id_map[app2_id] = f"dry_run_{app2_id}"
            continue

        row = conn.execute(text("""
            INSERT INTO assets (ticker, name, class, currency)
            VALUES (:ticker, :name, :class, :currency)
            ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """), {"ticker": ticker, "name": name, "class": asset_class, "currency": currency}).fetchone()
        canonical_id = str(row[0])
        id_map[app2_id] = canonical_id
        print(f"  OK (id={canonical_id[:8]}...)")
        inserted += 1

    return id_map, inserted, skipped


def migrate_investment_transactions(conn, text, apply, records, asset_id_map, owner_id):
    """Insere operações de investimento."""
    inserted, skipped, errors = 0, 0, []

    for r in records:
        app2_asset_id = r.get("asset_id")
        canonical_asset_id = asset_id_map.get(app2_asset_id)
        if not canonical_asset_id or canonical_asset_id.startswith("dry_run_"):
            if apply:
                errors.append(f"tx id={r.get('id')}: asset_id {app2_asset_id} sem mapeamento")
            skipped += 1
            continue

        raw_type = str(r.get("type") or "").strip().lower()
        tx_type = TX_TYPE_MAP.get(raw_type)
        if not tx_type:
            errors.append(f"tx id={r.get('id')}: tipo '{raw_type}' sem mapeamento — ignorado")
            skipped += 1
            continue

        quantity = _dec(r.get("quantity"), 8)
        unit_price = _dec(r.get("price") or 0, 6)
        fees = _dec(r.get("fees") or 0, 2) or "0.00"
        tx_date = str(r.get("date") or "")[:10]  # YYYY-MM-DD

        if not quantity or not tx_date:
            skipped += 1
            continue

        if not apply:
            inserted += 1
            continue

        try:
            conn.execute(text("""
                INSERT INTO investment_transactions
                    (user_id, asset_id, type, quantity, unit_price, fees, transaction_date)
                VALUES (:uid, :asset_id, :type, :qty, :price, :fees, :date)
                ON CONFLICT DO NOTHING
            """), {
                "uid": owner_id,
                "asset_id": canonical_asset_id,
                "type": tx_type,
                "qty": quantity,
                "price": unit_price,
                "fees": fees,
                "date": tx_date,
            })
            inserted += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"tx id={r.get('id')}: {type(e).__name__}: {str(e)[:80]}")
            skipped += 1

    return inserted, skipped, errors


def migrate_dividends(conn, text, apply, records, asset_id_map, owner_id):
    """Insere proventos (dividendos, JCP, rendimentos FII)."""
    inserted, skipped, errors = 0, 0, []

    for r in records:
        app2_asset_id = r.get("asset_id")
        canonical_asset_id = asset_id_map.get(app2_asset_id)
        if not canonical_asset_id or canonical_asset_id.startswith("dry_run_"):
            skipped += 1
            continue

        raw_type = str(r.get("type") or "").strip().lower()
        inc_type = INCOME_TYPE_MAP.get(raw_type, "dividend")

        total_amount = _dec(r.get("amount") or 0, 2) or "0.00"
        # App2 não tem quantity/amount_per_unit separados
        # Convenção: quantity=1, amount_per_unit=total
        quantity = "1.00000000"
        amount_per_unit = total_amount

        payment_date = str(r.get("date") or "")[:10]
        if not payment_date:
            skipped += 1
            continue

        if not apply:
            inserted += 1
            continue

        try:
            conn.execute(text("""
                INSERT INTO dividends
                    (user_id, asset_id, type, amount_per_unit, quantity, total_amount, payment_date)
                VALUES (:uid, :asset_id, :type, :apu, :qty, :total, :pdate)
                ON CONFLICT DO NOTHING
            """), {
                "uid":      owner_id,
                "asset_id": canonical_asset_id,
                "type":     inc_type,
                "apu":      amount_per_unit,
                "qty":      quantity,
                "total":    total_amount,
                "pdate":    payment_date,
            })
            inserted += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"income id={r.get('id')}: {type(e).__name__}: {str(e)[:80]}")
            skipped += 1

    return inserted, skipped, errors


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------

def run(apply: bool) -> int:
    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

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
    print(f"  Migracao App 2 (SQLite Investimentos) — {mode}")
    print(sep)
    print()

    engine = make_engine(cfg.dest_url, source_label="migrate_app2", read_only_hint=False)
    total_errors = []

    with engine.begin() as conn:
        # ── 1. Instituições ──────────────────────────────────────────────
        print("STEP 1 — financial_institutions")
        ins_i, ins_s = migrate_institutions(conn, text, apply, cfg.owner_id)
        print(f"  inseridas={ins_i}  ja_existiam={ins_s}")
        print()

        # ── 2. Assets ────────────────────────────────────────────────────
        print("STEP 2 — assets")
        asset_records = _load("03_app2_assets.json")
        asset_id_map, ast_i, ast_s = migrate_assets(conn, text, apply, asset_records)
        print(f"  inseridos={ast_i}  ja_existiam={ast_s}  mapeados={len(asset_id_map)}")
        print()

        # ── 3. Investment transactions ────────────────────────────────────
        print("STEP 3 — investment_transactions")
        tx_records = _load("03_app2_transactions.json")
        tx_i, tx_s, tx_e = migrate_investment_transactions(
            conn, text, apply, tx_records, asset_id_map, cfg.owner_id
        )
        if tx_e:
            total_errors.extend(tx_e)
            for e in tx_e[:5]:
                print(f"  ERRO: {e}")
        print(f"  inseridos={tx_i}  ignorados={tx_s}  erros={len(tx_e)}")
        print()

        # ── 4. Dividends ──────────────────────────────────────────────────
        print("STEP 4 — dividends")
        inc_records = _load("03_app2_incomes.json")
        div_i, div_s, div_e = migrate_dividends(
            conn, text, apply, inc_records, asset_id_map, cfg.owner_id
        )
        if div_e:
            total_errors.extend(div_e)
            for e in div_e[:5]:
                print(f"  ERRO: {e}")
        print(f"  inseridos={div_i}  ignorados={div_s}  erros={len(div_e)}")
        print()

    print(sep)
    print("  RESUMO")
    print(sep)
    print(f"  financial_institutions : inseridas={ins_i}  ja_existiam={ins_s}")
    print(f"  assets                 : inseridos={ast_i}  ja_existiam={ast_s}")
    print(f"  investment_transactions: inseridos={tx_i}  ignorados={tx_s}  erros={len(tx_e)}")
    print(f"  dividends              : inseridos={div_i}  ignorados={div_s}  erros={len(div_e)}")
    print(f"  Total erros            : {len(total_errors)}")
    if not apply:
        print()
        print("  Para migrar de verdade:")
        print("    python migration/migrate_app2_investimentos.py --apply")
    print(sep)

    return 0 if not total_errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra App 2 SQLite para o banco unificado")
    parser.add_argument("--apply", action="store_true", default=False)
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

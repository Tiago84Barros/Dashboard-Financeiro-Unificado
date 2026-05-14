"""
migration/seed_categories_app3.py
==================================
Insere no banco unificado as categorias do App 3 que ainda não existem.

As 16 categorias novas identificadas na análise do dry run (2026-05-14):
  - user_id = NULL  → categorias de sistema (visíveis para todos os usuários)
  - ON CONFLICT DO NOTHING → idempotente, seguro para executar mais de uma vez

Uso:
  python migration/seed_categories_app3.py             # dry_run (só mostra o que faria)
  python migration/seed_categories_app3.py --apply     # insere de verdade
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Categorias a inserir (mapeadas da análise do App 3)
# ---------------------------------------------------------------------------
# Formato: (name, type, icon)
# type: income | expense | transfer
# Categorias que já existem no banco (OK) são omitidas:
#   Salário, Saúde, Transporte, Educação, Lazer, Assinaturas
# "Outros (Receita)" → já coberta por "Outros Rendimentos" (income, sistema)

NEW_CATEGORIES: list[tuple[str, str, str]] = [
    # ── Despesas ─────────────────────────────────────────────────────────
    ("Combustível",         "expense",  "fuel"),
    ("Compras",             "expense",  "shopping"),
    ("Condomínio",          "expense",  "building"),
    ("Despesas Domésticas", "expense",  "home"),
    ("Financiamento",       "expense",  "bank"),
    ("Internet",            "expense",  "wifi"),
    ("Luz",                 "expense",  "zap"),
    ("Mercado",             "expense",  "shopping-cart"),
    ("Outros",              "expense",  "more-horizontal"),
    # ── Receitas ─────────────────────────────────────────────────────────
    ("Reembolso",           "income",   "refresh-cw"),
    ("Renda Extra",         "income",   "plus-circle"),
    # ── Transferências / Investimentos ───────────────────────────────────
    ("Exterior",            "transfer", "globe"),
    ("Pagamento de Cartão", "transfer", "credit-card"),
    ("Renda Fixa",          "transfer", "trending-up"),
    ("Renda Variável",      "transfer", "bar-chart-2"),
]


def _default_color(cat_type: str) -> str:
    """Cor padrão por tipo — segue o padrão visual do seed original."""
    return {"income": "#22c55e", "expense": "#ef4444", "transfer": "#3b82f6"}.get(cat_type, "#6b7280")


def run(apply: bool) -> int:
    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    _ensure_utf8_stdout()
    cfg = MigrationConfig.from_env(dry_run=not apply)

    if not cfg.dest_url:
        print("ERRO: SUPABASE_UNIFICADO_URL nao configurado no .env")
        return 1

    mode = "APLICANDO" if apply else "DRY RUN"
    sep = "=" * 55
    print(sep)
    print(f"  Seed de categorias App 3 — {mode}")
    print(sep)
    print(f"  Total a inserir : {len(NEW_CATEGORIES)} categorias")
    print(f"  user_id         : NULL (categorias de sistema)")
    print(f"  Conflito        : ON CONFLICT DO NOTHING (idempotente)")
    print()

    engine = make_engine(cfg.dest_url, source_label="seed_categories", read_only_hint=False)

    inserted = 0
    skipped = 0
    errors = []

    with engine.begin() as conn:
        # Verificar quais já existem (por nome, case-insensitive)
        existing = conn.execute(
            text("SELECT lower(name) FROM categories WHERE user_id IS NULL")
        ).fetchall()
        existing_names = {r[0] for r in existing}

        for name, cat_type, icon in NEW_CATEGORIES:
            already = name.lower() in existing_names

            if already:
                print(f"  -- (ja existe) {name:<28} [{cat_type}]")
                skipped += 1
                continue

            print(f"  ++ {name:<32} [{cat_type}]", end="")

            if not apply:
                print("  (dry run — nao inserido)")
                continue

            try:
                conn.execute(
                    text("""
                        INSERT INTO categories (name, type, icon, color, user_id)
                        VALUES (:name, :type, :icon, :color, NULL)
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "name": name,
                        "type": cat_type,
                        "icon": icon,
                        "color": _default_color(cat_type),
                    },
                )
                print("  OK")
                inserted += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ERRO: {e}")
                errors.append(f"{name}: {e}")

    print()
    print(sep)
    if apply:
        print(f"  Inseridas : {inserted}")
        print(f"  Ja existiam: {skipped}")
        print(f"  Erros     : {len(errors)}")
    else:
        print(f"  Seriam inseridas : {len(NEW_CATEGORIES) - skipped}")
        print(f"  Ja existem       : {skipped}")
        print()
        print("  Para inserir de verdade:")
        print("    python migration/seed_categories_app3.py --apply")
    print(sep)

    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Insere categorias do App 3 no banco unificado"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Insere de verdade (padrão: dry run, só mostra)",
    )
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

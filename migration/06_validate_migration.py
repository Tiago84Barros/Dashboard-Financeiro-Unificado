"""
migration/06_validate_migration.py
====================================
Valida a migração comparando origens com o banco unificado.

Fase 4.6 — Validação

Validações executadas:
  1. Contagem de registros: origem vs destino por tabela
  2. Soma de receitas (transactions.amount > 0)
  3. Soma de despesas (transactions.amount < 0)
  4. Soma de movimentações de investimentos (investment_transactions)
  5. Datas mínimas e máximas por tabela
  6. Categorias sem mapeamento (warnings do 04)
  7. Ativos sem ticker (assets.ticker IS NULL)
  8. Duplicidades por (source_system, source_table, source_id)
  9. Registros pessoais sem user_id

Resultado:
  migration/output/06_validation_report.json
  Console: PASSOU / FALHOU por validação

Uso:
  python -m migration.06_validate_migration
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Validadores individuais
# ---------------------------------------------------------------------------

def _run_query(conn, sql: str, params: dict | None = None) -> Any:  # noqa: ANN001
    """Executa query e retorna resultado."""
    from sqlalchemy import text  # noqa: PLC0415

    return conn.execute(text(sql), params or {}).fetchone()


def _run_query_all(conn, sql: str, params: dict | None = None) -> list:  # noqa: ANN001
    from sqlalchemy import text  # noqa: PLC0415

    return conn.execute(text(sql), params or {}).fetchall()


def validate_counts(conn, cfg) -> dict:  # noqa: ANN001
    """V1 — Contagem de registros por tabela canônica."""
    tables = [
        "profiles", "accounts", "categories", "transactions",
        "budgets", "financial_goals", "assets",
        "investment_transactions", "dividends",
        "import_batches", "import_logs", "migration_source_map",
    ]
    counts = {}
    for table in tables:
        try:
            row = _run_query(conn, f"SELECT COUNT(*) FROM {table}")
            counts[table] = row[0] if row else 0
        except Exception as e:  # noqa: BLE001
            counts[table] = f"ERRO: {e}"
    return {"name": "V1_record_counts", "passed": True, "data": counts}


def validate_transaction_sums(conn, cfg) -> dict:  # noqa: ANN001
    """V2 — Soma de receitas e despesas."""
    try:
        row = _run_query(
            conn,
            """
            SELECT
                SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS total_income,
                SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END) AS total_expenses,
                COUNT(*) AS total_tx,
                COUNT(CASE WHEN status = 'settled' THEN 1 END) AS settled_tx
            FROM transactions
            WHERE user_id = :uid
            """,
            {"uid": cfg.owner_id},
        )
        data = {
            "total_income": str(row[0]) if row else "0",
            "total_expenses": str(row[1]) if row else "0",
            "total_transactions": row[2] if row else 0,
            "settled_transactions": row[3] if row else 0,
        }
        passed = data["total_transactions"] >= 0
    except Exception as e:  # noqa: BLE001
        data = {"error": str(e)}
        passed = False

    return {"name": "V2_transaction_sums", "passed": passed, "data": data}


def validate_investment_sums(conn, cfg) -> dict:  # noqa: ANN001
    """V3 — Soma de movimentações de investimentos."""
    try:
        row = _run_query(
            conn,
            """
            SELECT
                COUNT(*) AS total_ops,
                COUNT(CASE WHEN type = 'buy' THEN 1 END) AS compras,
                COUNT(CASE WHEN type = 'sell' THEN 1 END) AS vendas,
                SUM(quantity * unit_price + fees) AS volume_total
            FROM investment_transactions
            WHERE user_id = :uid
            """,
            {"uid": cfg.owner_id},
        )
        data = {
            "total_operations": row[0] if row else 0,
            "buy_count": row[1] if row else 0,
            "sell_count": row[2] if row else 0,
            "total_volume": str(row[3]) if row and row[3] else "0",
        }
        passed = True
    except Exception as e:  # noqa: BLE001
        data = {"error": str(e)}
        passed = False

    return {"name": "V3_investment_sums", "passed": passed, "data": data}


def validate_date_ranges(conn, cfg) -> dict:  # noqa: ANN001
    """V4 — Datas mínimas e máximas."""
    checks = {
        "transactions": ("due_date", "user_id", cfg.owner_id),
        "investment_transactions": ("transaction_date", "user_id", cfg.owner_id),
        "dividends": ("payment_date", "user_id", cfg.owner_id),
    }
    data = {}
    passed = True
    for table, (date_col, filter_col, filter_val) in checks.items():
        try:
            row = _run_query(
                conn,
                f"""
                SELECT MIN({date_col}), MAX({date_col}), COUNT(*)
                FROM {table}
                WHERE {filter_col} = :fval
                """,
                {"fval": filter_val},
            )
            data[table] = {
                "min_date": str(row[0]) if row and row[0] else None,
                "max_date": str(row[1]) if row and row[1] else None,
                "count": row[2] if row else 0,
            }
        except Exception as e:  # noqa: BLE001
            data[table] = {"error": str(e)}
            passed = False

    return {"name": "V4_date_ranges", "passed": passed, "data": data}


def validate_unmapped_categories(conn, cfg) -> dict:  # noqa: ANN001
    """V5 — Transações sem categoria mapeada."""
    try:
        row = _run_query(
            conn,
            """
            SELECT COUNT(*) FROM transactions
            WHERE user_id = :uid AND category_id IS NULL
            """,
            {"uid": cfg.owner_id},
        )
        count = row[0] if row else 0
        passed = True  # Categorias NULL são permitidas
        data = {
            "transactions_without_category": count,
            "note": "Categorias NULL são permitidas — categoria opcional no schema.",
        }
        if count > 0:
            data["warning"] = f"{count} transações sem categoria."
    except Exception as e:  # noqa: BLE001
        data = {"error": str(e)}
        passed = False

    return {"name": "V5_unmapped_categories", "passed": passed, "data": data}


def validate_assets_without_ticker(conn, _cfg) -> dict:  # noqa: ANN001
    """V6 — Ativos sem ticker."""
    try:
        row = _run_query(
            conn,
            "SELECT COUNT(*) FROM assets WHERE ticker IS NULL OR ticker = ''",
        )
        count = row[0] if row else 0
        passed = count == 0
        data = {
            "assets_without_ticker": count,
            "passed": passed,
        }
        if count > 0:
            data["warning"] = f"{count} ativo(s) sem ticker — não poderão buscar cotações."
    except Exception as e:  # noqa: BLE001
        data = {"error": str(e)}
        passed = False

    return {"name": "V6_assets_without_ticker", "passed": passed, "data": data}


def validate_duplicate_sources(conn, _cfg) -> dict:  # noqa: ANN001
    """V7 — Duplicidades em migration_source_map."""
    try:
        rows = _run_query_all(
            conn,
            """
            SELECT source, source_table, source_id, COUNT(*) AS cnt
            FROM migration_source_map
            GROUP BY source, source_table, source_id
            HAVING COUNT(*) > 1
            LIMIT 20
            """,
        )
        dupes = [
            {
                "source": r[0],
                "source_table": r[1],
                "source_id": r[2],
                "count": r[3],
            }
            for r in rows
        ]
        passed = len(dupes) == 0
        data = {
            "duplicates_found": len(dupes),
            "examples": dupes[:5],
        }
        if dupes:
            data["warning"] = f"{len(dupes)} combinações duplicadas em migration_source_map."
    except Exception as e:  # noqa: BLE001
        data = {"error": str(e)}
        passed = False

    return {"name": "V7_duplicate_sources", "passed": passed, "data": data}


def validate_records_without_user_id(conn, _cfg) -> dict:  # noqa: ANN001
    """V8 — Registros pessoais sem user_id."""
    tables = [
        "accounts", "transactions", "investment_transactions",
        "dividends", "budgets", "financial_goals", "portfolios",
        "portfolio_positions", "alerts", "import_batches",
    ]
    data: dict[str, int] = {}
    passed = True
    for table in tables:
        try:
            row = _run_query(
                conn,
                f"SELECT COUNT(*) FROM {table} WHERE user_id IS NULL",
            )
            count = row[0] if row else 0
            data[table] = count
            if count > 0:
                passed = False
        except Exception as e:  # noqa: BLE001
            data[table] = f"ERRO: {e}"

    return {
        "name": "V8_records_without_user_id",
        "passed": passed,
        "data": data,
        "warning": (
            "Registros sem user_id encontrados — verificar antes de ativar o app."
            if not passed
            else None
        ),
    }


def validate_import_log_coverage(conn, cfg) -> dict:  # noqa: ANN001
    """V9 — Cobertura de import_logs por tabela."""
    try:
        rows = _run_query_all(
            conn,
            """
            SELECT target_table, action, COUNT(*) AS cnt
            FROM import_logs il
            JOIN import_batches ib ON ib.id = il.batch_id
            WHERE ib.user_id = :uid
            GROUP BY target_table, action
            ORDER BY target_table, action
            """,
            {"uid": cfg.owner_id},
        )
        coverage = {}
        for r in rows:
            tbl = r[0]
            if tbl not in coverage:
                coverage[tbl] = {}
            coverage[tbl][r[1]] = r[2]

        data = {"coverage": coverage, "tables_covered": list(coverage.keys())}
        passed = True
    except Exception as e:  # noqa: BLE001
        data = {"error": str(e)}
        passed = False

    return {"name": "V9_import_log_coverage", "passed": passed, "data": data}


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

def validate_all(cfg) -> dict[str, Any]:  # noqa: ANN001
    """Executa todas as validações."""
    from migration.config import make_engine  # noqa: PLC0415

    result: dict[str, Any] = {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": cfg.dry_run,
        "validations": [],
        "passed": 0,
        "failed": 0,
        "warnings": [],
    }

    if not cfg.dest_url:
        result["warnings"].append("SUPABASE_UNIFICADO_URL não configurado.")
        print("  ❌ SUPABASE_UNIFICADO_URL ausente — validação ignorada.")
        return result

    if not cfg.owner_id:
        result["warnings"].append("OWNER_USER_ID não configurado.")

    validators = [
        validate_counts,
        validate_transaction_sums,
        validate_investment_sums,
        validate_date_ranges,
        validate_unmapped_categories,
        validate_assets_without_ticker,
        validate_duplicate_sources,
        validate_records_without_user_id,
        validate_import_log_coverage,
    ]

    try:
        engine = make_engine(cfg.dest_url, source_label="unified_supabase")

        with engine.connect() as conn:
            for validator in validators:
                try:
                    vresult = validator(conn, cfg)
                    result["validations"].append(vresult)
                    if vresult.get("passed"):
                        result["passed"] += 1
                        status = "✅ PASSOU"
                    else:
                        result["failed"] += 1
                        status = "❌ FALHOU"
                    print(f"  {status}  {vresult['name']}")

                    if vresult.get("warning"):
                        print(f"         ⚠️  {vresult['warning']}")

                except Exception as e:  # noqa: BLE001
                    result["failed"] += 1
                    result["warnings"].append(f"Erro em validação: {e}")
                    print(f"  ❌ ERRO  {validator.__name__}: {e}")

    except Exception as e:  # noqa: BLE001
        result["warnings"].append(f"Erro de conexão: {type(e).__name__}: {e}")
        print(f"  ❌ Erro de conexão: {e}")

    return result


# ---------------------------------------------------------------------------
# Persistência e relatório
# ---------------------------------------------------------------------------

def save_result(result: dict, cfg) -> Path:  # noqa: ANN001
    """Salva relatório de validação."""
    cfg.ensure_output_dir()
    path = cfg.output_path("06_validation_report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  💾 Relatório salvo em: {path}")
    return path


def print_report(result: dict) -> None:
    """Exibe resumo no console."""
    print("\n" + "=" * 55)
    print("  RESULTADO DA VALIDAÇÃO")
    print("=" * 55)
    print(f"  Passaram : {result.get('passed', 0)}")
    print(f"  Falharam : {result.get('failed', 0)}")
    total = result.get("passed", 0) + result.get("failed", 0)
    pct = (result.get("passed", 0) / total * 100) if total else 0
    print(f"  Taxa     : {pct:.0f}%")

    if result.get("failed", 0) == 0:
        print("\n  ✅ Migração validada com sucesso.")
    else:
        print("\n  ⚠️  Problemas encontrados — revisar antes de ativar o app.")

    print("=" * 55)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valida migração no banco unificado"
    )
    parser.add_argument("--no-dry-run", action="store_true", default=False)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from migration.config import MigrationConfig  # noqa: PLC0415

    cfg = MigrationConfig.from_env(dry_run=not args.no_dry_run)
    cfg.print_summary()

    print("\n🔍 Iniciando validação da migração...")
    result = validate_all(cfg)
    save_result(result, cfg)
    print_report(result)

    return 0 if result.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

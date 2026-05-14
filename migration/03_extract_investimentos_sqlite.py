"""
migration/03_extract_investimentos_sqlite.py
============================================
Extrai dados do SQLite do Dashboard Investimentos (App 2).

Fase 4.6 — Extração (App 2)

Tabelas esperadas no SQLite (mapeadas em banco_unificado_mapa_origem_destino.md):
  assets          → assets (canônico)
  institutions    → financial_institutions (canônico)
  accounts        → accounts (canônico)
  transactions    → investment_transactions (canônico)
  incomes         → dividends (canônico)
  xp_positions    → portfolio_positions (canônico, posição mais recente)
  position_snapshots → portfolio_positions (snapshot mais recente por ativo)
  sync_log        → import_logs (referência)

Regras:
  - Somente leitura no SQLite (não altera o arquivo .db).
  - dry_run=True: apenas contagens e amostra.
  - Cada registro recebe _source_system='app2', _source_table, _source_id.

Uso:
  python -m migration.03_extract_investimentos_sqlite
  python -m migration.03_extract_investimentos_sqlite --no-dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Tabelas esperadas no SQLite do App 2
# ---------------------------------------------------------------------------

APP2_TABLES_PRIORITY: list[str] = [
    "assets",           # Ativos (ações, FIIs, etc.)
    "institutions",     # Instituições financeiras
    "accounts",         # Contas de corretagem
    "transactions",     # Operações de compra/venda
    "incomes",          # Dividendos e proventos
    "xp_positions",     # Posições XP (se existir)
    "position_snapshots",  # Snapshots históricos de posições
    "sync_log",         # Log de sincronização
]

# Colunas de ID por tabela (para source_id)
APP2_ID_COLUMNS: dict[str, str] = {
    "assets": "id",
    "institutions": "id",
    "accounts": "id",
    "transactions": "id",
    "incomes": "id",
    "xp_positions": "id",
    "position_snapshots": "id",
    "sync_log": "id",
}

# Mapeamento de tipo de ativo SQLite → canônico
ASSET_CLASS_MAP: dict[str, str] = {
    "acao": "stock",
    "ação": "stock",
    "Ação": "stock",
    "stock": "stock",
    "fii": "reit",
    "FII": "reit",
    "reit": "reit",
    "etf": "etf",
    "ETF": "etf",
    "renda_fixa": "fixed_income",
    "fixed_income": "fixed_income",
    "cripto": "crypto",
    "crypto": "crypto",
    "other": "other",
}

# Mapeamento de tipo de transação SQLite → canônico
TX_TYPE_MAP: dict[str, str] = {
    "buy": "buy",
    "sell": "sell",
    "compra": "buy",
    "venda": "sell",
    "Compra": "buy",
    "Venda": "sell",
    "B": "buy",
    "S": "sell",
    "C": "buy",
    "V": "sell",
}


# ---------------------------------------------------------------------------
# Extração de uma tabela
# ---------------------------------------------------------------------------

def extract_table(
    conn,  # noqa: ANN001
    table: str,
    dry_run: bool,
    sample_size: int = 3,
) -> dict[str, Any]:
    """Extrai dados de uma tabela SQLite."""
    from sqlalchemy import inspect, text  # noqa: PLC0415

    result: dict[str, Any] = {
        "table": table,
        "source_system": "app2",
        "dry_run": dry_run,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "count": 0,
        "columns": [],
        "sample": [],
        "records": [],
        "warnings": [],
    }

    try:
        inspector = inspect(conn)
        all_tables = inspector.get_table_names()

        if table not in all_tables:
            result["warnings"].append(f"Tabela '{table}' não encontrada no SQLite.")
            print(f"    ⚠️  '{table}' não existe neste SQLite (pode não ser usado).")
            return result

        # Colunas
        cols_info = inspector.get_columns(table)
        result["columns"] = [c["name"] for c in cols_info]

        # Contagem
        count_row = conn.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        ).fetchone()
        result["count"] = count_row[0] if count_row else 0

        # Sample
        sample_rows = conn.execute(
            text(f"SELECT * FROM {table} LIMIT {sample_size}")
        ).mappings().fetchall()
        result["sample"] = [dict(r) for r in sample_rows]

        if dry_run:
            print(
                f"    [dry_run] {table:<25} {result['count']:>6,} registros | "
                f"cols: {result['columns']}"
            )
        else:
            print(f"    Extraindo {table}: {result['count']:,} registros...")
            all_rows = conn.execute(
                text(f"SELECT * FROM {table}")
            ).mappings().fetchall()

            id_col = APP2_ID_COLUMNS.get(table, "id")
            records = []
            for row in all_rows:
                r = dict(row)
                r["_source_system"] = "app2"
                r["_source_table"] = table
                r["_source_id"] = str(r.get(id_col, ""))
                records.append(r)

            result["records"] = records
            print(f"    ✅ {table}: {len(records):,} registros extraídos")

    except Exception as e:  # noqa: BLE001
        result["warnings"].append(f"Erro em '{table}': {type(e).__name__}: {e}")
        print(f"    ❌ Erro em '{table}': {e}")

    return result


# ---------------------------------------------------------------------------
# Extração principal
# ---------------------------------------------------------------------------

def extract(cfg) -> dict[str, Any]:  # noqa: ANN001
    """Extrai todas as tabelas do SQLite App 2."""
    from migration.config import make_engine  # noqa: PLC0415

    summary: dict[str, Any] = {
        "source": "app2_investimentos_sqlite",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": cfg.dry_run,
        "sqlite_path": "configurado" if cfg.app2_path else "AUSENTE",
        "tables": {},
        "all_sqlite_tables": [],
        "total_records": 0,
        "warnings": [],
        "asset_class_coverage": {},
        "tx_type_coverage": {},
    }

    if not cfg.app2_path:
        summary["warnings"].append(
            "SOURCE_DB_APP2 não configurado. Extração do App 2 ignorada."
        )
        print("  ⚠️  SOURCE_DB_APP2 ausente — extração simulada.")
        return summary

    # Normalizar caminho SQLite
    sqlite_url = cfg.app2_path
    if not sqlite_url.startswith("sqlite"):
        sqlite_url = f"sqlite:///{sqlite_url}"

    try:
        engine = make_engine(sqlite_url, source_label="app2_sqlite")

        with engine.connect() as conn:
            from sqlalchemy import inspect  # noqa: PLC0415

            inspector = inspect(engine)
            all_tables = inspector.get_table_names()
            summary["all_sqlite_tables"] = all_tables
            print(f"  Tabelas encontradas no SQLite: {all_tables}")
            print()

            for table in APP2_TABLES_PRIORITY:
                table_result = extract_table(conn, table, cfg.dry_run)
                summary["tables"][table] = table_result
                summary["total_records"] += table_result.get("count", 0)
                if table_result.get("warnings"):
                    summary["warnings"].extend(table_result["warnings"])

            # Inspecionar tabelas desconhecidas
            known = set(APP2_TABLES_PRIORITY)
            extra = [t for t in all_tables if t not in known]
            if extra:
                summary["warnings"].append(
                    f"Tabelas não catalogadas no SQLite: {extra}"
                )
                print(f"\n  ℹ️  Tabelas extras no SQLite: {extra}")

            # Análise de cobertura de tipos (se extraiu assets e transactions)
            if not cfg.dry_run:
                _analyze_coverage(summary)

    except Exception as e:  # noqa: BLE001
        summary["warnings"].append(
            f"Erro de conexão ao SQLite: {type(e).__name__}: {e}"
        )
        print(f"  ❌ Erro ao conectar ao SQLite: {e}")
        print("     Verifique SOURCE_DB_APP2 (caminho do arquivo .db).")

    return summary


def _analyze_coverage(summary: dict) -> None:
    """Analisa cobertura dos mapeamentos de tipo."""
    # Asset classes
    assets_data = summary["tables"].get("assets", {})
    if assets_data.get("records"):
        classes = {r.get("class") or r.get("classe") for r in assets_data["records"]}
        for c in classes:
            summary["asset_class_coverage"][str(c)] = ASSET_CLASS_MAP.get(
                str(c), "⚠️ SEM MAPEAMENTO"
            )

    # Transaction types
    tx_data = summary["tables"].get("transactions", {})
    if tx_data.get("records"):
        types = {r.get("type") or r.get("tipo") for r in tx_data["records"]}
        for t in types:
            summary["tx_type_coverage"][str(t)] = TX_TYPE_MAP.get(
                str(t), "⚠️ SEM MAPEAMENTO"
            )


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def save_result(summary: dict, cfg) -> list[Path]:  # noqa: ANN001
    """Salva extração em arquivos JSON."""
    cfg.ensure_output_dir()
    saved: list[Path] = []

    # Sumário
    summary_path = cfg.output_path("03_app2_summary.json")
    summary_clean = {k: v for k, v in summary.items() if k != "tables"}
    summary_clean["table_counts"] = {
        t: d.get("count", 0) for t, d in summary.get("tables", {}).items()
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_clean, f, ensure_ascii=False, indent=2, default=str)
    saved.append(summary_path)

    # Por tabela
    for table, data in summary.get("tables", {}).items():
        if data.get("count", 0) == 0 and not data.get("records"):
            continue
        table_path = cfg.output_path(f"03_app2_{table}.json")
        with open(table_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        saved.append(table_path)
        print(f"  💾 {table_path.name}")

    return saved


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

def print_report(summary: dict) -> None:
    """Exibe relatório resumido."""
    print("\n" + "=" * 60)
    print("  RELATÓRIO — Extração App 2 (SQLite Investimentos)")
    print("=" * 60)
    print(f"  Total de registros: {summary.get('total_records', 0):,}")
    print()

    for table, data in summary.get("tables", {}).items():
        count = data.get("count", 0)
        exists = count > 0 or data.get("columns")
        if not exists:
            print(f"  ➖  {table:<25} (não encontrada)")
            continue
        warns = len(data.get("warnings", []))
        status = "⚠️" if warns else "✅"
        cols = len(data.get("columns", []))
        print(f"  {status}  {table:<25} {count:>6,} registros  ({cols} colunas)")

    cov = summary.get("asset_class_coverage")
    if cov:
        print("\n  Cobertura de classes de ativo:")
        for cls, mapped in cov.items():
            print(f"    {cls:<20} → {mapped}")

    cov_tx = summary.get("tx_type_coverage")
    if cov_tx:
        print("\n  Cobertura de tipos de transação:")
        for t, mapped in cov_tx.items():
            print(f"    {t:<15} → {mapped}")

    if summary.get("warnings"):
        print("\n  Avisos:")
        for w in summary["warnings"]:
            print(f"    ⚠️  {w}")

    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrai dados do SQLite Dashboard Investimentos (App 2)"
    )
    parser.add_argument("--no-dry-run", action="store_true", default=False)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from migration.config import MigrationConfig  # noqa: PLC0415

    cfg = MigrationConfig.from_env(dry_run=not args.no_dry_run)
    cfg.print_summary()

    print("\n📥 Iniciando extração — App 2 (SQLite Investimentos)...")
    summary = extract(cfg)
    save_result(summary, cfg)
    print_report(summary)

    return 0 if not summary.get("warnings") else 1


if __name__ == "__main__":
    sys.exit(main())

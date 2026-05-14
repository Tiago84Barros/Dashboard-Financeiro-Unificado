"""
migration/02_extract_controle_financeiro.py
===========================================
Extrai dados do Supabase Controle Financeiro (App 3) — fonte de migração.

Fase 4.6 — Extração (App 3)

Tabelas extraídas (nomes em português — schema original do App 3):
  transacoes, contas, categorias, orcamentos, metas

Regras:
  - Conexão somente leitura (apenas SELECT).
  - Não modifica nenhuma tabela da origem.
  - Salva dados em migration/output/02_app3_<tabela>.json.
  - dry_run=True: mostra contagens sem salvar dados completos.
  - Cada registro recebe source_system='app3', source_table e source_id.

Uso:
  python -m migration.02_extract_controle_financeiro
  python -m migration.02_extract_controle_financeiro --no-dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Tabelas esperadas no App 3 (Controle Financeiro)
# ---------------------------------------------------------------------------

APP3_TABLES: list[str] = [
    "transacoes",
    "contas",
    "categorias",
    "orcamentos",
    "metas",
]

# Colunas de identificador por tabela (usado como source_id)
APP3_ID_COLUMNS: dict[str, str] = {
    "transacoes": "id",
    "contas": "id",
    "categorias": "id",
    "orcamentos": "id",
    "metas": "id",
}

# Colunas mínimas esperadas (para validação de schema)
APP3_EXPECTED_COLS: dict[str, list[str]] = {
    "transacoes": ["id", "descricao", "valor"],
    "contas": ["id", "nome"],
    "categorias": ["id", "nome", "tipo"],
    "orcamentos": ["id"],
    "metas": ["id", "nome"],
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
    """
    Extrai dados de uma tabela do App 3.

    Em dry_run: retorna apenas contagem e sample.
    Em modo real: retorna todos os registros.
    """
    from sqlalchemy import inspect, text  # noqa: PLC0415

    result: dict[str, Any] = {
        "table": table,
        "source_system": "app3",
        "dry_run": dry_run,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "count": 0,
        "columns": [],
        "sample": [],
        "records": [],
        "warnings": [],
    }

    try:
        # Verificar se tabela existe
        inspector = inspect(conn)
        # Para conexão já aberta, usa o engine
        tables_in_schema = [
            t for t in inspector.get_table_names(schema="public")
        ]

        if table not in tables_in_schema:
            result["warnings"].append(f"Tabela '{table}' não encontrada no banco App 3.")
            print(f"    ⚠️  Tabela '{table}' não existe no App 3.")
            return result

        # Colunas
        cols_info = inspector.get_columns(table, schema="public")
        result["columns"] = [c["name"] for c in cols_info]

        # Validar colunas esperadas
        expected = APP3_EXPECTED_COLS.get(table, [])
        missing = [c for c in expected if c not in result["columns"]]
        if missing:
            result["warnings"].append(
                f"Colunas esperadas ausentes em '{table}': {missing}"
            )

        # Contagem
        count_row = conn.execute(
            text(f"SELECT COUNT(*) FROM public.{table}")
        ).fetchone()
        result["count"] = count_row[0] if count_row else 0

        # Sample (sempre)
        sample_rows = conn.execute(
            text(f"SELECT * FROM public.{table} LIMIT {sample_size}")
        ).mappings().fetchall()
        result["sample"] = [dict(r) for r in sample_rows]

        if dry_run:
            print(
                f"    [dry_run] {table}: {result['count']:,} registros | "
                f"colunas: {result['columns']}"
            )
        else:
            # Extração completa
            print(f"    Extraindo {table}: {result['count']:,} registros...")
            all_rows = conn.execute(
                text(f"SELECT * FROM public.{table}")
            ).mappings().fetchall()

            id_col = APP3_ID_COLUMNS.get(table, "id")
            records = []
            for row in all_rows:
                r = dict(row)
                r["_source_system"] = "app3"
                r["_source_table"] = table
                r["_source_id"] = str(r.get(id_col, ""))
                records.append(r)

            result["records"] = records
            print(f"    ✅ {table}: {len(records):,} registros extraídos")

    except Exception as e:  # noqa: BLE001
        result["warnings"].append(f"Erro ao extrair '{table}': {type(e).__name__}: {e}")
        print(f"    ❌ Erro em '{table}': {type(e).__name__}: {e}")

    return result


# ---------------------------------------------------------------------------
# Extração principal
# ---------------------------------------------------------------------------

def extract(cfg) -> dict[str, Any]:  # noqa: ANN001
    """Extrai todas as tabelas do App 3."""
    from migration.config import make_engine  # noqa: PLC0415

    summary: dict[str, Any] = {
        "source": "app3_controle_financeiro",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": cfg.dry_run,
        "tables": {},
        "total_records": 0,
        "warnings": [],
    }

    if not cfg.app3_url:
        summary["warnings"].append(
            "SUPABASE_ORIGEM_CONTROLE_URL não configurado. "
            "Extração do App 3 ignorada."
        )
        print("  ⚠️  SUPABASE_ORIGEM_CONTROLE_URL ausente — extração simulada.")
        return summary

    try:
        engine = make_engine(cfg.app3_url, source_label="app3_controle_financeiro")

        with engine.connect() as conn:
            print(f"  Conectado ao App 3. Extraindo {len(APP3_TABLES)} tabelas...\n")

            for table in APP3_TABLES:
                table_result = extract_table(conn, table, cfg.dry_run)
                summary["tables"][table] = table_result
                summary["total_records"] += table_result.get("count", 0)

                if table_result.get("warnings"):
                    summary["warnings"].extend(table_result["warnings"])

    except Exception as e:  # noqa: BLE001
        summary["warnings"].append(
            f"Erro de conexão ao App 3: {type(e).__name__}"
        )
        print(f"  ❌ Erro de conexão ao App 3: {type(e).__name__}")
        print("     Verifique SUPABASE_ORIGEM_CONTROLE_URL.")

    return summary


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def save_result(summary: dict, cfg) -> list[Path]:  # noqa: ANN001
    """Salva extração em arquivos JSON por tabela."""
    cfg.ensure_output_dir()
    saved: list[Path] = []

    # Salvar sumário geral
    summary_path = cfg.output_path("02_app3_summary.json")
    # Remove registros completos do sumário para não duplicar
    summary_clean = {
        k: v for k, v in summary.items() if k != "tables"
    }
    summary_clean["table_counts"] = {
        t: d.get("count", 0) for t, d in summary.get("tables", {}).items()
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_clean, f, ensure_ascii=False, indent=2, default=str)
    saved.append(summary_path)

    # Salvar por tabela
    for table, data in summary.get("tables", {}).items():
        table_path = cfg.output_path(f"02_app3_{table}.json")
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
    print("\n" + "=" * 55)
    print("  RELATÓRIO — Extração App 3 (Controle Financeiro)")
    print("=" * 55)
    print(f"  Total de registros: {summary.get('total_records', 0):,}")
    print()

    for table, data in summary.get("tables", {}).items():
        count = data.get("count", 0)
        cols = len(data.get("columns", []))
        warns = len(data.get("warnings", []))
        status = "⚠️" if warns else "✅"
        print(f"  {status}  {table:<20} {count:>6,} registros  ({cols} colunas)")

    if summary.get("warnings"):
        print("\n  Avisos:")
        for w in summary["warnings"]:
            print(f"    ⚠️  {w}")

    if summary.get("dry_run"):
        print("\n  ℹ️  dry_run=True: apenas contagens extraídas.")
        print("     Execute com --no-dry-run para extrair registros completos.")

    print("=" * 55)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrai dados do Supabase Controle Financeiro (App 3)"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Extrai registros completos (padrão: dry_run=True, apenas contagens)",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from migration.config import MigrationConfig  # noqa: PLC0415

    cfg = MigrationConfig.from_env(dry_run=not args.no_dry_run)
    cfg.print_summary()

    print("\n📥 Iniciando extração — App 3 (Controle Financeiro)...")
    summary = extract(cfg)
    save_result(summary, cfg)
    print_report(summary)

    return 0 if not summary.get("warnings") else 1


if __name__ == "__main__":
    sys.exit(main())

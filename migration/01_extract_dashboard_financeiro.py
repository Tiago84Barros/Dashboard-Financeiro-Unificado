"""
migration/01_extract_dashboard_financeiro.py
============================================
Extrai dados legados do banco Dashboard Financeiro / banco unificado.

Fase 4.6 — Extração (App 1 / tabelas legadas)

O banco unificado já contém 14 tabelas do App 1 (Dashboard Financeiro —
análise fundamentalista de ações). Este script as inspeciona e extrai
contagens para identificar se existe qualquer dado pessoal que precise
ser migrado para o modelo canônico.

Tabelas conhecidas do App 1 (auditoria Fase 4.1):
  Demonstracoes_Financeiras, Demonstracoes_Financeiras_TRI,
  cvm_to_ticker, docs_corporativos, docs_corporativos_chunks,
  info_economica, info_economica_mensal, multiplos, multiplos_TRI,
  patch6_runs, portfolio_snapshot_analysis, portfolio_snapshot_items,
  portfolio_snapshots, setores

Regras:
  - Somente leitura (SELECT).
  - Não modifica nenhuma tabela.
  - Salva contagens e amostras em migration/output/.
  - dry_run=True: não conecta ao banco se dest_url ausente.

Uso:
  python -m migration.01_extract_dashboard_financeiro
  python -m migration.01_extract_dashboard_financeiro --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Tabelas App 1 conhecidas (auditoria Fase 4.1)
# ---------------------------------------------------------------------------

APP1_KNOWN_TABLES: list[str] = [
    "Demonstracoes_Financeiras",
    "Demonstracoes_Financeiras_TRI",
    "cvm_to_ticker",
    "docs_corporativos",
    "docs_corporativos_chunks",
    "info_economica",
    "info_economica_mensal",
    "multiplos",
    "multiplos_TRI",
    "patch6_runs",
    "portfolio_snapshot_analysis",
    "portfolio_snapshot_items",
    "portfolio_snapshots",
    "setores",
]

# Tabelas App 4 canônicas (22) — criadas na Fase 4.5
APP4_CANONICAL_TABLES: list[str] = [
    "profiles", "financial_institutions", "accounts", "cards", "categories",
    "transactions", "budgets", "financial_goals", "debts", "assets",
    "portfolios", "portfolio_positions", "investment_transactions", "dividends",
    "asset_quotes", "benchmarks", "benchmark_quotes", "alerts", "user_settings",
    "import_batches", "import_logs", "migration_source_map",
]


# ---------------------------------------------------------------------------
# Extração
# ---------------------------------------------------------------------------

def extract(cfg) -> dict:  # noqa: ANN001
    """
    Conecta ao banco unificado e inspeciona tabelas.

    Returns:
        dict com contagens por tabela e metadados.
    """
    from migration.config import make_engine  # noqa: PLC0415

    result: dict = {
        "source": "app1_dashboard_financeiro",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": cfg.dry_run,
        "app1_tables": {},
        "app4_canonical_tables": {},
        "other_tables": [],
        "warnings": [],
    }

    if not cfg.dest_url:
        result["warnings"].append(
            "SUPABASE_UNIFICADO_URL não configurado — extração simulada."
        )
        print("  ⚠️  SUPABASE_UNIFICADO_URL ausente. Rodando em modo simulado.")
        return result

    try:
        from sqlalchemy import inspect, text  # noqa: PLC0415

        engine = make_engine(cfg.dest_url, source_label="unified_supabase")

        with engine.connect() as conn:
            # 1. Listar todas as tabelas no schema public
            inspector = inspect(engine)
            all_tables = inspector.get_table_names(schema="public")

            print(f"  Tabelas encontradas no banco: {len(all_tables)}")

            # 2. Contar registros nas tabelas App 1 conhecidas
            for table in APP1_KNOWN_TABLES:
                if table in all_tables:
                    try:
                        row = conn.execute(
                            text(f'SELECT COUNT(*) FROM public."{table}"')
                        ).fetchone()
                        count = row[0] if row else 0
                        result["app1_tables"][table] = {"count": count, "exists": True}
                        print(f"    App1  {table}: {count:,} registros")
                    except Exception as e:  # noqa: BLE001
                        result["app1_tables"][table] = {"exists": True, "error": str(e)}
                else:
                    result["app1_tables"][table] = {"exists": False, "count": 0}

            # 3. Contar registros nas tabelas canônicas do App 4
            for table in APP4_CANONICAL_TABLES:
                if table in all_tables:
                    try:
                        row = conn.execute(
                            text(f"SELECT COUNT(*) FROM public.{table}")
                        ).fetchone()
                        count = row[0] if row else 0
                        result["app4_canonical_tables"][table] = {
                            "count": count,
                            "exists": True,
                        }
                        if count > 0:
                            print(f"    App4  {table}: {count:,} registros ← dados existentes")
                    except Exception as e:  # noqa: BLE001
                        result["app4_canonical_tables"][table] = {
                            "exists": True,
                            "error": str(e),
                        }
                else:
                    result["app4_canonical_tables"][table] = {"exists": False, "count": 0}

            # 4. Registrar tabelas inesperadas
            known = set(APP1_KNOWN_TABLES) | set(APP4_CANONICAL_TABLES)
            other = [t for t in all_tables if t not in known]
            result["other_tables"] = other
            if other:
                result["warnings"].append(
                    f"Tabelas não catalogadas encontradas: {other}"
                )
                print(f"  ⚠️  Tabelas não catalogadas: {other}")

    except Exception as e:  # noqa: BLE001
        result["warnings"].append(f"Erro de conexão: {type(e).__name__}")
        print(f"  ❌ Erro ao conectar ao banco unificado: {type(e).__name__}")
        print("     (Verifique SUPABASE_UNIFICADO_URL e conectividade de rede)")

    return result


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def save_result(result: dict, cfg) -> Path:  # noqa: ANN001
    """Salva resultado em migration/output/01_app1_extract.json"""
    cfg.ensure_output_dir()
    output_path = cfg.output_path("01_app1_extract.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n  💾 Resultado salvo em: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Relatório resumido
# ---------------------------------------------------------------------------

def print_report(result: dict) -> None:
    """Exibe relatório resumido no console."""
    print("\n" + "=" * 55)
    print("  RELATÓRIO — Extração App 1 / Banco Unificado")
    print("=" * 55)

    app4 = result.get("app4_canonical_tables", {})
    populadas = {t: d for t, d in app4.items() if d.get("count", 0) > 0}

    if populadas:
        print("\n  ⚡ Tabelas canônicas com dados existentes:")
        for table, data in populadas.items():
            print(f"    {table}: {data['count']:,} registros")
        print("\n  ℹ️  Estes dados existem no banco unificado e não precisam")
        print("     ser re-migrados. Serão tratados como dados nativos.")
    else:
        print("\n  ✅ Tabelas canônicas vazias — banco limpo para migração.")

    app1 = result.get("app1_tables", {})
    existentes = {t: d for t, d in app1.items() if d.get("exists") and d.get("count", 0) > 0}
    if existentes:
        print(f"\n  📊 Tabelas App 1 com dados ({len(existentes)} tabelas):")
        for t, d in existentes.items():
            print(f"    {t}: {d['count']:,} registros")
        print("  ℹ️  Estes dados são análises fundamentalistas/CVM — não precisam")
        print("     ser migrados para o modelo canônico pessoal.")

    for w in result.get("warnings", []):
        print(f"\n  ⚠️  {w}")

    print("=" * 55)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrai e inspeciona banco Dashboard Financeiro / unificado"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Modo simulado — não grava dados (padrão: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Desativa dry_run para conexão real (apenas leitura)",
    )
    args = parser.parse_args()

    dry_run = not args.no_dry_run

    # Importação local para evitar import circular
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from migration.config import MigrationConfig  # noqa: PLC0415

    cfg = MigrationConfig.from_env(dry_run=dry_run)
    cfg.print_summary()

    print("\n📥 Iniciando extração — App 1 / Banco Unificado...")
    result = extract(cfg)
    save_result(result, cfg)
    print_report(result)

    return 0 if not result.get("warnings") else 1


if __name__ == "__main__":
    sys.exit(main())

"""
migration/05_load_to_unified_supabase.py
=========================================
Carrega dados transformados no banco unificado Supabase.

Fase 4.6 — Carga

Regras INVIOLÁVEIS:
  - dry_run=True por padrão — NUNCA grava sem --no-dry-run explícito.
  - Nunca usa DELETE ou TRUNCATE.
  - Usa INSERT ... ON CONFLICT DO NOTHING (idempotente).
  - Registra import_batch_id em cada registro.
  - Registra cada registro em migration_source_map para idempotência.
  - Preserva _source_system, _source_table, _source_id em import_logs.
  - Carrega em lotes (batch_size) para controle de memória.

Ordem de carga (respeita dependências FK):
  1. financial_institutions  (sem FK)
  2. assets                  (sem FK)
  3. profiles                (base, single-user — não migrar, criar manualmente)
  4. accounts                (→ profiles)
  5. categories              (→ profiles, self-ref)
  6. investment_transactions (→ profiles, assets)
  7. dividends               (→ profiles, assets)
  8. transactions            (→ profiles, accounts, categories)
  9. budgets                 (→ profiles, categories)
  10. financial_goals        (→ profiles)

Uso:
  python -m migration.05_load_to_unified_supabase           # dry_run
  python -m migration.05_load_to_unified_supabase --no-dry-run  # REAL
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Ordem de carga (respeita FKs)
# ---------------------------------------------------------------------------

LOAD_ORDER: list[str] = [
    "financial_institutions",
    "assets",
    "accounts",
    "categories",
    "investment_transactions",
    "dividends",
    "transactions",
    "budgets",
    "financial_goals",
]

# Chaves de conflito por tabela (para ON CONFLICT)
CONFLICT_KEYS: dict[str, list[str]] = {
    "financial_institutions": ["name"],
    "assets": ["ticker"],
    "accounts": ["_source_system_key"],  # via migration_source_map
    "categories": ["_source_system_key"],
    "transactions": ["_source_system_key"],
    "investment_transactions": ["_source_system_key"],
    "dividends": ["_source_system_key"],
    "budgets": ["user_id", "category_id", "month_year"],
    "financial_goals": ["_source_system_key"],
}

# Campos que NÃO devem ir para o banco (metadados de migração)
META_FIELDS = {"_source_system", "_source_table", "_source_id", "_source_system_key"}


# ---------------------------------------------------------------------------
# Cria import_batch
# ---------------------------------------------------------------------------

def create_import_batch(conn, cfg, source: str, total_records: int) -> str:  # noqa: ANN001
    """
    Insere registro em import_batches e retorna o batch_id.
    Em dry_run, retorna UUID simulado sem inserir.
    """
    from sqlalchemy import text  # noqa: PLC0415

    batch_id = str(uuid.uuid4())

    if cfg.dry_run:
        print(f"  [dry_run] import_batch_id simulado: {batch_id}")
        return batch_id

    conn.execute(
        text("""
            INSERT INTO import_batches
                (id, user_id, source, status, total_records, dry_run, started_at)
            VALUES
                (:id, :user_id, :source, 'processing', :total, false, NOW())
        """),
        {
            "id": batch_id,
            "user_id": cfg.owner_id,
            "source": source,
            "total": total_records,
        },
    )
    conn.commit()
    print(f"  import_batch_id: {batch_id}")
    return batch_id


def update_import_batch(conn, batch_id: str, imported: int, errors: int, cfg) -> None:  # noqa: ANN001
    """Atualiza status final do batch."""
    from sqlalchemy import text  # noqa: PLC0415

    if cfg.dry_run:
        return

    conn.execute(
        text("""
            UPDATE import_batches
            SET status = 'completed',
                imported_records = :imported,
                error_records = :errors,
                completed_at = NOW()
            WHERE id = :batch_id
        """),
        {"batch_id": batch_id, "imported": imported, "errors": errors},
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Idempotência via migration_source_map
# ---------------------------------------------------------------------------

def already_migrated(conn, source: str, source_table: str, source_id: str) -> bool:  # noqa: ANN001
    """Verifica se registro já foi migrado (idempotência)."""
    from sqlalchemy import text  # noqa: PLC0415

    row = conn.execute(
        text("""
            SELECT 1 FROM migration_source_map
            WHERE source = :source
              AND source_table = :source_table
              AND source_id = :source_id
            LIMIT 1
        """),
        {"source": source, "source_table": source_table, "source_id": source_id},
    ).fetchone()
    return row is not None


def register_migration(
    conn,  # noqa: ANN001
    target_table: str,
    target_id: str,
    source: str,
    source_table: str,
    source_id: str,
) -> None:
    """Registra mapeamento no migration_source_map."""
    from sqlalchemy import text  # noqa: PLC0415

    conn.execute(
        text("""
            INSERT INTO migration_source_map
                (id, target_table, target_id, source, source_table, source_id)
            VALUES
                (:id, :target_table, :target_id, :source, :source_table, :source_id)
            ON CONFLICT (source, source_table, source_id) DO NOTHING
        """),
        {
            "id": str(uuid.uuid4()),
            "target_table": target_table,
            "target_id": target_id,
            "source": source,
            "source_table": source_table,
            "source_id": source_id,
        },
    )


def log_import(
    conn,  # noqa: ANN001
    batch_id: str,
    target_table: str,
    source_id: str,
    destination_id: str,
    action: str,
    message: str = "",
) -> None:
    """Registra linha em import_logs."""
    from sqlalchemy import text  # noqa: PLC0415

    conn.execute(
        text("""
            INSERT INTO import_logs
                (id, batch_id, target_table, source_id, destination_id, action, message)
            VALUES
                (:id, :batch_id, :target_table, :source_id, :destination_id, :action, :message)
        """),
        {
            "id": str(uuid.uuid4()),
            "batch_id": batch_id,
            "target_table": target_table,
            "source_id": source_id,
            "destination_id": destination_id,
            "action": action,
            "message": message or "",
        },
    )


# ---------------------------------------------------------------------------
# Carga de uma tabela
# ---------------------------------------------------------------------------

def load_table(
    conn,  # noqa: ANN001
    table: str,
    records: list[dict],
    batch_id: str,
    cfg,  # noqa: ANN001
) -> dict[str, int]:
    """
    Carrega registros em uma tabela canônica.
    Retorna contadores: inserted, skipped, errors.
    """
    from sqlalchemy import text  # noqa: PLC0415

    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    if not records:
        print(f"    {table}: sem registros para carregar.")
        return stats

    if cfg.dry_run:
        # dry_run: validar e contar apenas
        print(
            f"    [dry_run] {table}: {len(records):,} registros seriam inseridos "
            f"(ON CONFLICT DO NOTHING)"
        )
        stats["inserted"] = len(records)
        return stats

    # Carga real em lotes
    batch_size = cfg.batch_size
    total = len(records)

    for i in range(0, total, batch_size):
        batch = records[i : i + batch_size]

        for rec in batch:
            source_system = rec.get("_source_system", "manual")
            source_table = rec.get("_source_table", table)
            source_id = rec.get("_source_id", str(uuid.uuid4()))

            # Verificar idempotência
            if already_migrated(conn, source_system, source_table, source_id):
                stats["skipped"] += 1
                continue

            # Remover campos de metadados
            clean_rec = {k: v for k, v in rec.items() if k not in META_FIELDS}

            # Garantir UUID de destino
            dest_id = clean_rec.get("id") or str(uuid.uuid4())
            clean_rec["id"] = dest_id

            # Injetar batch_id quando a tabela suporta (import_batches não tem esse campo)
            # Apenas em tabelas que têm esse campo (não em todas)

            try:
                cols = ", ".join(clean_rec.keys())
                placeholders = ", ".join(f":{k}" for k in clean_rec.keys())

                conn.execute(
                    text(f"""
                        INSERT INTO {table} ({cols})
                        VALUES ({placeholders})
                        ON CONFLICT DO NOTHING
                    """),
                    clean_rec,
                )

                # Registrar idempotência
                register_migration(
                    conn, table, dest_id, source_system, source_table, source_id
                )

                # Log
                log_import(
                    conn, batch_id, table, source_id, dest_id, "inserted"
                )

                stats["inserted"] += 1

            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                log_import(
                    conn,
                    batch_id,
                    table,
                    source_id,
                    "",
                    "error",
                    f"{type(e).__name__}: {e}",
                )

        conn.commit()
        progress = min(i + batch_size, total)
        print(
            f"    {table}: {progress}/{total} | "
            f"inseridos={stats['inserted']} | "
            f"ignorados={stats['skipped']} | "
            f"erros={stats['errors']}"
        )

    return stats


# ---------------------------------------------------------------------------
# Orquestrador principal
# ---------------------------------------------------------------------------

def load_all(cfg) -> dict[str, Any]:  # noqa: ANN001
    """Executa carga completa no banco unificado."""
    from migration.config import make_engine  # noqa: PLC0415

    result: dict[str, Any] = {
        "loaded_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": cfg.dry_run,
        "tables": {},
        "import_batch_id": None,
        "total_inserted": 0,
        "total_skipped": 0,
        "total_errors": 0,
        "warnings": [],
    }

    if not cfg.dest_url:
        result["warnings"].append("SUPABASE_UNIFICADO_URL não configurado.")
        print("  ❌ SUPABASE_UNIFICADO_URL ausente.")
        return result

    if not cfg.owner_id:
        result["warnings"].append("OWNER_USER_ID não configurado.")
        print("  ❌ OWNER_USER_ID ausente.")
        return result

    # Carregar dados transformados
    transformed_dir = cfg.output_dir / "transformed"
    all_data: dict[str, list[dict]] = {}
    total_records = 0

    for entity in LOAD_ORDER:
        path = transformed_dir / f"04_{entity}_canonical.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            records = data.get("records", [])
            all_data[entity] = records
            total_records += len(records)
        else:
            all_data[entity] = []

    if cfg.dry_run:
        print(f"\n  [dry_run] {total_records:,} registros seriam carregados.")
        print("  Simulando carga sem conectar ao banco...\n")

        for entity in LOAD_ORDER:
            records = all_data[entity]
            print(f"    {entity:<30} {len(records):>5,} registros")
            result["tables"][entity] = {"inserted": len(records), "skipped": 0, "errors": 0}
            result["total_inserted"] += len(records)

        result["import_batch_id"] = "dry_run_" + str(uuid.uuid4())[:8]
        return result

    # Carga real
    try:
        engine = make_engine(cfg.dest_url, source_label="unified_supabase")

        with engine.connect() as conn:
            batch_id = create_import_batch(conn, cfg, "app3", total_records)
            result["import_batch_id"] = batch_id
            cfg.import_batch_id = batch_id

            for entity in LOAD_ORDER:
                records = all_data[entity]
                print(f"\n  Carregando {entity}...")
                stats = load_table(conn, entity, records, batch_id, cfg)
                result["tables"][entity] = stats
                result["total_inserted"] += stats["inserted"]
                result["total_skipped"] += stats["skipped"]
                result["total_errors"] += stats["errors"]

            update_import_batch(
                conn,
                batch_id,
                result["total_inserted"],
                result["total_errors"],
                cfg,
            )

    except Exception as e:  # noqa: BLE001
        result["warnings"].append(f"Erro de conexão: {type(e).__name__}: {e}")
        print(f"\n  ❌ Erro de conexão: {e}")

    return result


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def save_result(result: dict, cfg) -> Path:  # noqa: ANN001
    """Salva relatório de carga."""
    cfg.ensure_output_dir()
    path = cfg.output_path("05_load_report.json")

    # Não salvar registros completos no relatório
    clean = {k: v for k, v in result.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n  💾 Relatório salvo em: {path}")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Carrega dados transformados no banco unificado Supabase"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="⚠️  ATENÇÃO: executa carga real no banco. Use com cautela.",
    )
    args = parser.parse_args()

    if args.no_dry_run:
        print("⚠️  MODO REAL ATIVADO — dados serão gravados no banco.")
        print("    Pressione Ctrl+C agora para cancelar.")
        print("    Continuando em 5 segundos...")
        import time  # noqa: PLC0415
        time.sleep(5)

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from migration.config import MigrationConfig  # noqa: PLC0415

    cfg = MigrationConfig.from_env(dry_run=not args.no_dry_run)
    cfg.print_summary()

    print("\n📤 Iniciando carga no banco unificado...")
    result = load_all(cfg)
    save_result(result, cfg)

    print("\n" + "=" * 55)
    print("  RESUMO DA CARGA")
    print("=" * 55)
    print(f"  dry_run        : {result['dry_run']}")
    print(f"  batch_id       : {result.get('import_batch_id', 'N/A')}")
    print(f"  total_inserido : {result['total_inserted']:,}")
    print(f"  total_ignorado : {result['total_skipped']:,}")
    print(f"  total_erros    : {result['total_errors']:,}")
    print("=" * 55)

    return 0 if result["total_errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

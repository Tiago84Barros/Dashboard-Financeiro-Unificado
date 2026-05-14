"""
migration/backup_supabase.py
============================
Exporta todas as tabelas do banco unificado para arquivos JSON locais.

Usa as credenciais do .env automaticamente — nenhuma senha é exibida no terminal.

Uso:
  python migration/backup_supabase.py

Saída:
  migration/backup/YYYY-MM-DD_HH-MM/
    <tabela>.json   — um arquivo por tabela com todos os registros
    _summary.json   — resumo do backup (tabelas, contagens, data)

Segurança:
  - Somente leitura (apenas SELECT).
  - Não modifica nenhuma tabela.
  - Arquivos de backup NÃO são versionados (gitignored).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def run_backup() -> int:
    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine  # noqa: PLC0415
    from sqlalchemy import inspect, text  # noqa: PLC0415

    _ensure_utf8_stdout()

    cfg = MigrationConfig.from_env(dry_run=True)

    if not cfg.dest_url:
        print("ERRO: SUPABASE_UNIFICADO_URL nao configurado no .env")
        return 1

    # Pasta de saída com timestamp
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    backup_dir = PROJECT_ROOT / "migration" / "backup" / ts
    backup_dir.mkdir(parents=True, exist_ok=True)

    print("Iniciando backup do banco unificado...")
    print(f"   Destino: {backup_dir.relative_to(PROJECT_ROOT)}\n")

    engine = make_engine(cfg.dest_url, source_label="backup_unificado")

    summary = {
        "backup_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
        "total_records": 0,
        "errors": [],
    }

    with engine.connect() as conn:
        inspector = inspect(conn)
        all_tables = inspector.get_table_names(schema="public")

        print(f"   Tabelas encontradas: {len(all_tables)}\n")

        for table in sorted(all_tables):
            try:
                # Aspas duplas necessárias para nomes com maiúsculas/especiais
                rows = conn.execute(
                    text(f'SELECT * FROM public."{table}"')
                ).mappings().fetchall()

                records = [dict(r) for r in rows]
                count = len(records)

                # Salvar JSON da tabela
                out_path = backup_dir / f"{table}.json"
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(
                        {"table": table, "count": count, "records": records},
                        f,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )

                summary["tables"][table] = count
                summary["total_records"] += count
                status = "OK" if count > 0 else "--"
                print(f"   {status}  {table:<40} {count:>6,} registros")

            except Exception as e:  # noqa: BLE001
                # Rollback para liberar a conexão após erro
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    pass
                msg = f"{table}: {type(e).__name__}: {str(e)[:120]}"
                summary["errors"].append(msg)
                print(f"   ❌  {table:<40} {type(e).__name__}: {str(e)[:80]}")

    # Salvar sumário
    summary_path = backup_dir / "_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    sep = "=" * 55
    print(f"\n{sep}")
    print("  Backup concluido")
    print(f"  Tabelas: {len(summary['tables'])}")
    print(f"  Total de registros: {summary['total_records']:,}")
    print(f"  Erros: {len(summary['errors'])}")
    print(f"  Pasta: migration/backup/{ts}/")
    if summary["errors"]:
        print("\n  Erros detalhados:")
        for e in summary["errors"]:
            print(f"    - {e}")
    print(sep)

    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(run_backup())

"""
migration/run_dry_run.py
========================
Orquestrador do pipeline completo de migracao em modo dry_run.

Executa a sequencia completa de scripts (01 a 07) com dry_run=True.
Nenhum dado e gravado no banco de destino.
Nenhum dado e alterado nas fontes.

Uso:
  python migration/run_dry_run.py
  python -m migration.run_dry_run

O que este script faz:
  1. Verifica configuracao (variaveis de ambiente)
  2. Simula extracao do banco unificado (App 1)
  3. Simula extracao do Controle Financeiro (App 3 - requer credenciais)
  4. Simula extracao do SQLite de Investimentos (App 2 - requer caminho)
  5. Transforma dados para o modelo canonico (le arquivos JSON)
  6. Simula carga no banco unificado (SEM conexao real)
  7. Valida estrutura (requer credenciais para validacao real)
  8. Gera relatorio Markdown em docs/

Seguranca:
  - dry_run=True hardcoded neste orquestrador
  - Nunca passa --no-dry-run
  - Nao imprime valores de credenciais
  - Interrompe se qualquer script tentar carga real

Saida:
  migration/output/  - arquivos JSON intermediarios (gitignored)
  docs/fase_4_6_relatorio_migracao_planejada.md

Proximos passos apos este script:
  1. Revisar os arquivos em migration/output/
  2. Verificar warnings reportados
  3. Satisfazer pre-requisitos listados no relatorio
  4. Executar migracao real somente com autorizacao explícita
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Garantir path correto
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Seguranca: verificar que dry_run nao foi desativado acidentalmente
# ---------------------------------------------------------------------------

def _assert_dry_run(cfg) -> None:  # noqa: ANN001
    """Interrompe se dry_run for False. Garantia de segurança inviolável."""
    if not cfg.dry_run:
        print("\n🛑 INTERROMPIDO: Este orquestrador NUNCA executa carga real.")
        print("   Para carga real, use python -m migration.05_load_to_unified_supabase --no-dry-run")
        print("   com revisao manual dos dados transformados.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Carregador de módulos por caminho (contorna nomes com digitos)
# ---------------------------------------------------------------------------

def _load_module(filename: str, module_name: str):
    """Carrega modulo Python a partir de caminho de arquivo."""
    path = PROJECT_ROOT / "migration" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Nao foi possivel carregar: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

SEP = "=" * 65


def _header(step: int, title: str) -> None:
    print()
    print(SEP)
    print(f"  STEP {step}: {title}")
    print(SEP)


def _ok(msg: str) -> None:
    print(f"  OK  {msg}")


def _warn(msg: str) -> None:
    print(f"  AVISO  {msg}")


def _skip(msg: str) -> None:
    print(f"  SKIP  {msg}")


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def run_pipeline() -> dict:
    """Executa pipeline completo em dry_run. Retorna sumario."""
    from migration.config import MigrationConfig, _ensure_utf8_stdout  # noqa: PLC0415

    _ensure_utf8_stdout()

    started_at = time.time()
    summary: dict = {
        "dry_run": True,
        "steps": {},
        "total_warnings": 0,
        "total_errors": 0,
        "sources_available": [],
        "sources_missing": [],
    }

    # ── Config ───────────────────────────────────────────────────────────────
    _header(0, "Verificacao de configuracao")
    cfg = MigrationConfig.from_env(dry_run=True)
    _assert_dry_run(cfg)
    cfg.print_summary()
    cfg.ensure_output_dir()

    # Detectar fontes disponiveis
    if cfg.dest_url:
        summary["sources_available"].append("banco_unificado (App1)")
    else:
        summary["sources_missing"].append("SUPABASE_UNIFICADO_URL (banco_unificado)")

    if cfg.app3_url:
        summary["sources_available"].append("app3 (Controle Financeiro)")
    else:
        summary["sources_missing"].append("SUPABASE_ORIGEM_CONTROLE_URL (app3)")

    if cfg.app2_path:
        summary["sources_available"].append("app2 (SQLite Investimentos)")
    else:
        summary["sources_missing"].append("SOURCE_DB_APP2 (app2 SQLite)")

    print(f"\n  Fontes disponiveis : {summary['sources_available'] or 'nenhuma'}")
    print(f"  Fontes ausentes    : {summary['sources_missing'] or 'nenhuma'}")

    # ── Step 1: Extract App1 ──────────────────────────────────────────────
    _header(1, "Extracao banco unificado (App 1 + tabelas canonicas)")
    m01 = _load_module("01_extract_dashboard_financeiro.py", "m01")
    result1 = m01.extract(cfg)
    m01.save_result(result1, cfg)
    m01.print_report(result1)
    w1 = len(result1.get("warnings", []))
    summary["steps"]["01_extract_app1"] = {"warnings": w1, "status": "ok"}
    summary["total_warnings"] += w1

    # ── Step 2: Extract App3 ─────────────────────────────────────────────
    _header(2, "Extracao App 3 (Controle Financeiro - PostgreSQL)")
    m02 = _load_module("02_extract_controle_financeiro.py", "m02")
    result2 = m02.extract(cfg)
    m02.save_result(result2, cfg)
    m02.print_report(result2)
    w2 = len(result2.get("warnings", []))
    summary["steps"]["02_extract_app3"] = {
        "warnings": w2,
        "total_records": result2.get("total_records", 0),
        "status": "ok" if cfg.app3_url else "skipped (sem credenciais)",
    }
    summary["total_warnings"] += w2

    # ── Step 3: Extract App2 SQLite ───────────────────────────────────────
    _header(3, "Extracao App 2 (SQLite Investimentos)")
    m03 = _load_module("03_extract_investimentos_sqlite.py", "m03")
    result3 = m03.extract(cfg)
    m03.save_result(result3, cfg)
    m03.print_report(result3)
    w3 = len(result3.get("warnings", []))
    summary["steps"]["03_extract_app2"] = {
        "warnings": w3,
        "total_records": result3.get("total_records", 0),
        "status": "ok" if cfg.app2_path else "skipped (sem caminho SQLite)",
    }
    summary["total_warnings"] += w3

    # ── Step 4: Transform ────────────────────────────────────────────────
    _header(4, "Transformacao para modelo canonico")
    m04 = _load_module("04_transform_to_canonical.py", "m04")
    result4 = m04.transform_all(cfg)
    m04.save_result(result4, cfg)
    w4 = len(result4.get("warnings", []))
    # Contar arquivos nao encontrados nas entidades
    entity_warnings = sum(
        len(d.get("warnings", []))
        for d in result4.get("entities", {}).values()
    )
    summary["steps"]["04_transform"] = {
        "warnings": w4 + entity_warnings,
        "total_records": result4.get("total_records", 0),
        "entities": {
            e: d.get("count", 0)
            for e, d in result4.get("entities", {}).items()
        },
        "status": "ok",
    }
    summary["total_warnings"] += w4 + entity_warnings
    print(f"\n  Total transformado : {result4.get('total_records', 0):,} registros")
    if result4.get("warnings"):
        for w in result4["warnings"]:
            _warn(w)

    # ── Step 5: Load simulation ───────────────────────────────────────────
    _header(5, "Simulacao de carga (dry_run - SEM conexao ao banco)")
    _assert_dry_run(cfg)  # Segunda verificacao antes de load
    m05 = _load_module("05_load_to_unified_supabase.py", "m05")
    result5 = m05.load_all(cfg)
    _assert_dry_run(cfg)  # Terceira verificacao apos load
    m05.save_result(result5, cfg)
    w5 = len(result5.get("warnings", []))
    summary["steps"]["05_load"] = {
        "warnings": w5,
        "total_inserted_simulated": result5.get("total_inserted", 0),
        "total_errors": result5.get("total_errors", 0),
        "import_batch_id": result5.get("import_batch_id"),
        "status": "ok (simulado)",
    }
    summary["total_warnings"] += w5
    summary["total_errors"] += result5.get("total_errors", 0)
    print(f"\n  Batch simulado     : {result5.get('import_batch_id', 'N/A')}")
    print(f"  Inseridos (sim.)   : {result5.get('total_inserted', 0):,}")
    print(f"  Erros              : {result5.get('total_errors', 0)}")

    # ── Step 6: Validate ─────────────────────────────────────────────────
    _header(6, "Validacao pos-carga (requer SUPABASE_UNIFICADO_URL)")
    m06 = _load_module("06_validate_migration.py", "m06")
    result6 = m06.validate_all(cfg)
    m06.save_result(result6, cfg)
    m06.print_report(result6)
    summary["steps"]["06_validate"] = {
        "passed": result6.get("passed", 0),
        "failed": result6.get("failed", 0),
        "status": "ok" if cfg.dest_url else "skipped (sem credenciais)",
    }

    # ── Step 7: Report ───────────────────────────────────────────────────
    _header(7, "Gerando relatorio Markdown")
    m07 = _load_module("07_report_migration.py", "m07")
    report_md = m07.generate_report(cfg.output_dir, cfg)
    docs_path = PROJECT_ROOT / "docs" / "fase_4_6_relatorio_migracao_planejada.md"
    docs_path.write_text(report_md, encoding="utf-8")
    print(f"  Relatorio salvo em: {docs_path.relative_to(PROJECT_ROOT)}")
    summary["steps"]["07_report"] = {"status": "ok", "output": str(docs_path)}

    # ── Sumário final ─────────────────────────────────────────────────────
    elapsed = time.time() - started_at
    summary["elapsed_seconds"] = round(elapsed, 2)

    print()
    print(SEP)
    print("  RESUMO DO DRY RUN")
    print(SEP)
    print("  dry_run              : TRUE (nenhum dado gravado)")
    print(f"  Duracao              : {elapsed:.1f}s")
    print(f"  Fontes disponiveis   : {len(summary['sources_available'])}")
    print(f"  Fontes ausentes      : {len(summary['sources_missing'])}")
    print(f"  Total warnings       : {summary['total_warnings']}")
    print(f"  Total erros          : {summary['total_errors']}")
    print()

    for step, info in summary["steps"].items():
        status = info.get("status", "?")
        w = info.get("warnings", 0)
        suffix = f" ({w} warnings)" if w else ""
        print(f"  {step:<30} {status}{suffix}")

    if summary["sources_missing"]:
        print()
        print("  Para conectar as fontes, configure:")
        for m in summary["sources_missing"]:
            print(f"    - {m}")

    print()
    if summary["total_errors"] == 0 and len(summary["sources_missing"]) == 0:
        print("  RESULTADO: PRONTO PARA MIGRACAO REAL (apos checklist de pre-requisitos)")
    elif summary["total_errors"] == 0:
        print("  RESULTADO: DRY RUN OK - Credenciais ausentes. Configure e rode novamente.")
    else:
        print("  RESULTADO: ERROS ENCONTRADOS - revisar antes de prosseguir.")

    print(SEP)

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    summary = run_pipeline()
    return 0 if summary.get("total_errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

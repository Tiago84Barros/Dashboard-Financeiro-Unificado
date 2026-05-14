"""
migration/07_report_migration.py
=================================
Gera relatório Markdown da migração planejada.

Fase 4.6 — Relatório

Lê os arquivos JSON de saída dos scripts anteriores e gera:
  docs/fase_4_6_relatorio_migracao_planejada.md

Pode ser executado mesmo sem ter rodado a migração real — usa dados
disponíveis em migration/output/ e gera planejamento baseado nas
extrações de dry_run.

Uso:
  python -m migration.07_report_migration
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers de leitura
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    """Lê arquivo JSON ou retorna dict vazio se não existir."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value or "—")


def _fmt_decimal(value: Any) -> str:
    try:
        from decimal import Decimal  # noqa: PLC0415
        return f"R$ {Decimal(str(value)):,.2f}"
    except Exception:  # noqa: BLE001
        return str(value or "—")


# ---------------------------------------------------------------------------
# Gerador de relatório
# ---------------------------------------------------------------------------

def generate_report(output_dir: Path, cfg) -> str:  # noqa: ANN001
    """Gera string Markdown completa do relatório."""

    # Carregar dados disponíveis
    app1 = _load_json(output_dir / "01_app1_extract.json")
    app3_summary = _load_json(output_dir / "02_app3_summary.json")
    app2_summary = _load_json(output_dir / "03_app2_summary.json")
    transform = _load_json(output_dir / "transformed" / "04_transform_summary.json")
    load_report = _load_json(output_dir / "05_load_report.json")
    validation = _load_json(output_dir / "06_validation_report.json")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dry_run_global = cfg.dry_run

    lines: list[str] = []

    # ── Cabeçalho ──────────────────────────────────────────────────────────
    lines += [
        "# Fase 4.6 — Relatório de Migração Planejada",
        "",
        f"**Gerado em:** {now}  ",
        f"**Modo:** {'🔵 dry_run (planejamento)' if dry_run_global else '🟠 execução real'}  ",
        f"**MOCK_MODE:** true (app continua em modo mock)  ",
        "",
        "---",
        "",
        "## 1. Resumo Executivo",
        "",
    ]

    # Totais
    app3_total = app3_summary.get("total_records", 0)
    app2_total = app2_summary.get("total_records", 0)
    transform_total = transform.get("total_records", 0)
    load_inserted = load_report.get("total_inserted", 0)
    load_errors = load_report.get("total_errors", 0)
    val_passed = validation.get("passed", 0)
    val_failed = validation.get("failed", 0)

    lines += [
        "| Métrica | Valor |",
        "|---------|------:|",
        f"| Registros App 3 (Controle Financeiro) | {_fmt_int(app3_total)} |",
        f"| Registros App 2 (SQLite Investimentos) | {_fmt_int(app2_total)} |",
        f"| Registros transformados | {_fmt_int(transform_total)} |",
        f"| Registros inseridos (load) | {_fmt_int(load_inserted)} |",
        f"| Erros na carga | {_fmt_int(load_errors)} |",
        f"| Validações aprovadas | {val_passed} |",
        f"| Validações com falha | {val_failed} |",
        "",
    ]

    if dry_run_global:
        lines += [
            "> ℹ️  **dry_run=True** — nenhum dado foi gravado. Os números acima",
            "> refletem o que seria migrado. Execute com `--no-dry-run` para migração real.",
            "",
        ]

    # ── Fontes de dados ────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 2. Fontes de Dados",
        "",
        "### 2.1 App 1 — Dashboard Financeiro (banco unificado)",
        "",
    ]

    app4_tables = app1.get("app4_canonical_tables", {})
    populadas = {t: d for t, d in app4_tables.items() if isinstance(d, dict) and d.get("count", 0) > 0}
    if populadas:
        lines.append("Tabelas canônicas com dados existentes no banco:")
        lines.append("")
        lines.append("| Tabela | Registros |")
        lines.append("|--------|----------:|")
        for t, d in populadas.items():
            lines.append(f"| `{t}` | {_fmt_int(d['count'])} |")
        lines.append("")
    else:
        lines += ["Banco canônico vazio — nenhum dado de App 4 existente.", ""]

    # App 3
    lines += [
        "### 2.2 App 3 — Controle Financeiro (PostgreSQL/Supabase)",
        "",
    ]

    app3_tables = app3_summary.get("table_counts", {})
    if app3_tables:
        lines.append("| Tabela (origem) | Registros | Tabela destino |")
        lines.append("|-----------------|----------:|----------------|")
        dest_map = {
            "transacoes": "transactions",
            "contas": "accounts",
            "categorias": "categories",
            "orcamentos": "budgets",
            "metas": "financial_goals",
        }
        for t, count in app3_tables.items():
            dest = dest_map.get(t, t)
            lines.append(f"| `{t}` | {_fmt_int(count)} | `{dest}` |")
        lines.append("")
    else:
        lines += ["> ⚠️  Extração do App 3 não executada ou sem dados.", ""]

    # App 2
    lines += [
        "### 2.3 App 2 — Dashboard Investimentos (SQLite)",
        "",
    ]

    app2_tables = app2_summary.get("table_counts", {})
    if app2_tables:
        dest_map2 = {
            "assets": "assets",
            "institutions": "financial_institutions",
            "accounts": "accounts",
            "transactions": "investment_transactions",
            "incomes": "dividends",
            "xp_positions": "portfolio_positions",
            "position_snapshots": "portfolio_positions (snapshot)",
            "sync_log": "import_logs",
        }
        lines.append("| Tabela (origem) | Registros | Tabela destino |")
        lines.append("|-----------------|----------:|----------------|")
        for t, count in app2_tables.items():
            dest = dest_map2.get(t, t)
            lines.append(f"| `{t}` | {_fmt_int(count)} | `{dest}` |")
        lines.append("")
    else:
        lines += ["> ⚠️  Extração do App 2 não executada ou sem dados.", ""]

    # ── Transformação ──────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 3. Transformação para o Modelo Canônico",
        "",
    ]

    entity_counts = transform.get("entity_counts", {})
    if entity_counts:
        lines.append("| Entidade canônica | Registros transformados |")
        lines.append("|-------------------|------------------------:|")
        for entity, count in entity_counts.items():
            lines.append(f"| `{entity}` | {_fmt_int(count)} |")
        lines.append("")

    transform_warnings = transform.get("warnings", [])
    if transform_warnings:
        lines.append(f"**Avisos de transformação ({len(transform_warnings)}):**")
        lines.append("")
        for w in transform_warnings[:10]:
            lines.append(f"- ⚠️  {w}")
        if len(transform_warnings) > 10:
            lines.append(f"- ... e mais {len(transform_warnings) - 10} avisos.")
        lines.append("")

    # ── Carga ──────────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 4. Carga no Banco Unificado",
        "",
        f"**dry_run:** {load_report.get('dry_run', True)}  ",
        f"**import_batch_id:** `{load_report.get('import_batch_id', 'N/A')}`  ",
        "",
    ]

    load_tables = load_report.get("tables", {})
    if load_tables:
        lines.append("| Tabela | Inseridos | Ignorados | Erros |")
        lines.append("|--------|----------:|----------:|------:|")
        for t, stats in load_tables.items():
            lines.append(
                f"| `{t}` | {_fmt_int(stats.get('inserted', 0))} | "
                f"{_fmt_int(stats.get('skipped', 0))} | "
                f"{_fmt_int(stats.get('errors', 0))} |"
            )
        lines.append("")

    # ── Validação ──────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 5. Validação",
        "",
    ]

    validations = validation.get("validations", [])
    if validations:
        lines.append("| Validação | Status | Detalhe |")
        lines.append("|-----------|:------:|---------|")
        for v in validations:
            passed = v.get("passed", False)
            status = "✅" if passed else "❌"
            warning = v.get("warning") or ""
            lines.append(f"| `{v['name']}` | {status} | {warning} |")
        lines.append("")
    else:
        lines += ["> ℹ️  Validação não executada ainda.", ""]

    # ── Riscos ──────────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 6. Riscos e Pendências",
        "",
        "| # | Risco | Impacto | Status |",
        "|---|-------|:-------:|:------:|",
        "| R1 | Colunas do App 3 diferentes do esperado (nomes pt-br) | Alto | ⏳ Verificar na extração |",
        "| R2 | IDs de categorias do App 3 não coincidem com canônico | Médio | ⏳ Verificar no transform |",
        "| R3 | Ativos SQLite com classe não mapeada | Médio | ⏳ Verificar cobertura |",
        "| R4 | Transações de investimento com tipo não canônico | Alto | ⏳ Verificar mapeamento |",
        "| R5 | OWNER_USER_ID não configurado antes da carga | Alto | 🔴 Obrigatório |",
        "| R6 | Banco App 3 offline ou credencial expirada | Médio | ⏳ Testar conectividade |",
        "| R7 | Arquivo SQLite movido ou corrompido | Médio | ⏳ Confirmar caminho |",
        "",
    ]

    # ── Próximos passos ────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## 7. Próximos Passos",
        "",
        "### Para executar a migração real:",
        "",
        "```bash",
        "# 1. Configurar variáveis de ambiente",
        "export SUPABASE_UNIFICADO_URL='postgresql://...'",
        "export SUPABASE_ORIGEM_CONTROLE_URL='postgresql://...'",
        "export SOURCE_DB_APP2='sqlite:///path/to/investimentos.db'",
        "export OWNER_USER_ID='<uuid-do-profiles>'",
        "",
        "# 2. Extrair (dry_run por padrão)",
        "python -m migration.01_extract_dashboard_financeiro",
        "python -m migration.02_extract_controle_financeiro --no-dry-run",
        "python -m migration.03_extract_investimentos_sqlite --no-dry-run",
        "",
        "# 3. Transformar",
        "python -m migration.04_transform_to_canonical --no-dry-run",
        "",
        "# 4. Revisar migration/output/transformed/ antes de prosseguir",
        "",
        "# 5. Carregar (--no-dry-run inicia contagem regressiva de 5s)",
        "python -m migration.05_load_to_unified_supabase --no-dry-run",
        "",
        "# 6. Validar",
        "python -m migration.06_validate_migration",
        "",
        "# 7. Gerar relatório",
        "python -m migration.07_report_migration",
        "```",
        "",
        "### Checklist pré-migração:",
        "",
        "- [ ] `009_schema_amendments.sql` aplicado no Supabase",
        "- [ ] Perfil criado em `profiles` e `OWNER_USER_ID` configurado",
        "- [ ] `user_settings` criado para o perfil",
        "- [ ] Conectividade com App 3 (Supabase Controle Financeiro) testada",
        "- [ ] Caminho do SQLite App 2 confirmado",
        "- [ ] Backup do banco unificado feito antes da carga real",
        "- [ ] `dry_run=True` testado sem erros",
        "",
        "---",
        "",
        "*Relatório gerado automaticamente por `migration/07_report_migration.py`.*  ",
        f"*{now}*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from migration.config import MigrationConfig  # noqa: PLC0415

    cfg = MigrationConfig.from_env()
    cfg.ensure_output_dir()

    output_dir = cfg.output_dir
    report_md = generate_report(output_dir, cfg)

    # Salvar em docs/
    docs_path = Path(__file__).parent.parent / "docs" / "fase_4_6_relatorio_migracao_planejada.md"
    docs_path.write_text(report_md, encoding="utf-8")
    print(f"✅ Relatório salvo em: {docs_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

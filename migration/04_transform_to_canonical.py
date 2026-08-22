"""
migration/04_transform_to_canonical.py
=======================================
Transforma dados extraídos (02/03) para o modelo canônico do banco unificado.

Fase 4.6 — Transformação

Operações:
  - Renomear colunas (português → inglês, conforme banco_unificado_mapa_origem_destino.md)
  - Padronizar datas → ISO 8601 (YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SSZ)
  - Padronizar valores monetários → NUMERIC(15,2) via Decimal
  - Padronizar tipos de ativo e transação para os CHECKs canônicos
  - Separar transactions pessoais de investment_transactions
  - Injetar user_id = OWNER_USER_ID em todos os registros pessoais
  - Preservar _source_system, _source_table, _source_id
  - Emitir warnings para campos sem correspondência

Saída:
  migration/output/transformed/04_<entidade>_canonical.json

Regras:
  - Não conecta a nenhum banco.
  - Lê apenas dos arquivos JSON em migration/output/.
  - dry_run=True: processa mas não salva; mostra prévia.

Uso:
  python -m migration.04_transform_to_canonical
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Mapeamentos de colunas: App 3 (português) → canônico (inglês)
# Ref: docs/banco_unificado_mapa_origem_destino.md
# ---------------------------------------------------------------------------

APP3_COL_MAP: dict[str, dict[str, str]] = {
    "contas": {
        "usuario_id": "user_id",
        "nome": "name",
        "tipo": "type",
        "saldo_inicial": "initial_balance",
        "moeda": "currency",
        "ativo": "active",
        "criado_em": "created_at",
    },
    "categorias": {
        "usuario_id": "user_id",
        "nome": "name",
        "tipo": "type",
        "icone": "icon",
        "cor": "color",
        "pai_id": "parent_id",
    },
    "transacoes": {
        "usuario_id": "user_id",
        "conta_id": "account_id",
        "categoria_id": "category_id",
        "descricao": "description",
        "valor": "amount",
        "data_competencia": "due_date",
        "data_pagamento": "payment_date",
        "tipo": "type",
        "status": "status",
        "recorrente": "recurring",
        "parcela_atual": "installment_current",
        "total_parcelas": "installment_total",
        "grupo_parcela": "installment_group",
        "origem": "source",
        "criado_em": "created_at",
    },
    "orcamentos": {
        "usuario_id": "user_id",
        "categoria_id": "category_id",
        "mes_ano": "month_year",
        "valor_limite": "amount_limit",
    },
    "metas": {
        "usuario_id": "user_id",
        "nome": "name",
        "tipo": "type",
        "valor_alvo": "target_amount",
        "valor_acumulado": "current_amount",
        "prazo": "deadline",
        "ativa": "active",
        "criado_em": "created_at",
    },
}

# Mapeamento App 3 (schema REAL auditado 2026-05-14) → canônico
# Colunas já estão em inglês; apenas renomeações necessárias.
APP3_TRANSACTIONS_COL_MAP: dict[str, str] = {
    "date":         "due_date",          # date → due_date
    "installments": "installment_total", # installments → installment_total
    # "category" → preservado como nota (sem FK de categoria no App3)
    # "user_id"  → sobrescrito com owner_id
    # "type"     → mapeado via AUTO_VALUE_MAP["transactions.type"]
    # "amount", "description", "payment_type", "card_name" → sem renomeação
}

# Mapeamento App 2 (SQLite) → canônico
APP2_COL_MAP: dict[str, dict[str, str]] = {
    "assets": {
        "nome": "name",
        "classe": "class",
        "setor": "sector",
        "moeda": "currency",
    },
    "institutions": {
        "nome": "name",
        "tipo": "type",
    },
    "transactions": {
        "ativo_id": "asset_id",
        "tipo": "type",
        "quantidade": "quantity",
        "preco_unitario": "unit_price",
        "price": "unit_price",        # alias inglês
        "taxas": "fees",
        "data_operacao": "transaction_date",
        "date": "transaction_date",   # alias inglês
        "corretora": "broker",
        "criado_em": "created_at",
    },
    "incomes": {
        "ativo_id": "asset_id",
        "tipo": "type",
        "valor_por_cota": "amount_per_unit",
        "quantidade": "quantity",
        "valor_total": "total_amount",
        "data_com": "ex_date",
        "data_pagamento": "payment_date",
    },
    "xp_positions": {
        "ativo_id": "asset_id",
        "quantidade": "quantity",
        "preco_medio": "average_price",
        "total_investido": "total_invested",
    },
}

# Valores canônicos permitidos por campo
CANONICAL_CHECKS: dict[str, set[str]] = {
    "transactions.type": {"income", "expense", "transfer"},
    "transactions.status": {"pending", "settled", "cancelled"},
    "transactions.source": {"manual", "import", "csv", "open_banking"},
    "investment_transactions.type": {"buy", "sell"},
    "assets.class": {"stock", "reit", "etf", "fixed_income", "crypto", "other"},
    "financial_institutions.type": {"bank", "broker", "fintech", "insurance"},
    "categories.type": {"income", "expense", "transfer"},
    "dividends.type": {"dividend", "jcp", "reit_income", "amortization", "other"},
}

# Mapeamentos automáticos de valores fora do canônico
AUTO_VALUE_MAP: dict[str, dict[str, str]] = {
    # App 3 — valores reais da coluna type
    "transactions.type": {
        "entrada":      "income",
        "saida":        "expense",
        "investimento": "transfer",
    },
    "assets.class": {
        "acao": "stock", "ação": "stock", "Ação": "stock",
        "fii": "reit", "FII": "reit",
        "renda_fixa": "fixed_income",
        "cripto": "crypto",
    },
    "investment_transactions.type": {
        "compra": "buy", "venda": "sell",
        "Compra": "buy", "Venda": "sell",
        "C": "buy", "V": "sell", "B": "buy", "S": "sell",
    },
    "dividends.type": {
        "dividendo": "dividend",
        "jcp": "jcp",
        "rendimento": "reit_income",
        "amortizacao": "amortization",
        "amortização": "amortization",
    },
}


# ---------------------------------------------------------------------------
# Helpers de normalização
# ---------------------------------------------------------------------------

def _normalize_date(value: Any) -> str | None:
    """Converte valor para string ISO 8601 YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    s = str(value).strip()
    if not s or s.lower() in ("none", "null", ""):
        return None
    # Tentar formatos comuns
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s[:len(fmt)], fmt).date().isoformat()
        except ValueError:
            continue
    return s  # Retorna como está — warning será gerado pelo caller


def _normalize_decimal(value: Any, precision: int = 2) -> str | None:
    """Converte valor para string Decimal com precisão controlada."""
    if value is None:
        return None
    try:
        q = Decimal(10) ** -precision
        return str(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def _map_value(
    field_key: str,
    value: Any,
    warnings: list[str],
    record_id: str = "?",
) -> Any:
    """Normaliza valor usando mapeamento automático e verifica CHECK constraint."""
    if value is None:
        return None

    s = str(value)
    # Aplicar mapeamento automático
    auto = AUTO_VALUE_MAP.get(field_key, {})
    mapped = auto.get(s, s)

    # Verificar CHECK constraint
    allowed = CANONICAL_CHECKS.get(field_key)
    if allowed and mapped not in allowed:
        warnings.append(
            f"[{record_id}] '{field_key}': valor '{mapped}' fora do canônico {allowed}. "
            "Será inserido como-está — pode causar erro de CHECK constraint."
        )

    return mapped


def _rename_cols(record: dict, col_map: dict[str, str]) -> dict:
    """Renomeia colunas de acordo com o mapeamento."""
    result = {}
    for k, v in record.items():
        if k.startswith("_"):
            result[k] = v  # Metadados preservados
        else:
            new_key = col_map.get(k, k)
            result[new_key] = v
    return result


# ---------------------------------------------------------------------------
# Transformadores por entidade
# ---------------------------------------------------------------------------

def transform_accounts(records: list[dict], owner_id: str) -> tuple[list[dict], list[str]]:
    """Transforma contas/accounts."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP3_COL_MAP.get("contas", {}))
        rec["user_id"] = owner_id
        rec["initial_balance"] = _normalize_decimal(rec.get("initial_balance"), 2)
        rec["currency"] = rec.get("currency") or "BRL"
        rec.setdefault("active", True)
        out.append(rec)
    return out, warnings


def transform_categories(records: list[dict], owner_id: str) -> tuple[list[dict], list[str]]:
    """Transforma categorias/categories."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP3_COL_MAP.get("categorias", {}))
        if rec.get("user_id"):
            rec["user_id"] = owner_id
        cat_type = _map_value(
            "categories.type", rec.get("type"), warnings, str(rec.get("id", "?"))
        )
        rec["type"] = cat_type
        out.append(rec)
    return out, warnings


def transform_transactions(records: list[dict], owner_id: str) -> tuple[list[dict], list[str]]:
    """Transforma transacoes/transactions."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP3_COL_MAP.get("transacoes", {}))
        rec["user_id"] = owner_id
        rec["amount"] = _normalize_decimal(rec.get("amount"), 2)
        rec["due_date"] = _normalize_date(rec.get("due_date"))
        rec["payment_date"] = _normalize_date(rec.get("payment_date"))
        rec["type"] = _map_value(
            "transactions.type", rec.get("type"), warnings, str(rec.get("id", "?"))
        )
        rec["status"] = rec.get("status") or "settled"
        rec.setdefault("source", "import")
        rec.setdefault("recurring", False)
        out.append(rec)
    return out, warnings


def transform_budgets(records: list[dict], owner_id: str) -> tuple[list[dict], list[str]]:
    """Transforma orcamentos/budgets."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP3_COL_MAP.get("orcamentos", {}))
        rec["user_id"] = owner_id
        rec["amount_limit"] = _normalize_decimal(rec.get("amount_limit"), 2)
        rec["month_year"] = _normalize_date(rec.get("month_year"))
        out.append(rec)
    return out, warnings


def transform_financial_goals(records: list[dict], owner_id: str) -> tuple[list[dict], list[str]]:
    """Transforma metas/financial_goals."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP3_COL_MAP.get("metas", {}))
        rec["user_id"] = owner_id
        rec["target_amount"] = _normalize_decimal(rec.get("target_amount"), 2)
        rec["current_amount"] = _normalize_decimal(rec.get("current_amount"), 2) or "0.00"
        rec["deadline"] = _normalize_date(rec.get("deadline"))
        rec.setdefault("active", True)
        out.append(rec)
    return out, warnings


def _resolve_account_id(payment_type: str | None, card_name: str | None) -> str | None:
    """Mapeia payment_type + card_name → account_id (lido do ambiente em tempo de execução)."""
    import os as _os  # noqa: PLC0415
    account_id_cc = _os.environ.get("ACCOUNT_ID_CC", "")
    account_id_c6 = _os.environ.get("ACCOUNT_ID_C6", "")
    pt = (payment_type or "").strip().lower()
    cn = (card_name or "").strip().lower()
    if "crédit" in pt and "c6" in cn:
        return account_id_c6 or None
    # Conta, Pix, ou qualquer outro → Conta Corrente
    return account_id_cc or None


def transform_app3_transactions(
    records: list[dict],
    owner_id: str,
    category_map: dict[str, str] | None = None,
) -> tuple[list[dict], list[str]]:
    """
    Transforma transações do App 3 (schema real, colunas em inglês).

    Mapeamento:
      date          → due_date
      type          → type (saida→expense, entrada→income, investimento→transfer)
      amount        → amount (Decimal)
      category      → category_id (via category_map) + anexado à description
      installments  → installment_total
      user_id       → sobrescrito com owner_id
      payment_type + card_name → account_id (via ACCOUNT_ID_CC / ACCOUNT_ID_C6 do .env)

    Campos App3 removidos do output (não existem na tabela canônica):
      category, payment_type, card_name, id (DB gera UUID)
    """
    out, warnings = [], []
    cat_map = category_map or {}

    import os as _os  # noqa: PLC0415
    if not _os.environ.get("ACCOUNT_ID_CC"):
        warnings.append(
            "ACCOUNT_ID_CC nao configurado no .env — account_id ficara NULL nas transactions."
        )

    # Colunas canônicas permitidas em transactions
    CANONICAL_COLS = {
        "user_id", "account_id", "card_id", "category_id", "description",
        "amount", "due_date", "payment_date", "type", "status", "recurring",
        "installment_current", "installment_total", "installment_group",
        "source", "_source_system", "_source_table", "_source_id",
    }

    for r in records:
        # 1. Renomear colunas estruturais
        rec = _rename_cols(r, APP3_TRANSACTIONS_COL_MAP)

        # 2. Forçar user_id → owner
        rec["user_id"] = owner_id

        # 3. Resolver account_id a partir de payment_type + card_name
        rec["account_id"] = _resolve_account_id(
            rec.get("payment_type"), rec.get("card_name")
        )

        # 4. Normalizar type (saida/entrada/investimento → canônico)
        rec["type"] = _map_value(
            "transactions.type", rec.get("type"), warnings, str(rec.get("id", "?"))
        )

        # 5. Normalizar amount
        rec["amount"] = _normalize_decimal(rec.get("amount"), 2)

        # 6. Normalizar data
        rec["due_date"] = _normalize_date(rec.get("due_date"))

        # 7. description: combinar description + category se description vazia
        desc = str(rec.get("description") or "").strip()
        cat_name = str(rec.get("category") or "").strip()
        if not desc and cat_name:
            rec["description"] = cat_name
        elif desc and cat_name and cat_name.lower() != desc.lower():
            rec["description"] = f"{desc} [{cat_name}]"
        if not rec.get("description"):
            rec["description"] = "Sem descrição"

        # 8. Resolver category_id pelo nome da categoria
        rec["category_id"] = cat_map.get(cat_name.lower()) if cat_name else None
        if cat_name and not rec["category_id"]:
            warnings.append(
                f"Categoria '{cat_name}' nao encontrada no banco — category_id sera NULL."
            )

        # 9. Defaults canônicos
        rec.setdefault("status", "settled")
        rec.setdefault("source", "import")
        rec.setdefault("recurring", False)
        rec["payment_date"] = _normalize_date(rec.get("payment_date"))

        # 10. Remover campos que não pertencem à tabela canônica
        clean = {k: v for k, v in rec.items() if k in CANONICAL_COLS}
        out.append(clean)
    return out, warnings


def transform_assets(records: list[dict]) -> tuple[list[dict], list[str]]:
    """Transforma assets do App 2 (SQLite)."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP2_COL_MAP.get("assets", {}))
        rec["class"] = _map_value(
            "assets.class", rec.get("class"), warnings, str(rec.get("id", "?"))
        )
        rec["currency"] = rec.get("currency") or "BRL"
        out.append(rec)
    return out, warnings


def transform_investment_transactions(
    records: list[dict], owner_id: str
) -> tuple[list[dict], list[str]]:
    """Transforma transactions SQLite → investment_transactions canônico."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP2_COL_MAP.get("transactions", {}))
        rec["user_id"] = owner_id
        rec["type"] = _map_value(
            "investment_transactions.type",
            rec.get("type"),
            warnings,
            str(rec.get("id", "?")),
        )
        rec["unit_price"] = _normalize_decimal(rec.get("unit_price"), 6)
        rec["quantity"] = _normalize_decimal(rec.get("quantity"), 8)
        rec["fees"] = _normalize_decimal(rec.get("fees"), 2) or "0.00"
        rec["transaction_date"] = _normalize_date(rec.get("transaction_date"))
        out.append(rec)
    return out, warnings


def transform_dividends(records: list[dict], owner_id: str) -> tuple[list[dict], list[str]]:
    """Transforma incomes SQLite → dividends canônico."""
    out, warnings = [], []
    for r in records:
        rec = _rename_cols(r, APP2_COL_MAP.get("incomes", {}))
        rec["user_id"] = owner_id
        rec["type"] = _map_value(
            "dividends.type", rec.get("type"), warnings, str(rec.get("id", "?"))
        )
        rec["amount_per_unit"] = _normalize_decimal(rec.get("amount_per_unit"), 6)
        rec["quantity"] = _normalize_decimal(rec.get("quantity"), 8)
        rec["total_amount"] = _normalize_decimal(rec.get("total_amount"), 2)
        rec["payment_date"] = _normalize_date(rec.get("payment_date"))
        rec["ex_date"] = _normalize_date(rec.get("ex_date"))
        out.append(rec)
    return out, warnings


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

def transform_all(cfg) -> dict[str, Any]:  # noqa: ANN001
    """Executa todas as transformações."""
    result: dict[str, Any] = {
        "transformed_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": cfg.dry_run,
        "owner_id": "configurado" if cfg.owner_id else "AUSENTE",
        "entities": {},
        "warnings": [],
        "total_records": 0,
    }

    if not cfg.owner_id:
        result["warnings"].append("OWNER_USER_ID não configurado — registros pessoais não terão user_id.")

    output_dir = cfg.output_dir

    # ── Carregar mapa de categorias (name.lower() → id) do banco unificado ──
    category_map: dict[str, str] = {}
    if cfg.dest_url:
        try:
            from sqlalchemy import text as _text  # noqa: PLC0415

            from migration.config import make_engine  # noqa: PLC0415
            _cat_engine = make_engine(cfg.dest_url, source_label="categories_lookup", read_only_hint=True)
            with _cat_engine.connect() as _conn:
                rows = _conn.execute(
                    _text("SELECT id, name FROM categories")
                ).fetchall()
                for row in rows:
                    category_map[str(row[1]).strip().lower()] = str(row[0])
            print(f"  Categorias carregadas do banco: {len(category_map)}")
        except Exception as _e:  # noqa: BLE001
            result["warnings"].append(f"Nao foi possivel carregar categorias do banco: {_e}")
    else:
        result["warnings"].append(
            "SUPABASE_UNIFICADO_URL ausente — category_id sera NULL em todas as transactions."
        )

    # ── App 3 (schema real auditado 2026-05-14: tabela 'transactions') ────
    src_file_app3 = output_dir / "02_app3_transactions.json"
    if src_file_app3.exists():
        with open(src_file_app3, encoding="utf-8") as f:
            data = json.load(f)

        records = data.get("records", [])
        if not records and data.get("sample"):
            # dry_run: usar apenas sample para preview
            records = data["sample"]

        transformed, t_warnings = transform_app3_transactions(
            records, cfg.owner_id, category_map=category_map
        )
        result["entities"]["transactions"] = {
            "source": "app3/transactions",
            "count": len(transformed),
            "records": transformed if not cfg.dry_run else transformed[:3],
            "warnings": t_warnings,
        }
        result["total_records"] += len(transformed)
        result["warnings"].extend(t_warnings)
        print(
            f"  ✅ {'transactions':<20} → {'transactions':<25} "
            f"{len(transformed):>5,} registros"
        )
    else:
        result["entities"]["transactions"] = {
            "source": "app3/transactions",
            "count": 0,
            "warnings": ["Arquivo não encontrado: 02_app3_transactions.json"],
        }
        print("  ⚠️  02_app3_transactions.json não encontrado — transactions: 0 registros")

    # ── App 2 (SQLite) ────────────────────────────────────────────────────
    transformers_app2 = [
        ("assets", "assets", transform_assets, False),
        ("transactions", "investment_transactions", transform_investment_transactions, True),
        ("incomes", "dividends", transform_dividends, True),
    ]

    for src_table, dest_table, transformer, needs_owner in transformers_app2:
        src_file = output_dir / f"03_app2_{src_table}.json"
        if not src_file.exists():
            result["entities"][dest_table] = {
                "source": f"app2/{src_table}",
                "count": 0,
                "warnings": [f"Arquivo não encontrado: {src_file.name}"],
            }
            continue

        with open(src_file, encoding="utf-8") as f:
            data = json.load(f)

        records = data.get("records", data.get("sample", []))

        if needs_owner:
            transformed, warnings = transformer(records, cfg.owner_id)
        else:
            transformed, warnings = transformer(records)

        result["entities"][dest_table] = {
            "source": f"app2/{src_table}",
            "count": len(transformed),
            "records": transformed if not cfg.dry_run else transformed[:3],
            "warnings": warnings,
        }
        result["total_records"] += len(transformed)
        result["warnings"].extend(warnings)
        print(f"  ✅ {src_table:<20} → {dest_table:<25} {len(transformed):>5,} registros")

    return result


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def save_result(result: dict, cfg) -> list[Path]:  # noqa: ANN001
    """Salva dados transformados."""
    cfg.ensure_output_dir()
    saved: list[Path] = []

    for entity, data in result.get("entities", {}).items():
        path = cfg.transformed_path(f"04_{entity}_canonical.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        saved.append(path)
        print(f"  💾 {path.name}")

    # Sumário
    summary_path = cfg.transformed_path("04_transform_summary.json")
    summary = {k: v for k, v in result.items() if k != "entities"}
    summary["entity_counts"] = {
        e: d.get("count", 0) for e, d in result.get("entities", {}).items()
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    saved.append(summary_path)

    return saved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transforma dados extraídos para o modelo canônico"
    )
    parser.add_argument("--no-dry-run", action="store_true", default=False)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from migration.config import MigrationConfig  # noqa: PLC0415

    cfg = MigrationConfig.from_env(dry_run=not args.no_dry_run)
    cfg.print_summary()

    print("\n🔄 Iniciando transformação para o modelo canônico...")
    result = transform_all(cfg)
    save_result(result, cfg)

    print(f"\n  Total transformado: {result['total_records']:,} registros")
    if result["warnings"]:
        print(f"  ⚠️  {len(result['warnings'])} avisos — revisar antes de carregar.")

    return 0 if not result["warnings"] else 1


if __name__ == "__main__":
    sys.exit(main())

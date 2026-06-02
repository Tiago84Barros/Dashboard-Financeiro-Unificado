"""
Importacao de extratos bancarios em PDF para o Controle Financeiro.

Fluxo:
- extrai movimentos de PDF C6 Bank;
- classifica usando apenas categorias existentes;
- grava uma tabela intermediaria de auditoria/revisao;
- publica em `transactions` somente movimentos classificados.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import unicodedata
from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from core.config import settings
from core.database import get_engine

logger = logging.getLogger(__name__)

BANK_STATEMENT_SOURCE = "import"
BANK_STATEMENT_KIND = "bank_statement_pdf"
SUPPORTED_BANKS = ("C6 Bank",)
VALOR_ALTO_FINANCIAMENTO = 1000.0

_DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")
_MONEY_RE = re.compile(
    r"(?:[-+]\s*)?(?:R\$\s*)?(?:[-+]\s*)?"
    r"(?:\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:[,.]\d{2}))"
)

DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS bank_statement_movements (
        id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id                    UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        account_id                 UUID REFERENCES accounts(id) ON DELETE SET NULL,
        transaction_id             UUID REFERENCES transactions(id) ON DELETE SET NULL,
        banco                      TEXT NOT NULL,
        conta                      TEXT,
        data_movimento             DATE NOT NULL,
        data_lancamento            DATE,
        tipo_original_banco        TEXT,
        descricao_original         TEXT NOT NULL,
        descricao_normalizada      TEXT NOT NULL,
        valor                      NUMERIC(15,2) NOT NULL,
        direcao                    TEXT NOT NULL,
        categoria_id               UUID REFERENCES categories(id) ON DELETE SET NULL,
        subcategoria_id            UUID,
        categoria_sugerida_texto   TEXT,
        subcategoria_sugerida_texto TEXT,
        categoria_confirmada_id    UUID REFERENCES categories(id) ON DELETE SET NULL,
        subcategoria_confirmada_id UUID,
        confianca_classificacao    NUMERIC(5,2),
        status_classificacao       TEXT NOT NULL DEFAULT 'pendente',
        origem_arquivo             TEXT,
        hash_lancamento            TEXT NOT NULL,
        created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (user_id, hash_lancamento)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bank_statement_classification_rules (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id               UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        banco                 TEXT,
        tipo_original_banco   TEXT,
        palavra_chave         TEXT NOT NULL,
        descricao_normalizada TEXT,
        category_id           UUID NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        subcategoria_id       UUID,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bank_statement_movements_user_date
    ON bank_statement_movements (user_id, data_movimento DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bank_statement_movements_user_status
    ON bank_statement_movements (user_id, status_classificacao)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_bank_statement_rules_user_bank
    ON bank_statement_classification_rules (user_id, banco)
    """,
    "ALTER TABLE bank_statement_movements ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE bank_statement_classification_rules ENABLE ROW LEVEL SECURITY",
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='bank_statement_movements'
              AND policyname='bank_statement_movements_owner_all'
        ) THEN
            CREATE POLICY bank_statement_movements_owner_all ON bank_statement_movements
                USING (user_id = auth.uid())
                WITH CHECK (user_id = auth.uid());
        END IF;
    END; $$
    """,
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='bank_statement_classification_rules'
              AND policyname='bank_statement_rules_owner_all'
        ) THEN
            CREATE POLICY bank_statement_rules_owner_all ON bank_statement_classification_rules
                USING (user_id = auth.uid())
                WITH CHECK (user_id = auth.uid());
        END IF;
    END; $$
    """,
]


def _norm(value: object) -> str:
    text_value = unicodedata.normalize("NFKD", str(value or ""))
    text_value = text_value.encode("ascii", "ignore").decode("ascii")
    text_value = re.sub(r"[^a-zA-Z0-9]+", " ", text_value).casefold().strip()
    return " ".join(text_value.split())


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def _parse_money(value: object) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    negative = "-" in raw
    clean = (
        raw.replace("R$", "")
        .replace("+", "")
        .replace("-", "")
        .replace(" ", "")
        .strip()
    )
    if "," in clean and "." in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        clean = clean.replace(",", ".")
    try:
        amount = float(clean)
    except ValueError:
        return 0.0
    return -amount if negative else amount


def _fmt_hash_amount(value: float) -> str:
    return f"{float(value or 0):.2f}"


def build_bank_statement_hash(row: dict) -> str:
    """Hash idempotente conforme banco, data, valor, tipo e descricao normalizada."""
    raw = "|".join(
        [
            _norm(row.get("banco") or "C6 Bank"),
            str(row.get("data_movimento") or ""),
            _fmt_hash_amount(float(row.get("valor") or 0.0)),
            _norm(row.get("tipo_original_banco") or ""),
            row.get("descricao_normalizada") or _norm(row.get("descricao_original")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _infer_bank_type(description: str, amount: float) -> str:
    norm = _norm(description)
    known = [
        ("entrada pix", "Entrada PIX"),
        ("saida pix", "Saida PIX"),
        ("pix enviado", "Saida PIX"),
        ("pix recebido", "Entrada PIX"),
        ("debito de cartao", "Debito de Cartao"),
        ("utilidade de cartao", "Utilidade de Cartao"),
        ("outros gastos", "Outros gastos"),
        ("resgate de cdb", "Resgate de CDB"),
        ("boleto", "Boleto"),
        ("pagamento", "Pagamento"),
        ("entradas", "Entradas"),
    ]
    for key, label in known:
        if key in norm:
            return label
    return "Entrada" if amount >= 0 else "Saida"


def _direction_for(bank_type: str, description: str, amount: float) -> str:
    norm = _norm(f"{bank_type} {description}")
    if "resgate de cdb" in norm:
        return "entrada"
    if amount < 0 or any(
        term in norm
        for term in (
            "saida pix",
            "pix enviado",
            "pagamento",
            "boleto",
            "debito de cartao",
            "outros gastos",
            "utilidade de cartao",
        )
    ):
        return "saida"
    return "entrada"


def _normalize_signed_amount(bank_type: str, description: str, raw_amount: float) -> float:
    direction = _direction_for(bank_type, description, raw_amount)
    amount = abs(float(raw_amount or 0.0))
    return amount if direction == "entrada" else -amount


def _clean_statement_description(line: str, amount_span: tuple[int, int]) -> str:
    left = line[: amount_span[0]]
    right = line[amount_span[1] :]
    text_value = f"{left} {right}"
    text_value = _DATE_RE.sub(" ", text_value)
    return " ".join(text_value.replace("|", " ").split())


def _parse_c6_line(line: str, banco: str, origem_arquivo: str | None) -> dict | None:
    clean = " ".join(str(line or "").split())
    if not clean or not _DATE_RE.search(clean):
        return None

    dates = [_parse_date(value) for value in _DATE_RE.findall(clean)]
    dates = [value for value in dates if value is not None]
    if not dates:
        return None

    money_matches = list(_MONEY_RE.finditer(clean))
    if not money_matches:
        return None
    amount_match = money_matches[-1]
    raw_amount = _parse_money(amount_match.group(0))
    if raw_amount == 0 and "0,00" not in amount_match.group(0):
        return None

    description = _clean_statement_description(clean, amount_match.span())
    if len(_norm(description)) < 3:
        return None

    bank_type = _infer_bank_type(description, raw_amount)
    amount = _normalize_signed_amount(bank_type, description, raw_amount)
    direction = _direction_for(bank_type, description, amount)

    row = {
        "banco": banco,
        "conta": None,
        "data_movimento": dates[0],
        "data_lancamento": dates[1] if len(dates) > 1 else None,
        "tipo_original_banco": bank_type,
        "descricao_original": description[:500],
        "descricao_normalizada": _norm(description),
        "valor": round(amount, 2),
        "direcao": direction,
        "categoria_id": None,
        "subcategoria_id": None,
        "categoria_sugerida_texto": None,
        "subcategoria_sugerida_texto": None,
        "status_classificacao": "pendente",
        "confianca_classificacao": 0.0,
        "origem_arquivo": origem_arquivo,
    }
    row["hash_lancamento"] = build_bank_statement_hash(row)
    return row


def _merge_wrapped_lines(text_value: str) -> list[str]:
    lines = [" ".join(line.split()) for line in str(text_value or "").splitlines()]
    lines = [line for line in lines if line]
    merged: list[str] = []
    buffer = ""
    for line in lines:
        starts_with_date = bool(_DATE_RE.search(line))
        has_money = bool(_MONEY_RE.search(line))
        if starts_with_date and has_money:
            if buffer:
                merged.append(buffer)
                buffer = ""
            merged.append(line)
        elif starts_with_date:
            if buffer:
                merged.append(buffer)
            buffer = line
        elif buffer:
            buffer = f"{buffer} {line}"
            if _MONEY_RE.search(buffer):
                merged.append(buffer)
                buffer = ""
    if buffer:
        merged.append(buffer)
    return merged


def parse_c6_bank_text(text_value: str, file_name: str | None = None) -> dict:
    """Extrai movimentos de texto ja obtido do PDF C6 Bank."""
    rows: list[dict] = []
    seen_hashes: set[str] = set()
    errors: list[str] = []

    for line in _merge_wrapped_lines(text_value):
        row = _parse_c6_line(line, "C6 Bank", file_name)
        if not row:
            continue
        if row["hash_lancamento"] in seen_hashes:
            continue
        seen_hashes.add(row["hash_lancamento"])
        rows.append(row)

    if not rows:
        errors.append("Nenhuma movimentacao bancaria foi identificada no PDF.")
    return {
        "ok": bool(rows) and not errors,
        "bank": "C6 Bank",
        "rows": rows,
        "errors": errors,
        "summary": summarize_bank_movements(rows, "C6 Bank"),
    }


def _extract_pdf_text(file_bytes: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("Dependencia pdfplumber nao instalada.") from exc

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def parse_c6_bank_pdf(file_bytes: bytes, file_name: str | None = None) -> dict:
    text_value = _extract_pdf_text(file_bytes)
    return parse_c6_bank_text(text_value, file_name=file_name)


def summarize_bank_movements(rows: list[dict], bank: str = "C6 Bank") -> dict:
    if not rows:
        return {
            "bank": bank,
            "rows": 0,
            "entradas": 0,
            "saidas": 0,
            "total_entradas": 0.0,
            "total_saidas": 0.0,
            "classificados": 0,
            "pendentes": 0,
            "periodo_inicio": None,
            "periodo_fim": None,
        }
    dates = [row["data_movimento"] for row in rows if row.get("data_movimento")]
    entradas = [float(row.get("valor") or 0.0) for row in rows if float(row.get("valor") or 0.0) > 0]
    saidas = [float(row.get("valor") or 0.0) for row in rows if float(row.get("valor") or 0.0) < 0]
    pendentes = sum(1 for row in rows if row.get("status_classificacao") == "pendente")
    return {
        "bank": bank,
        "rows": len(rows),
        "entradas": len(entradas),
        "saidas": len(saidas),
        "total_entradas": round(sum(entradas), 2),
        "total_saidas": round(sum(abs(value) for value in saidas), 2),
        "classificados": len(rows) - pendentes,
        "pendentes": pendentes,
        "periodo_inicio": min(dates) if dates else None,
        "periodo_fim": max(dates) if dates else None,
    }


def _category_name(category: dict) -> str:
    return str(category.get("nome") or category.get("name") or "")


def _category_type(category: dict) -> str:
    return str(category.get("tipo") or category.get("type") or "")


def _find_category(
    categories: list[dict],
    aliases: list[str],
    preferred_types: tuple[str, ...] = (),
) -> dict | None:
    norm_aliases = [_norm(alias) for alias in aliases if _norm(alias)]
    if not norm_aliases:
        return None

    candidates: list[tuple[int, int, dict]] = []
    for category in categories:
        name_norm = _norm(_category_name(category))
        type_norm = _norm(_category_type(category))
        if preferred_types and type_norm not in preferred_types:
            continue
        score = None
        best_alias_idx = len(norm_aliases)
        for alias_idx, alias in enumerate(norm_aliases):
            if name_norm == alias:
                score = 0
                best_alias_idx = alias_idx
                break
            if alias in name_norm:
                if score is None or 1 < score:
                    score = 1
                    best_alias_idx = alias_idx
            elif name_norm and name_norm in alias and len(name_norm) >= 5:
                if score is None or 2 < score:
                    score = 2
                    best_alias_idx = alias_idx
        if score is not None:
            candidates.append((best_alias_idx, score, category))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], _category_name(item[2])))
        return candidates[0][2]

    if preferred_types:
        return _find_category(categories, aliases, ())
    return None


def _is_boleto_or_pagamento(row: dict) -> bool:
    text_value = _norm(f"{row.get('tipo_original_banco')} {row.get('descricao_original')}")
    return "boleto" in text_value or "pagamento" in text_value


def _classification_payload(
    row: dict,
    category: dict | None,
    suggested_text: str,
    status: str,
    confidence: float,
    reason: str,
) -> dict:
    category_id = category.get("id") if category else None
    category_name = _category_name(category) if category else None
    return {
        **row,
        "categoria_id": category_id,
        "categoria_nome": category_name,
        "categoria_sugerida_texto": suggested_text,
        "status_classificacao": status if category_id else "pendente",
        "confianca_classificacao": round(float(confidence if category_id else 0.0), 2),
        "classificacao_motivo": reason,
    }


def _rule_category(rule: dict, categories: list[dict]) -> dict | None:
    category_id = str(rule.get("category_id") or rule.get("categoria_id") or "")
    for category in categories:
        if str(category.get("id")) == category_id:
            return category
    return None


def _apply_saved_rules(row: dict, categories: list[dict], saved_rules: list[dict]) -> dict | None:
    desc_norm = row.get("descricao_normalizada") or ""
    bank_norm = _norm(row.get("banco"))
    type_norm = _norm(row.get("tipo_original_banco"))
    for rule in saved_rules or []:
        rule_bank = _norm(rule.get("banco"))
        rule_type = _norm(rule.get("tipo_original_banco"))
        if rule_bank and rule_bank != bank_norm:
            continue
        if rule_type and rule_type != type_norm:
            continue
        keyword = _norm(rule.get("palavra_chave"))
        exact = _norm(rule.get("descricao_normalizada"))
        if (keyword and keyword in desc_norm) or (exact and exact == desc_norm):
            category = _rule_category(rule, categories)
            if category:
                return _classification_payload(
                    row,
                    category,
                    _category_name(category),
                    "confirmada",
                    0.99,
                    "Regra salva pelo usuario",
                )
    return None


def classify_bank_movement(
    row: dict,
    categories: list[dict],
    saved_rules: list[dict] | None = None,
) -> dict:
    """Classifica uma movimentacao usando somente categorias recebidas."""
    manual = _apply_saved_rules(row, categories, saved_rules or [])
    if manual:
        return manual

    desc = row.get("descricao_normalizada") or _norm(row.get("descricao_original"))
    abs_value = abs(float(row.get("valor") or 0.0))

    if "pix recebido de tiago da silva barros" in desc:
        category = _find_category(
            categories,
            ["Salario", "Renda principal", "Remuneracao", "Receita fixa", "Receita", "Entrada"],
            ("income",),
        )
        return _classification_payload(row, category, "Salario", "sugerida", 0.98, "Regra explicita: salario")

    if _is_boleto_or_pagamento(row) and any(
        term in desc for term in ("euroville", "euro ville", "salinas resort", "salinas", "resort")
    ):
        category = _find_category(
            categories,
            ["Condominio", "Moradia", "Casa", "Imovel", "Despesas fixas", "Saida"],
            ("expense",),
        )
        return _classification_payload(row, category, "Condominio", "sugerida", 0.95, "Regra explicita: condominio")

    if (
        _is_boleto_or_pagamento(row)
        and any(term in desc for term in ("reserva do lago", "reserva lago", "res do lago"))
        and abs_value >= VALOR_ALTO_FINANCIAMENTO
    ):
        category = _find_category(
            categories,
            [
                "Financiamento",
                "Emprestimos e Financiamentos",
                "Dividas",
                "Imovel",
                "Moradia",
                "Casa",
                "Investimentos",
            ],
            ("expense", "transfer", "income"),
        )
        return _classification_payload(row, category, "Financiamento", "sugerida", 0.93, "Regra explicita: financiamento")

    auto_rules: list[tuple[tuple[str, ...], list[str], tuple[str, ...], str, float]] = [
        (("resgate de cdb",), ["Resgate de Investimento", "Investimentos", "Renda Fixa"], ("transfer", "income"), "Resgate de Investimento", 0.92),
        (("tim celular",), ["Telefone / Internet", "Assinaturas", "Moradia"], ("expense",), "Telefone / Internet", 0.86),
        (("equatorial",), ["Luz", "Energia", "Moradia", "Casa", "Contas fixas"], ("expense",), "Luz", 0.86),
        (("secretaria do tesouro nacional", "tesouro nacional"), ["Impostos e Taxas", "Impostos", "Tributos", "Taxas"], ("expense",), "Impostos e Taxas", 0.88),
        (("posto", "pana vip", "para vip", "pit stop", "abastece"), ["Combustivel", "Transporte", "Carro", "Veiculo"], ("expense",), "Combustivel", 0.84),
        (("ifood",), ["Alimentacao", "Restaurante", "Delivery", "Lanche"], ("expense",), "Alimentacao", 0.84),
        (("drogaria", "farmacia"), ["Saude", "Farmacia", "Medicamentos"], ("expense",), "Saude", 0.86),
        (("estacionamento",), ["Transporte", "Estacionamento", "Carro", "Veiculo"], ("expense",), "Transporte", 0.83),
    ]
    for keywords, aliases, preferred_types, suggested, confidence in auto_rules:
        if any(keyword in desc for keyword in keywords):
            category = _find_category(categories, aliases, preferred_types)
            return _classification_payload(row, category, suggested, "sugerida", confidence, "Regra automatica por palavra-chave")

    if "debito de cartao" in desc or "debito de cartao" in _norm(row.get("tipo_original_banco")):
        category = _find_category(categories, ["Debito de Cartao", "Compra no Cartao"], ("expense", "transfer"))
        return _classification_payload(row, category, "Debito de Cartao", "sugerida", 0.78, "Regra automatica: debito de cartao")

    return {
        **row,
        "categoria_id": None,
        "categoria_nome": None,
        "categoria_sugerida_texto": row.get("categoria_sugerida_texto") or "Pendente",
        "status_classificacao": "pendente",
        "confianca_classificacao": 0.0,
        "classificacao_motivo": "Sem regra segura",
    }


def classify_bank_movements(rows: list[dict], categories: list[dict], saved_rules: list[dict] | None = None) -> list[dict]:
    return [classify_bank_movement(row, categories, saved_rules or []) for row in rows]


def _ensure_tables(conn) -> None:
    for ddl in DDL_SQL:
        conn.execute(text(ddl))


def _owner_id() -> str:
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID nao configurado.")
    return str(owner)


def _maybe_owner_id() -> str | None:
    return str(settings.OWNER_USER_ID) if settings.OWNER_USER_ID else None


def _fetch_categories(conn, owner: str) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT id::text, name, type, parent_id::text
            FROM categories
            WHERE user_id = CAST(:uid AS uuid) OR user_id IS NULL
            ORDER BY name
            """
        ),
        {"uid": owner},
    ).fetchall()
    return [{"id": r.id, "nome": r.name, "tipo": r.type, "parent_id": r.parent_id} for r in rows]


def _fetch_rules(conn, owner: str, banco: str = "C6 Bank") -> list[dict]:
    _ensure_tables(conn)
    rows = conn.execute(
        text(
            """
            SELECT id::text, banco, tipo_original_banco, palavra_chave,
                   descricao_normalizada, category_id::text, subcategoria_id::text
            FROM bank_statement_classification_rules
            WHERE user_id = CAST(:uid AS uuid)
              AND (banco IS NULL OR banco = :banco)
            ORDER BY created_at DESC
            """
        ),
        {"uid": owner, "banco": banco},
    ).fetchall()
    return [
        {
            "id": r.id,
            "banco": r.banco,
            "tipo_original_banco": r.tipo_original_banco,
            "palavra_chave": r.palavra_chave,
            "descricao_normalizada": r.descricao_normalizada,
            "category_id": r.category_id,
            "subcategoria_id": r.subcategoria_id,
        }
        for r in rows
    ]


def get_bank_statement_accounts() -> list[dict]:
    if settings.MOCK_MODE:
        return [{"id": "mock-checking", "nome": "Conta Corrente", "tipo": "checking"}]
    engine = get_engine()
    if engine is None:
        return []
    owner = _maybe_owner_id()
    if not owner:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id::text, name, type
                FROM accounts
                WHERE user_id = CAST(:uid AS uuid)
                  AND active = TRUE
                  AND COALESCE(type, '') != 'credit_card'
                ORDER BY name
                """
            ),
            {"uid": owner},
        ).fetchall()
    return [{"id": r.id, "nome": r.name, "tipo": r.type} for r in rows]


def get_bank_statement_categories() -> list[dict]:
    if settings.MOCK_MODE:
        return []
    engine = get_engine()
    if engine is None:
        return []
    owner = _maybe_owner_id()
    if not owner:
        return []
    with engine.connect() as conn:
        return _fetch_categories(conn, owner)


def preview_bank_statement_pdf(file_bytes: bytes, file_name: str | None = None, banco: str = "C6 Bank") -> dict:
    parsed = parse_c6_bank_pdf(file_bytes, file_name=file_name)
    rows = parsed.get("rows", [])
    if not rows:
        return parsed

    if settings.MOCK_MODE:
        categories: list[dict] = []
        rules: list[dict] = []
    else:
        engine = get_engine()
        if engine is None:
            categories, rules = [], []
        else:
            owner = _maybe_owner_id()
            if not owner:
                categories, rules = [], []
            else:
                with engine.begin() as conn:
                    _ensure_tables(conn)
                    categories = _fetch_categories(conn, owner)
                    rules = _fetch_rules(conn, owner, banco)

    classified = classify_bank_movements(rows, categories, rules)
    parsed["rows"] = classified
    parsed["summary"] = summarize_bank_movements(classified, banco)
    return parsed


def _transaction_type_for(row: dict, category: dict | None = None) -> str:
    cat_name = _category_name(category or {})
    cat_type = _category_type(category or {})
    cat_norm = _norm(cat_name)
    suggested = _norm(row.get("categoria_sugerida_texto"))
    if cat_type == "transfer" or any(term in cat_norm for term in ("investimento", "transferencia")):
        return "transfer"
    if "investimento" in suggested or "resgate" in suggested:
        return "transfer"
    if row.get("direcao") == "entrada":
        return "income"
    return "expense"


def _insert_transaction(conn, owner: str, row: dict, account_id: str, category: dict | None) -> str | None:
    tx_row = conn.execute(
        text(
            """
            INSERT INTO transactions (
                user_id, account_id, category_id, description, amount,
                due_date, payment_date, type, status, source
            )
            VALUES (
                CAST(:uid AS uuid), CAST(:account_id AS uuid), CAST(:category_id AS uuid),
                :description, :amount, :due_date, :payment_date, :type, 'settled', :source
            )
            RETURNING id::text
            """
        ),
        {
            "uid": owner,
            "account_id": account_id,
            "category_id": row.get("categoria_id"),
            "description": str(row.get("descricao_original") or "")[:255],
            "amount": round(float(row.get("valor") or 0.0), 2),
            "due_date": row.get("data_movimento"),
            "payment_date": row.get("data_lancamento"),
            "type": _transaction_type_for(row, category),
            "source": BANK_STATEMENT_SOURCE,
        },
    ).fetchone()
    return tx_row.id if tx_row else None


def import_bank_statement_pdf(file_bytes: bytes, file_name: str, account_id: str, banco: str = "C6 Bank") -> dict:
    if settings.MOCK_MODE:
        return {"ok": False, "message": "Modo mock ativo; importacao nao executada."}

    engine = get_engine()
    if engine is None:
        return {"ok": False, "message": "Banco nao configurado."}

    owner = _owner_id()
    parsed = parse_c6_bank_pdf(file_bytes, file_name=file_name)
    if parsed.get("errors"):
        return {"ok": False, "message": "; ".join(parsed["errors"][:5]), "errors": parsed["errors"]}
    if not parsed.get("rows"):
        return {"ok": False, "message": "Extrato sem linhas validas."}

    inserted = 0
    skipped = 0
    published = 0
    pending = 0

    with engine.begin() as conn:
        _ensure_tables(conn)
        account = conn.execute(
            text(
                """
                SELECT id::text, name, type
                FROM accounts
                WHERE id = CAST(:account_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                LIMIT 1
                """
            ),
            {"account_id": account_id, "uid": owner},
        ).fetchone()
        if not account:
            return {"ok": False, "message": "Conta bancaria nao encontrada."}
        if getattr(account, "type", "") == "credit_card":
            return {"ok": False, "message": "Extratos bancarios devem ser importados em conta, nao cartao."}

        categories = _fetch_categories(conn, owner)
        rules = _fetch_rules(conn, owner, banco)
        rows = classify_bank_movements(parsed["rows"], categories, rules)
        category_by_id = {str(category["id"]): category for category in categories}

        for row in rows:
            params = {
                "uid": owner,
                "account_id": account_id,
                "banco": row.get("banco") or banco,
                "conta": getattr(account, "name", None),
                "data_movimento": row.get("data_movimento"),
                "data_lancamento": row.get("data_lancamento"),
                "tipo_original_banco": row.get("tipo_original_banco"),
                "descricao_original": row.get("descricao_original"),
                "descricao_normalizada": row.get("descricao_normalizada"),
                "valor": round(float(row.get("valor") or 0.0), 2),
                "direcao": row.get("direcao"),
                "categoria_id": row.get("categoria_id"),
                "subcategoria_id": row.get("subcategoria_id"),
                "categoria_sugerida_texto": row.get("categoria_sugerida_texto"),
                "subcategoria_sugerida_texto": row.get("subcategoria_sugerida_texto"),
                "confianca_classificacao": row.get("confianca_classificacao"),
                "status_classificacao": row.get("status_classificacao") or "pendente",
                "origem_arquivo": file_name,
                "hash_lancamento": row.get("hash_lancamento"),
            }
            inserted_row = conn.execute(
                text(
                    """
                    INSERT INTO bank_statement_movements (
                        user_id, account_id, banco, conta, data_movimento, data_lancamento,
                        tipo_original_banco, descricao_original, descricao_normalizada, valor,
                        direcao, categoria_id, subcategoria_id, categoria_sugerida_texto,
                        subcategoria_sugerida_texto, confianca_classificacao,
                        status_classificacao, origem_arquivo, hash_lancamento
                    )
                    VALUES (
                        CAST(:uid AS uuid), CAST(:account_id AS uuid), :banco, :conta,
                        :data_movimento, :data_lancamento, :tipo_original_banco,
                        :descricao_original, :descricao_normalizada, :valor, :direcao,
                        CAST(:categoria_id AS uuid), CAST(:subcategoria_id AS uuid),
                        :categoria_sugerida_texto, :subcategoria_sugerida_texto,
                        :confianca_classificacao, :status_classificacao,
                        :origem_arquivo, :hash_lancamento
                    )
                    ON CONFLICT (user_id, hash_lancamento) DO NOTHING
                    RETURNING id::text
                    """
                ),
                params,
            ).fetchone()
            if not inserted_row:
                skipped += 1
                continue

            inserted += 1
            if row.get("categoria_id"):
                category = category_by_id.get(str(row.get("categoria_id")))
                tx_id = _insert_transaction(conn, owner, row, account_id, category)
                if tx_id:
                    conn.execute(
                        text(
                            """
                            UPDATE bank_statement_movements
                            SET transaction_id = CAST(:tx_id AS uuid), updated_at = NOW()
                            WHERE id = CAST(:movement_id AS uuid)
                            """
                        ),
                        {"tx_id": tx_id, "movement_id": inserted_row.id},
                    )
                    published += 1
            else:
                pending += 1

    try:
        from core.controle import _clear_controle_caches

        _clear_controle_caches()
    except Exception:
        logger.debug("Nao foi possivel limpar caches do controle financeiro.", exc_info=True)

    summary = summarize_bank_movements(rows, banco)
    summary.update({"inserted": inserted, "skipped": skipped, "published": published, "pending": pending})
    return {
        "ok": True,
        "message": (
            f"{inserted} movimento(s) importado(s); {published} publicado(s) no Controle Financeiro; "
            f"{pending} pendente(s); {skipped} duplicado(s) ignorado(s)."
        ),
        "summary": summary,
        "rows": rows,
    }


def get_bank_statement_review_rows(
    status: str | None = None,
    ano: int | None = None,
    mes: int | None = None,
    banco: str | None = None,
    limit: int = 250,
) -> list[dict]:
    if settings.MOCK_MODE:
        return []
    engine = get_engine()
    if engine is None:
        return []
    owner = _maybe_owner_id()
    if not owner:
        return []
    where = ["m.user_id = CAST(:uid AS uuid)"]
    params: dict[str, Any] = {"uid": owner, "limit": int(limit)}
    if status and status != "Todos":
        where.append("m.status_classificacao = :status")
        params["status"] = status
    if ano:
        where.append("EXTRACT(YEAR FROM m.data_movimento)::int = :ano")
        params["ano"] = int(ano)
    if mes:
        where.append("EXTRACT(MONTH FROM m.data_movimento)::int = :mes")
        params["mes"] = int(mes)
    if banco and banco != "Todos":
        where.append("m.banco = :banco")
        params["banco"] = banco

    with engine.begin() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            text(
                f"""
                SELECT
                    m.id::text, m.account_id::text, m.transaction_id::text,
                    m.banco, m.conta, m.data_movimento, m.data_lancamento,
                    m.tipo_original_banco, m.descricao_original, m.descricao_normalizada,
                    m.valor, m.direcao, m.categoria_id::text,
                    COALESCE(c.name, '') AS categoria_nome,
                    m.categoria_confirmada_id::text,
                    COALESCE(cc.name, '') AS categoria_confirmada_nome,
                    m.categoria_sugerida_texto,
                    m.confianca_classificacao,
                    m.status_classificacao,
                    m.origem_arquivo,
                    m.hash_lancamento
                FROM bank_statement_movements m
                LEFT JOIN categories c  ON c.id  = m.categoria_id
                LEFT JOIN categories cc ON cc.id = m.categoria_confirmada_id
                WHERE {' AND '.join(where)}
                ORDER BY m.data_movimento DESC, m.created_at DESC
                LIMIT :limit
                """
            ),
            params,
        ).fetchall()

    return [dict(row._mapping) for row in rows]


def confirm_bank_statement_movement(
    movement_id: str,
    category_id: str,
    account_id: str | None = None,
    save_rule: bool = False,
    palavra_chave: str | None = None,
) -> tuple[bool, str]:
    if settings.MOCK_MODE:
        return False, "Modo mock ativo; confirmacao nao executada."
    engine = get_engine()
    if engine is None:
        return False, "Banco nao configurado."
    owner = _owner_id()

    with engine.begin() as conn:
        _ensure_tables(conn)
        movement = conn.execute(
            text(
                """
                SELECT *
                FROM bank_statement_movements
                WHERE id = CAST(:movement_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                LIMIT 1
                """
            ),
            {"movement_id": movement_id, "uid": owner},
        ).fetchone()
        if not movement:
            return False, "Movimento nao encontrado."

        category = conn.execute(
            text(
                """
                SELECT id::text, name, type
                FROM categories
                WHERE id = CAST(:category_id AS uuid)
                  AND (user_id = CAST(:uid AS uuid) OR user_id IS NULL)
                LIMIT 1
                """
            ),
            {"category_id": category_id, "uid": owner},
        ).fetchone()
        if not category:
            return False, "Categoria inexistente ou indisponivel para o usuario."

        final_account_id = account_id or str(movement.account_id or "")
        if not final_account_id:
            return False, "Selecione uma conta para publicar o movimento."

        account = conn.execute(
            text(
                """
                SELECT id::text, name, type
                FROM accounts
                WHERE id = CAST(:account_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                LIMIT 1
                """
            ),
            {"account_id": final_account_id, "uid": owner},
        ).fetchone()
        if not account:
            return False, "Conta nao encontrada."
        if getattr(account, "type", "") == "credit_card":
            return False, "Use uma conta bancaria, nao uma conta de cartao."

        row = {
            "descricao_original": movement.descricao_original,
            "valor": float(movement.valor or 0.0),
            "data_movimento": movement.data_movimento,
            "data_lancamento": movement.data_lancamento,
            "direcao": movement.direcao,
            "categoria_id": category.id,
            "categoria_sugerida_texto": category.name,
        }
        category_dict = {"id": category.id, "nome": category.name, "tipo": category.type}

        if movement.transaction_id:
            conn.execute(
                text(
                    """
                    UPDATE transactions
                    SET category_id = CAST(:category_id AS uuid),
                        account_id = CAST(:account_id AS uuid),
                        type = :type
                    WHERE id = CAST(:tx_id AS uuid)
                      AND user_id = CAST(:uid AS uuid)
                    """
                ),
                {
                    "category_id": category.id,
                    "account_id": final_account_id,
                    "type": _transaction_type_for(row, category_dict),
                    "tx_id": str(movement.transaction_id),
                    "uid": owner,
                },
            )
            tx_id = str(movement.transaction_id)
        else:
            tx_id = _insert_transaction(conn, owner, row, final_account_id, category_dict)

        conn.execute(
            text(
                """
                UPDATE bank_statement_movements
                SET account_id = CAST(:account_id AS uuid),
                    transaction_id = CAST(:tx_id AS uuid),
                    categoria_id = CAST(:category_id AS uuid),
                    categoria_confirmada_id = CAST(:category_id AS uuid),
                    categoria_sugerida_texto = :category_name,
                    status_classificacao = 'confirmada',
                    confianca_classificacao = 1.00,
                    updated_at = NOW()
                WHERE id = CAST(:movement_id AS uuid)
                  AND user_id = CAST(:uid AS uuid)
                """
            ),
            {
                "account_id": final_account_id,
                "tx_id": tx_id,
                "category_id": category.id,
                "category_name": category.name,
                "movement_id": movement_id,
                "uid": owner,
            },
        )

        if save_rule:
            keyword = (palavra_chave or "").strip() or " ".join(str(movement.descricao_original or "").split()[:4])
            conn.execute(
                text(
                    """
                    INSERT INTO bank_statement_classification_rules (
                        user_id, banco, tipo_original_banco, palavra_chave,
                        descricao_normalizada, category_id
                    )
                    VALUES (
                        CAST(:uid AS uuid), :banco, :tipo_original_banco, :palavra_chave,
                        :descricao_normalizada, CAST(:category_id AS uuid)
                    )
                    """
                ),
                {
                    "uid": owner,
                    "banco": movement.banco,
                    "tipo_original_banco": movement.tipo_original_banco,
                    "palavra_chave": keyword[:160],
                    "descricao_normalizada": _norm(keyword),
                    "category_id": category.id,
                },
            )

    try:
        from core.controle import _clear_controle_caches

        _clear_controle_caches()
    except Exception:
        logger.debug("Nao foi possivel limpar caches do controle financeiro.", exc_info=True)
    return True, ""

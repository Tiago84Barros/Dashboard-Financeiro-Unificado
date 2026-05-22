"""
data_pipeline/importers/investments/common.py
=============================================
Helpers compartilhados pelos parsers de importação de investimentos.

Não tem dependência de UI nem do Streamlit. Tudo aqui é puro Python/SQLAlchemy
e pode ser testado isoladamente.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Identidades / metadados
# ─────────────────────────────────────────────────────────────────────────────

B3_INSTITUTION_NAME = "B3 - Custódia Consolidada"
B3_ACCOUNT_NAME = "B3 - Carteira Consolidada"
B3_ACCOUNT_TYPE = "investment"  # respeita CHECK accounts.type
B3_INSTITUTION_TYPE = "broker"  # respeita CHECK financial_institutions.type


def make_summary(source: str) -> dict[str, Any]:
    """Cria o esqueleto do dicionário de resumo padronizado."""
    return {
        "status": "success",
        "source": source,
        "records_imported": 0,
        "transactions_imported": 0,
        "incomes_imported": 0,
        "positions_imported": 0,
        "duplicates_skipped": 0,
        "rows_skipped": 0,
        "errors": [],
        "started_at": datetime.now(tz=timezone.utc).isoformat(),
        "finished_at": None,
    }


def finalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Fecha o resumo: calcula records_imported total, status e finished_at."""
    summary["records_imported"] = (
        int(summary.get("transactions_imported", 0))
        + int(summary.get("incomes_imported", 0))
        + int(summary.get("positions_imported", 0))
    )
    if summary["records_imported"] == 0 and summary["errors"]:
        summary["status"] = "failed"
    elif summary["errors"]:
        summary["status"] = "partial_success"
    else:
        summary["status"] = "success"
    summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Parsing de valores brasileiros
# ─────────────────────────────────────────────────────────────────────────────

_BR_NUMBER = re.compile(r"^-?\d{1,3}(\.\d{3})*(,\d+)?$")


def to_float_br(value: Any) -> float | None:
    """
    Converte string em formato BR ("1.234,56") ou número em float.

    Retorna None para vazio, "-" ou valor não conversível.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s in ("-", "None"):
        return None
    # Limpa moeda, espaços e sinais simples
    s = s.replace("R$", "").replace(" ", "").strip()
    # Heurística: se tem vírgula, tratamos como decimal BR
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_date_br(value: Any) -> date | None:
    """
    Aceita 'DD/MM/YYYY', datetime, date. Retorna `date` ou None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Identificadores externos (idempotência)
# ─────────────────────────────────────────────────────────────────────────────

def make_external_id(prefix: str, parts: Iterable[Any]) -> str:
    """
    Gera external_id determinístico a partir de partes ordenadas.

    >>> make_external_id("b3neg", ["15/03/2025", "compra", "PETR4", 100, 30.00])
    'b3neg-<hash16>'
    """
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}-{hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]}"


# ─────────────────────────────────────────────────────────────────────────────
# Classificação de ticker / movimentação
# ─────────────────────────────────────────────────────────────────────────────

_FII_SUFFIXES = {"11", "11B", "11P"}
_FIXED_INCOME_PREFIXES = ("TESOURO", "LFT", "LTN", "NTN", "CDB", "LCI", "LCA", "CRI", "CRA")


def classify_ticker(ticker: str) -> str:
    """
    Mapeia o sufixo do ticker para a classe canônica em `assets.class`.

    Domínios válidos: 'stock', 'reit', 'etf', 'fixed_income', 'crypto', 'other'.
    """
    t = (ticker or "").strip().upper()
    if not t:
        return "other"
    if any(t.startswith(p) for p in _FIXED_INCOME_PREFIXES):
        return "fixed_income"
    if len(t) > 4:
        suffix = t[4:]
        if suffix in _FII_SUFFIXES:
            return "reit"
    if t.endswith(("3", "4", "5", "6")):
        return "stock"
    return "stock"


# Movimentos da B3-Movimentação que entram em `investment_transactions`.
# IMPORTANTE: "compra"/"venda" puras NÃO entram aqui — são canônicas do
# arquivo Negociação e ficam em _SKIP_TYPES_B3 para evitar duplicidade.
_TX_MAP_B3 = {
    "transferência_credito":                 "buy",
    "transferência_debito":                  "sell",
    "bonificação em ativos":                 "buy",
    "desdobro":                              "buy",
    "fração em ativos":                      "buy",
    "leilão de fração":                      "sell",
    "aplicação":                             "buy",
    "resgate antecipado":                    "sell",
}

# Movimentos da B3 que entram em `dividends`
_INCOME_MAP_B3 = {
    "dividendo":                              "dividend",
    "juros sobre capital próprio":            "jcp",
    "rendimento":                             "reit_income",
    "reembolso":                              "reit_income",
    "pagamento de prêmio/rendimentos":        "reit_income",
    "pagamento de rendimentos":               "reit_income",
    "direito de subscrição":                  "other",
    "direitos de subscrição - não exercido":  "other",
    "cessão de direitos":                     "other",
    "cessão de direitos - solicitada":        "other",
    "amortização":                            "amortization",
    "amortização de fii":                     "amortization",
}

# Movimentos ignorados (já cobertos por outro arquivo ou irrelevantes).
# "compra"/"venda" do arquivo Movimentação são duplicatas do arquivo
# Negociação, então ficam aqui.
_SKIP_TYPES_B3 = {
    "compra",
    "venda",
    "empréstimo",
    "atualização",
    "transf. sem financeiro",
    "transferência - liquidação",
}


def classify_movement(mov: str, entrada: str = "") -> tuple[str, str] | None:
    """
    Retorna (categoria, tipo_canônico) ou None se a movimentação deve ser
    ignorada.

    - categoria ∈ {'transaction', 'income', 'skip'}
    - tipo_canônico:
        * transaction: 'buy' | 'sell'
        * income:      'dividend' | 'jcp' | 'reit_income' | 'amortization' | 'other'
    """
    if not mov:
        return None
    mov_lower = str(mov).strip().lower()
    entrada_lower = str(entrada).strip().lower() if entrada else ""

    if mov_lower in _SKIP_TYPES_B3:
        return ("skip", "")
    if any(mov_lower.startswith(s) for s in _SKIP_TYPES_B3):
        return ("skip", "")

    if mov_lower in _INCOME_MAP_B3:
        return ("income", _INCOME_MAP_B3[mov_lower])

    # Chave composta tipo+entrada para resolver "transferência" / "bonificação"
    chave = f"{mov_lower}_{entrada_lower}" if entrada_lower else mov_lower
    if chave in _TX_MAP_B3:
        return ("transaction", _TX_MAP_B3[chave])
    if mov_lower in _TX_MAP_B3:
        return ("transaction", _TX_MAP_B3[mov_lower])

    return None


def parse_ticker_from_produto(produto: str) -> tuple[str, str]:
    """
    Extrai (ticker, nome) do campo Produto da B3.
    "BBAS3 - BANCO DO BRASIL" → ("BBAS3", "BANCO DO BRASIL")
    """
    s = str(produto or "").strip()
    if not s:
        return ("", "")
    parts = s.split(" - ", 1)
    ticker = parts[0].strip().upper()
    name = parts[1].strip() if len(parts) > 1 else ticker
    return (ticker, name)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de banco — get_or_create idempotentes
# ─────────────────────────────────────────────────────────────────────────────

def ensure_external_id_columns(engine: Engine) -> None:
    """
    Garante que `investment_transactions.external_id` e `dividends.external_id`
    existam. Idempotente — pode ser chamado em toda importação.

    A migração definitiva está em `etl/schema_setup.py`. Esta função é uma
    rede de segurança para bancos que ainda não rodaram `criar_schema()`.
    """
    statements = [
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'investment_transactions'
                  AND column_name = 'external_id'
            ) THEN
                ALTER TABLE investment_transactions ADD COLUMN external_id VARCHAR(64);
            END IF;
        END $$
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_investment_transactions_external_id
            ON investment_transactions(external_id)
            WHERE external_id IS NOT NULL
        """,
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'dividends'
                  AND column_name = 'external_id'
            ) THEN
                ALTER TABLE dividends ADD COLUMN external_id VARCHAR(64);
            END IF;
        END $$
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            ux_dividends_external_id
            ON dividends(external_id)
            WHERE external_id IS NOT NULL
        """,
    ]
    try:
        with engine.begin() as conn:
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except Exception as exc:  # noqa: BLE001
                    logger.debug("ensure_external_id_columns: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_external_id_columns falhou: %s", exc)


def get_or_create_institution(conn: Connection, name: str, inst_type: str) -> str:
    """
    Retorna o UUID da financial_institution. Cria se não existir.
    """
    row = conn.execute(
        text("SELECT id FROM financial_institutions WHERE name = :name LIMIT 1"),
        {"name": name},
    ).fetchone()
    if row:
        return str(row[0])

    row = conn.execute(
        text("""
            INSERT INTO financial_institutions (name, type, active)
            VALUES (:name, :type, TRUE)
            RETURNING id
        """),
        {"name": name, "type": inst_type},
    ).fetchone()
    return str(row[0])


def get_or_create_account(
    conn: Connection,
    user_id: str,
    institution_id: str,
    name: str,
    account_type: str = "investment",
    currency: str = "BRL",
) -> str:
    """
    Retorna o UUID da account. Cria se não existir.
    Filtra por (user_id, name) — o nome canônico é estável por instituição.
    """
    row = conn.execute(
        text("""
            SELECT id FROM accounts
            WHERE user_id = :uid AND name = :name
            LIMIT 1
        """),
        {"uid": user_id, "name": name},
    ).fetchone()
    if row:
        return str(row[0])

    row = conn.execute(
        text("""
            INSERT INTO accounts
                (user_id, financial_institution_id, name, type, currency, active)
            VALUES
                (:uid, :iid, :name, :type, :ccy, TRUE)
            RETURNING id
        """),
        {
            "uid": user_id,
            "iid": institution_id,
            "name": name,
            "type": account_type,
            "ccy": currency,
        },
    ).fetchone()
    return str(row[0])


def get_or_create_asset(
    conn: Connection,
    ticker: str,
    name: str | None = None,
    asset_class: str | None = None,
    currency: str = "BRL",
) -> str:
    """
    Retorna o UUID do asset. Cria se não existir.
    """
    t = (ticker or "").strip().upper()
    if not t:
        raise ValueError("ticker vazio em get_or_create_asset")

    row = conn.execute(
        text("SELECT id FROM assets WHERE ticker = :t LIMIT 1"),
        {"t": t},
    ).fetchone()
    if row:
        return str(row[0])

    cls = asset_class or classify_ticker(t)
    row = conn.execute(
        text("""
            INSERT INTO assets (ticker, name, class, currency)
            VALUES (:t, :n, :c, :ccy)
            ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """),
        {"t": t, "n": (name or t)[:200], "c": cls, "ccy": currency},
    ).fetchone()
    return str(row[0])


def get_or_create_b3_account(conn: Connection, user_id: str) -> str:
    """
    Retorna o account_id da conta agregadora "B3 - Carteira Consolidada".
    A instituição é criada/buscada por nome.
    """
    inst_id = get_or_create_institution(conn, B3_INSTITUTION_NAME, B3_INSTITUTION_TYPE)
    return get_or_create_account(
        conn,
        user_id=user_id,
        institution_id=inst_id,
        name=B3_ACCOUNT_NAME,
        account_type=B3_ACCOUNT_TYPE,
        currency="BRL",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Insert idempotente por external_id
# ─────────────────────────────────────────────────────────────────────────────

def insert_investment_transaction(
    conn: Connection,
    *,
    user_id: str,
    asset_id: str,
    tx_type: str,
    quantity: float,
    unit_price: float,
    fees: float,
    transaction_date: date,
    broker: str | None,
    external_id: str,
) -> str | None:
    """
    Insere uma operação em `investment_transactions`. Retorna o UUID criado
    ou None se já existia (duplicata).
    """
    if tx_type not in ("buy", "sell"):
        raise ValueError(f"tx_type invalido: {tx_type}")
    # ON CONFLICT precisa repetir o predicado do indice parcial
    # ux_investment_transactions_external_id (WHERE external_id IS NOT NULL),
    # senao o Postgres lanca InvalidColumnReference.
    row = conn.execute(
        text("""
            INSERT INTO investment_transactions
                (user_id, asset_id, type, quantity, unit_price, fees,
                 transaction_date, broker, external_id)
            VALUES
                (:uid, :aid, :type, :qty, :price, :fees,
                 :tdate, :broker, :ext)
            ON CONFLICT (external_id) WHERE external_id IS NOT NULL DO NOTHING
            RETURNING id
        """),
        {
            "uid":    user_id,
            "aid":    asset_id,
            "type":   tx_type,
            "qty":    quantity,
            "price":  unit_price,
            "fees":   fees,
            "tdate":  transaction_date,
            "broker": broker,
            "ext":    external_id,
        },
    ).fetchone()
    return str(row[0]) if row else None


def insert_dividend(
    conn: Connection,
    *,
    user_id: str,
    asset_id: str,
    div_type: str,
    amount_per_unit: float,
    quantity: float,
    total_amount: float,
    ex_date: date | None,
    payment_date: date,
    external_id: str,
) -> str | None:
    """
    Insere um provento em `dividends`. Retorna o UUID ou None se duplicata.
    """
    if div_type not in ("dividend", "jcp", "reit_income", "amortization", "other"):
        raise ValueError(f"div_type invalido: {div_type}")
    # ON CONFLICT precisa repetir o predicado do indice parcial
    # ux_dividends_external_id (WHERE external_id IS NOT NULL).
    row = conn.execute(
        text("""
            INSERT INTO dividends
                (user_id, asset_id, type, amount_per_unit, quantity,
                 total_amount, ex_date, payment_date, external_id)
            VALUES
                (:uid, :aid, :type, :apu, :qty, :total, :exd, :pd, :ext)
            ON CONFLICT (external_id) WHERE external_id IS NOT NULL DO NOTHING
            RETURNING id
        """),
        {
            "uid":   user_id,
            "aid":   asset_id,
            "type":  div_type,
            "apu":   amount_per_unit,
            "qty":   quantity,
            "total": total_amount,
            "exd":   ex_date,
            "pd":    payment_date,
            "ext":   external_id,
        },
    ).fetchone()
    return str(row[0]) if row else None


# ─────────────────────────────────────────────────────────────────────────────
# Sanitização para logs e erros
# ─────────────────────────────────────────────────────────────────────────────

_SECRET_PATTERNS = (
    re.compile(r"postgresql\+?\w*://[^\s\"']+"),
    re.compile(r"sqlite:///[^\s\"']+"),
    re.compile(r"(?i)(senha|password|pwd|token|key)\s*[:=]\s*\S+"),
)


def safe_error(exc: Exception | str, max_len: int = 200) -> str:
    """
    Mensagem de erro segura para logar e exibir.

    Remove connection strings, senhas e tokens; limita tamanho.
    """
    msg = str(exc).strip()
    for pat in _SECRET_PATTERNS:
        msg = pat.sub("***", msg)
    return msg[:max_len]

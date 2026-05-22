"""
data_pipeline/importers/investments/nomad_pdf.py
================================================
Parser de notas de corretagem da Nomad (.pdf) — múltiplos arquivos por lote.

Suporta dois formatos emitidos pelos clearings parceiros da Nomad:
  * Apex Clearing — datas ISO (YYYY-MM-DD), bilíngue PT/EN.
  * DriveWealth   — datas MM/DD/YYYY, inglês.

A interface da UI faz upload de N PDFs de uma vez; este módulo processa todos
em uma única conexão SQLAlchemy, com SAVEPOINT por operação para garantir
que um arquivo problemático não derrube o lote.

Valores ficam em USD (assets.currency='USD'). Conversão para BRL é
responsabilidade da camada de visualização.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.engine import Engine

from core.config import settings
from .common import (
    finalize_summary,
    get_or_create_account,
    get_or_create_asset,
    get_or_create_institution,
    ensure_external_id_columns,
    insert_investment_transaction,
    make_external_id,
    make_summary,
    safe_error,
)

logger = logging.getLogger(__name__)

SOURCE = "nomad_pdf"

NOMAD_INSTITUTION_NAME = "Nomad Investment Services Inc."
NOMAD_INSTITUTION_TYPE = "broker"
NOMAD_ACCOUNT_NAME = "Nomad - Carteira Internacional"
NOMAD_ACCOUNT_TYPE = "investment"
NOMAD_CURRENCY = "USD"

# Mapeamento opcional ticker→nome amigável (usado quando o PDF só traz o
# ticker e não o nome do produto)
NAME_MAP: dict[str, str] = {
    "SPY":  "SPDR S&P 500 ETF Trust",
    "IEFA": "iShares Core MSCI EAFE ETF",
    "SGOV": "iShares 0-3 Month Treasury Bond ETF",
    "TFLO": "iShares Treasury Floating Rate Bond ETF",
    "QQQ":  "Invesco QQQ Trust",
    "VTI":  "Vanguard Total Stock Market ETF",
    "VEA":  "Vanguard FTSE Developed Markets ETF",
    "BND":  "Vanguard Total Bond Market ETF",
    "AGG":  "iShares Core U.S. Aggregate Bond ETF",
    "IVV":  "iShares Core S&P 500 ETF",
    "VOO":  "Vanguard S&P 500 ETF",
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de parsing de números (mais permissivos do que parse BR padrão —
# PDFs Nomad usam tanto vírgula quanto ponto, e parênteses para negativos)
# ─────────────────────────────────────────────────────────────────────────────

def _to_float_usd(value: Any) -> float:
    if value is None:
        return 0.0
    raw = str(value).strip()
    if not raw:
        return 0.0
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.replace("US$", "").replace("R$", "").replace("$", "")
    raw = raw.replace("(", "").replace(")", "").replace(" ", "").strip()
    if not raw or raw == "-":
        return 0.0
    try:
        # "1,234.56" → "1234.56" (en) | "1.234,56" → "1234.56" (pt)
        if "," in raw and "." in raw:
            if raw.rfind(",") > raw.rfind("."):
                raw = raw.replace(".", "").replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "," in raw:
            raw = raw.replace(",", ".")
        number = float(raw)
        return -number if negative else number
    except (ValueError, TypeError):
        return 0.0


def _parse_iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _parse_us_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Detecção e extração de formato
# ─────────────────────────────────────────────────────────────────────────────

def _extract_text(pdf_bytes: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber nao instalado. Adicione 'pdfplumber' ao requirements."
        ) from exc
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _is_apex(text: str) -> bool:
    has_iso_pair = bool(re.search(r"\d{4}-\d{2}-\d{2}\s+\d{4}-\d{2}-\d{2}", text))
    lower = text.lower()
    has_action_or_header = any(
        marker in lower
        for marker in (
            "you bought", "you sold",
            "você comprou", "voce comprou",
            "você vendeu", "voce vendeu",
            "símbolo", "simbolo",
            "valor líquido", "valor liquido",
        )
    )
    return has_iso_pair and has_action_or_header


def _is_drivewealth(text: str) -> bool:
    return "Principal Amount" in text and "DriveWealth" in text


def _is_monthly_statement(text: str, filename: str = "") -> bool:
    """
    Identifica PDFs de extrato mensal (Apex Monthly Statement / Nomad).
    Esses arquivos consolidam o que as notas de negociação individuais já
    trazem — operações já são capturadas pelas notas, então pular é seguro.
    """
    fn = filename.lower()
    if "monthly_statement" in fn or "monthly-statement" in fn:
        return True
    lower = text.lower()
    monthly_markers = (
        "monthly statement",
        "account statement",
        "statement period",
        "beginning balance",
        "ending balance",
        "extrato mensal",
        "período do extrato",
        "periodo do extrato",
    )
    hits = sum(1 for m in monthly_markers if m in lower)
    return hits >= 2


# ─────────────────────────────────────────────────────────────────────────────
# Parsers de cada formato — devolvem lista de trades canônicos
# ─────────────────────────────────────────────────────────────────────────────

_NUM = r"-?[\d.,]+"

_APEX_ROW = re.compile(
    rf"(\d{{4}}-\d{{2}}-\d{{2}})\s+(\d{{4}}-\d{{2}}-\d{{2}})\s+"
    rf"([A-Z]{{1,10}})\s+({_NUM})\s+({_NUM})\s+"
    rf"({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})\s+({_NUM})"
)
_APEX_DESC = re.compile(
    r"DESC:\s*(.+?)(?:\s+Trade#:\s*(\S+))?(?:\s+(?:CAP|TETO):|$)",
    flags=re.IGNORECASE,
)


def _parse_apex(text: str) -> list[dict]:
    lines = text.split("\n")
    trades: list[dict] = []
    action = "buy"
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lower = line.lower()
        if "you bought" in lower or "você comprou" in lower or "voce comprou" in lower:
            action = "buy"
        elif "you sold" in lower or "você vendeu" in lower or "voce vendeu" in lower:
            action = "sell"

        m = _APEX_ROW.search(line)
        if not m:
            continue
        (trade_date_s, _settle, symbol, qty_s, price_s,
         _gross, _comm, _fee, _add, net_s) = m.groups()

        symbol = symbol.upper()
        name = NAME_MAP.get(symbol, symbol)

        trade_number = ""
        if i + 1 < len(lines):
            dm = _APEX_DESC.search(lines[i + 1])
            if dm:
                name = (dm.group(1) or name).strip()
                trade_number = dm.group(2) or ""

        quantity = abs(_to_float_usd(qty_s))
        price = _to_float_usd(price_s)
        net = abs(_to_float_usd(net_s))
        if quantity <= 0 or price <= 0 or net <= 0:
            continue

        tx_date = _parse_iso_date(trade_date_s)
        if tx_date is None:
            continue

        ext_raw = trade_number or f"{trade_date_s}|{symbol}|{qty_s}|{price_s}|{net_s}"
        trades.append({
            "symbol":     symbol,
            "name":       name[:200],
            "action":     action,
            "quantity":   quantity,
            "price_usd":  price,
            "net_usd":    net,
            "trade_date": tx_date,
            "ext_id":     make_external_id("nomad-apex", [ext_raw]),
        })
    return trades


_DW_ROW = re.compile(
    rf"^([A-Z]{{2,10}})\s+(.+?)\s+(?:M|C)\s+(Buy|Sell)\s+"
    rf"\d+:\d+:\d+\s+(?:AM|PM)\s+({_NUM})\s+({_NUM})\s+"
    r"(\d+/\d+/\d{4})\s+(\d+/\d+/\d{4})"
)
_DW_NET = re.compile(r"Net Amount\s+(\(?\$?[\d.,]+\)?)")


def _parse_drivewealth(text: str) -> list[dict]:
    lines = text.split("\n")
    trades: list[dict] = []
    for i, raw_line in enumerate(lines):
        m = _DW_ROW.match(raw_line.strip())
        if not m:
            continue
        symbol, desc, action_raw, qty_s, price_s, trade_date_s, _settle_s = m.groups()
        symbol = symbol.upper()
        quantity = abs(_to_float_usd(qty_s))
        price = _to_float_usd(price_s)
        net = quantity * price

        # "Net Amount" às vezes vem em linha subsequente
        for line in lines[i + 1 : min(i + 9, len(lines))]:
            nm = _DW_NET.search(line)
            if nm:
                net = abs(_to_float_usd(nm.group(1)))
                break

        if quantity <= 0 or price <= 0:
            continue

        tx_date = _parse_us_date(trade_date_s)
        if tx_date is None:
            continue

        name = NAME_MAP.get(symbol, (desc or symbol).strip())
        trades.append({
            "symbol":     symbol,
            "name":       name[:200],
            "action":     action_raw.lower(),
            "quantity":   quantity,
            "price_usd":  price,
            "net_usd":    abs(net),
            "trade_date": tx_date,
            "ext_id":     make_external_id(
                "nomad-dw", [trade_date_s, symbol, qty_s, price_s],
            ),
        })
    return trades


# ─────────────────────────────────────────────────────────────────────────────
# Setup das entidades da Nomad
# ─────────────────────────────────────────────────────────────────────────────

def _get_or_create_nomad_account(conn, user_id: str) -> str:
    inst_id = get_or_create_institution(
        conn, NOMAD_INSTITUTION_NAME, NOMAD_INSTITUTION_TYPE,
    )
    return get_or_create_account(
        conn,
        user_id=user_id,
        institution_id=inst_id,
        name=NOMAD_ACCOUNT_NAME,
        account_type=NOMAD_ACCOUNT_TYPE,
        currency=NOMAD_CURRENCY,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point publico — recebe lista de arquivos
# ─────────────────────────────────────────────────────────────────────────────

def parse(files: list[tuple[str, bytes]] | bytes, engine: Engine) -> dict[str, Any]:
    """
    Processa um ou vários PDFs Nomad em um único lote.

    `files` pode ser:
      * lista de (filename, bytes) — caso "vários arquivos de uma vez";
      * bytes simples — caso "1 arquivo" (compatibilidade com chamadas legadas).
    """
    summary = make_summary(SOURCE)
    user_id = settings.OWNER_USER_ID
    if not user_id:
        summary["status"] = "failed"
        summary["errors"].append("OWNER_USER_ID nao configurado.")
        return finalize_summary(summary)

    # Normaliza para lista de (filename, bytes)
    file_list: list[tuple[str, bytes]]
    if isinstance(files, (bytes, bytearray)):
        file_list = [("nomad.pdf", bytes(files))]
    else:
        file_list = [(str(name), bytes(data)) for name, data in files]

    if not file_list:
        summary["status"] = "failed"
        summary["errors"].append("Nenhum arquivo enviado.")
        return finalize_summary(summary)

    ensure_external_id_columns(engine)

    # Extracao + parsing de cada PDF em memoria (sem hit no banco)
    parsed: list[tuple[str, list[dict]]] = []  # (filename, trades)
    for filename, file_bytes in file_list:
        try:
            text = _extract_text(file_bytes)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(
                f"[{filename}] Erro ao ler PDF: {safe_error(exc)}"
            )
            continue

        # Extratos mensais consolidam o que as notas individuais ja trazem —
        # nao sao "formato desconhecido", apenas redundantes. Pular sem erro.
        if _is_monthly_statement(text, filename):
            summary["files_skipped"] += 1
            summary["files_skipped_notes"].append(
                f"[{filename}] Extrato mensal: operacoes ja vem das notas "
                f"individuais — arquivo pulado intencionalmente."
            )
            continue

        if _is_apex(text):
            trades = _parse_apex(text)
            fmt = "Apex Clearing"
        elif _is_drivewealth(text):
            trades = _parse_drivewealth(text)
            fmt = "DriveWealth"
        else:
            summary["errors"].append(
                f"[{filename}] Formato de PDF nao reconhecido (nem Apex nem DriveWealth)."
            )
            continue

        if not trades:
            summary["errors"].append(
                f"[{filename}] ({fmt}) Nenhuma negociacao encontrada."
            )
            continue
        parsed.append((filename, trades))

    if not parsed:
        return finalize_summary(summary)

    # Persistencia: 1 conexao, transacao externa, savepoint por trade.
    with engine.connect() as conn:
        with conn.begin():
            try:
                _ = _get_or_create_nomad_account(conn, user_id)
            except Exception as exc:  # noqa: BLE001
                summary["status"] = "failed"
                summary["errors"].append(
                    f"Falha ao preparar conta Nomad: {safe_error(exc)}"
                )
                return finalize_summary(summary)

            for filename, trades in parsed:
                for trade in trades:
                    try:
                        with conn.begin_nested():
                            _persist_trade(conn, user_id, trade, summary)
                    except Exception as exc:  # noqa: BLE001
                        summary["errors"].append(
                            f"[{filename}] {trade.get('symbol', '?')}: {safe_error(exc)}"
                        )

    return finalize_summary(summary)


def _persist_trade(conn, user_id: str, trade: dict, summary: dict) -> None:
    """Grava uma operação Nomad em investment_transactions."""
    asset_id = get_or_create_asset(
        conn,
        ticker=trade["symbol"],
        name=trade["name"],
        asset_class="etf",     # universo Nomad é majoritariamente ETF
        currency=NOMAD_CURRENCY,
    )

    if trade["action"] not in ("buy", "sell"):
        summary["rows_skipped"] += 1
        return

    new_id = insert_investment_transaction(
        conn,
        user_id=user_id,
        asset_id=asset_id,
        tx_type=trade["action"],
        quantity=trade["quantity"],
        unit_price=trade["price_usd"],
        fees=0.0,
        transaction_date=trade["trade_date"],
        broker="Nomad",
        external_id=trade["ext_id"],
    )
    if new_id is None:
        summary["duplicates_skipped"] += 1
    else:
        summary["transactions_imported"] += 1

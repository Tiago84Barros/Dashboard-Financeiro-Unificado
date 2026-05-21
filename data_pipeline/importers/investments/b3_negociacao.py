"""
data_pipeline/importers/investments/b3_negociacao.py
====================================================
Parser do arquivo de Negociação exportado pelo portal da B3.
(investidor.b3.com.br → Extratos e Informativos → Negociação)

Colunas esperadas (em ordem):
    Data do Negócio | Tipo de Movimentação | Mercado | Prazo/Vencimento
    Instituição | Código de Negociação | Quantidade | Preço | Valor

Comportamento:
  * Só importa Compra/Venda (movimentações financeiras puras).
  * Cria ativos novos automaticamente em `assets`.
  * Cria instituição/conta agregadora "B3 - Carteira Consolidada".
  * Idempotente via `investment_transactions.external_id`.
"""
from __future__ import annotations

import io
import logging
from typing import Any

import openpyxl
from sqlalchemy.engine import Engine

from core.config import settings
from .common import (
    finalize_summary,
    get_or_create_asset,
    get_or_create_b3_account,
    ensure_external_id_columns,
    insert_investment_transaction,
    make_external_id,
    make_summary,
    parse_date_br,
    parse_ticker_from_produto,
    safe_error,
    to_float_br,
)

logger = logging.getLogger(__name__)

SOURCE = "b3_negociacao"
SHEET_HINT = "negocia"  # encontra "Negociação", "Negociacao", etc.


def parse(file_bytes: bytes, engine: Engine) -> dict[str, Any]:
    """
    Processa o XLSX de Negociação da B3 e grava as operações no app4.

    Retorna o resumo padronizado descrito em
    `.claude/skills/investment-imports/SKILL.md`.
    """
    summary = make_summary(SOURCE)
    user_id = settings.OWNER_USER_ID
    if not user_id:
        summary["status"] = "failed"
        summary["errors"].append("OWNER_USER_ID nao configurado.")
        return finalize_summary(summary)

    ensure_external_id_columns(engine)

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as exc:
        summary["status"] = "failed"
        summary["errors"].append(f"Arquivo invalido: {safe_error(exc)}")
        return finalize_summary(summary)

    sheet = None
    for name in wb.sheetnames:
        if SHEET_HINT in name.lower():
            sheet = wb[name]
            break
    if sheet is None:
        summary["status"] = "failed"
        summary["errors"].append(
            f"Aba 'Negociacao' nao encontrada. Abas: {wb.sheetnames}"
        )
        return finalize_summary(summary)

    with engine.begin() as conn:
        try:
            account_id = get_or_create_b3_account(conn, user_id)
        except Exception as exc:
            summary["status"] = "failed"
            summary["errors"].append(
                f"Falha ao preparar conta B3: {safe_error(exc)}"
            )
            return finalize_summary(summary)

        for i, row in enumerate(sheet.iter_rows(values_only=True)):
            if i == 0:
                continue
            try:
                _process_row(conn, row, user_id=user_id, summary=summary,
                             row_number=i + 1, account_label="B3")
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"Linha {i + 1}: {safe_error(exc)}"
                )

    # account_id não é gravado em investment_transactions (não há FK direta).
    # Mantemos a conta criada apenas para consistência operacional com o
    # módulo de relatórios.
    _ = account_id  # noqa: F841 — variável intencional para futura extensão

    return finalize_summary(summary)


def _process_row(conn, row, *, user_id: str, summary: dict, row_number: int,
                 account_label: str) -> None:
    """Processa uma linha do XLSX de Negociação."""
    # Estrutura: Data, Tipo, Mercado, Prazo, Inst, Ticker, Qtd, Preco, Valor
    if not row or len(row) < 9:
        summary["rows_skipped"] += 1
        return

    data_raw, tipo_raw, mercado_raw, _prazo, _inst, ticker_raw, qtd_raw, preco_raw, valor_raw = row[:9]

    if not ticker_raw or not data_raw or not tipo_raw:
        summary["rows_skipped"] += 1
        return

    tipo_norm = str(tipo_raw).strip().lower()
    if tipo_norm == "compra":
        tx_type = "buy"
    elif tipo_norm == "venda":
        tx_type = "sell"
    else:
        summary["rows_skipped"] += 1
        return

    ticker_clean, name_from_produto = parse_ticker_from_produto(str(ticker_raw))
    if not ticker_clean:
        summary["rows_skipped"] += 1
        return

    qtd = to_float_br(qtd_raw)
    preco = to_float_br(preco_raw)
    valor = to_float_br(valor_raw)
    if qtd is None or qtd <= 0:
        summary["rows_skipped"] += 1
        return

    if (valor is None or valor == 0) and preco and preco > 0:
        valor = round(preco * qtd, 2)
    if (preco is None or preco == 0) and valor and valor > 0 and qtd > 0:
        preco = round(valor / qtd, 6)
    preco = preco or 0.0
    valor = valor or 0.0

    tx_date = parse_date_br(data_raw)
    if tx_date is None:
        summary["rows_skipped"] += 1
        return

    ext_id = make_external_id(
        "b3neg",
        [tx_date.isoformat(), tx_type, ticker_clean, qtd, preco, str(mercado_raw or "")],
    )

    asset_id = get_or_create_asset(
        conn,
        ticker=ticker_clean,
        name=name_from_produto or ticker_clean,
    )

    new_id = insert_investment_transaction(
        conn,
        user_id=user_id,
        asset_id=asset_id,
        tx_type=tx_type,
        quantity=qtd,
        unit_price=preco,
        fees=0.0,
        transaction_date=tx_date,
        broker=account_label,
        external_id=ext_id,
    )
    if new_id is None:
        summary["duplicates_skipped"] += 1
    else:
        summary["transactions_imported"] += 1

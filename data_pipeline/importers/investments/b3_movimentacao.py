"""
data_pipeline/importers/investments/b3_movimentacao.py
======================================================
Parser do arquivo de Movimentação exportado pelo portal da B3.
(investidor.b3.com.br → Extratos e Informativos → Movimentação)

Colunas esperadas:
    Entrada/Saída | Data | Movimentação | Produto | Instituição |
    Quantidade | Preço unitário | Valor da Operação

Importa:
  * Dividendos, JCP, Rendimentos de FII, Amortizações em `dividends`.
  * Bonificações, desdobros e operações sem contrapartida financeira em
    `investment_transactions` (preço/valor = 0 quando aplicável).

Ignora (com contagem):
  * Compra/venda comum (já vem do arquivo Negociação).
  * Eventos não suportados pelo schema atual.

Idempotência via `external_id` em ambas as tabelas.
"""
from __future__ import annotations

import io
import logging
from typing import Any

import openpyxl
from sqlalchemy.engine import Engine

from core.config import settings
from .common import (
    classify_movement,
    classify_ticker,
    ensure_external_id_columns,
    finalize_summary,
    get_or_create_asset,
    get_or_create_b3_account,
    insert_dividend,
    insert_investment_transaction,
    make_external_id,
    make_summary,
    parse_date_br,
    parse_ticker_from_produto,
    safe_error,
    to_float_br,
)

logger = logging.getLogger(__name__)

SOURCE = "b3_movimentacao"
SHEET_HINT = "movimenta"


def parse(file_bytes: bytes, engine: Engine) -> dict[str, Any]:
    """
    Processa o XLSX de Movimentação da B3 e grava proventos/eventos no app4.
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
            f"Aba 'Movimentacao' nao encontrada. Abas: {wb.sheetnames}"
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
                             row_number=i + 1)
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"Linha {i + 1}: {safe_error(exc)}"
                )

    _ = account_id  # noqa: F841 — reservado para futura ligação direta

    return finalize_summary(summary)


def _process_row(conn, row, *, user_id: str, summary: dict, row_number: int) -> None:
    if not row or len(row) < 8:
        summary["rows_skipped"] += 1
        return

    entrada, data_raw, mov_raw, produto_raw, _inst, qtd_raw, preco_raw, valor_raw = row[:8]
    if not mov_raw or not produto_raw or not data_raw:
        summary["rows_skipped"] += 1
        return

    classification = classify_movement(str(mov_raw), str(entrada or ""))
    if classification is None:
        summary["rows_skipped"] += 1
        return

    category, canonical_type = classification
    if category == "skip":
        summary["rows_skipped"] += 1
        return

    ticker_clean, name_from_produto = parse_ticker_from_produto(str(produto_raw))
    if not ticker_clean:
        summary["rows_skipped"] += 1
        return

    tx_date = parse_date_br(data_raw)
    if tx_date is None:
        summary["rows_skipped"] += 1
        return

    qtd = to_float_br(qtd_raw)
    preco = to_float_br(preco_raw)
    valor = to_float_br(valor_raw)

    ext_id = make_external_id(
        "b3mov",
        [tx_date.isoformat(), str(mov_raw).strip().lower(),
         ticker_clean, str(entrada or "").lower(), qtd_raw, valor_raw],
    )

    asset_id = get_or_create_asset(
        conn,
        ticker=ticker_clean,
        name=name_from_produto or ticker_clean,
        asset_class=classify_ticker(ticker_clean),
    )

    if category == "income":
        if valor is None or valor <= 0:
            summary["rows_skipped"] += 1
            return
        # B3 Movimentação não traz amount_per_unit separado. Convenção:
        # quando há quantidade, usar amount_per_unit = valor / quantidade.
        # Sem quantidade, quantidade = 1 e amount_per_unit = valor.
        if qtd and qtd > 0:
            apu = round(valor / qtd, 6)
            quantity_used = qtd
        else:
            apu = valor
            quantity_used = 1.0
        new_id = insert_dividend(
            conn,
            user_id=user_id,
            asset_id=asset_id,
            div_type=canonical_type,
            amount_per_unit=apu,
            quantity=quantity_used,
            total_amount=valor,
            ex_date=None,
            payment_date=tx_date,
            external_id=ext_id,
        )
        if new_id is None:
            summary["duplicates_skipped"] += 1
        else:
            summary["incomes_imported"] += 1
        return

    # category == "transaction"
    if canonical_type not in ("buy", "sell"):
        summary["rows_skipped"] += 1
        return
    if qtd is None or qtd <= 0:
        summary["rows_skipped"] += 1
        return

    if (preco is None or preco == 0) and valor and valor > 0 and qtd > 0:
        preco = round(valor / qtd, 6)
    preco = preco or 0.0

    new_id = insert_investment_transaction(
        conn,
        user_id=user_id,
        asset_id=asset_id,
        tx_type=canonical_type,
        quantity=qtd,
        unit_price=preco,
        fees=0.0,
        transaction_date=tx_date,
        broker="B3",
        external_id=ext_id,
    )
    if new_id is None:
        summary["duplicates_skipped"] += 1
    else:
        summary["transactions_imported"] += 1

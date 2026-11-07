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

Guarda TODAS as linhas, cruas, em `investment_movement_events` (2026-09-24).
Bonificação, desdobro, subscrição e transferência continuam fora de
`investment_transactions` -- como buy/sell de preço zero derrubavam o preço
médio --, mas eram jogadas fora, e o arquivo não fica guardado: o extrato de
negociação fechava com a posição em 6 de 15 ativos sem que desse para saber
por quê. A interpretação desses fatos mora em `core/movimentacao_b3.py`.

Idempotência via `external_id` em ambas as tabelas.

Otimização (2026-05-22):
  Antes: 1 SAVEPOINT + 3-4 queries por linha do XLSX. Depois: parse 100%
  em memória + ~7 queries em batch (independente do número de linhas).
"""
from __future__ import annotations

import io
import logging
from typing import Any

import openpyxl
from sqlalchemy.engine import Engine

from core.config import settings

from .common import (
    TIPO_REEMBOLSO_BTC,
    ajustar_quantidades_dividendos,
    aviso_reembolso_sem_084,
    aviso_sem_quantidade,
    batch_filter_existing_external_ids,
    batch_get_or_create_assets,
    batch_insert_dividends,
    batch_insert_investment_transactions,
    classify_movement,
    classify_ticker,
    corrigir_tipo_reembolso,
    ensure_external_id_columns,
    ensure_movement_events_table,
    finalize_summary,
    get_or_create_b3_account,
    insert_movement_events,
    make_external_id,
    make_summary,
    migracao_083_aplicada,
    migracao_084_aplicada,
    parse_date_br,
    parse_ticker_from_produto,
    quantidade_e_valor_unitario,
    safe_error,
    tipo_gravavel,
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

    # Estratégia em 2 fases (ver b3_negociacao.py para racional).
    tx_candidates: list[dict] = []
    div_candidates: list[dict] = []
    ev_candidates: list[dict] = []
    ocorrencias: dict[tuple, int] = {}

    for i, row in enumerate(sheet.iter_rows(values_only=True)):
        if i == 0:
            continue
        try:
            ev = _parse_event(row, user_id, ocorrencias)
        except Exception:  # noqa: BLE001 -- o fato cru nunca derruba o resto
            ev = None
        if ev is not None:
            ev_candidates.append(ev)
        try:
            parsed = _parse_row(row)
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"Linha {i + 1}: {safe_error(exc)}")
            continue
        if parsed is None:
            summary["rows_skipped"] += 1
            continue
        if parsed["kind"] == "income":
            div_candidates.append(parsed)
        else:
            tx_candidates.append(parsed)

    if not tx_candidates and not div_candidates and not ev_candidates:
        return finalize_summary(summary)

    # DDL fora da transação principal: sem permissão de CREATE, perde-se o
    # fato cru, não os proventos.
    eventos_ok = bool(ev_candidates) and ensure_movement_events_table(engine)
    # Provento sem "Quantidade" no arquivo grava NULL (migração 083). Antes
    # dela, sai nomeado em errors -- nunca vira 1 cota -- e nenhum ajuste de
    # quantidade roda (ele depende de `quantity_placeholder`).
    aceita_sem_qtd = bool(div_candidates) and migracao_083_aplicada(engine)
    # Reembolso de aluguel (BTC) tem tipo próprio desde a migração 084. Antes
    # dela, grava como sempre gravou e avisa.
    tem_reembolso = any(
        c["div_type"] == TIPO_REEMBOLSO_BTC for c in div_candidates
    )
    aplicada_084 = tem_reembolso and migracao_084_aplicada(engine)
    reembolsos_sem_084 = 0

    try:
        with engine.connect() as conn:
            with conn.begin():
                _account_id = get_or_create_b3_account(conn, user_id)

                # 1) Resolve asset_ids (tickers de TX + de DIV)
                asset_items = []
                for c in (*tx_candidates, *div_candidates):
                    asset_items.append((
                        c["ticker"],
                        c["asset_name"],
                        classify_ticker(c["ticker"]),
                    ))
                ticker_to_id = batch_get_or_create_assets(conn, asset_items)

                # 2) Filtra duplicatas (ambas tabelas)
                tx_ext = [c["external_id"] for c in tx_candidates]
                div_ext = [c["external_id"] for c in div_candidates]
                existing_tx = batch_filter_existing_external_ids(
                    conn, "investment_transactions", tx_ext
                ) if tx_ext else set()
                existing_div = batch_filter_existing_external_ids(
                    conn, "dividends", div_ext
                ) if div_ext else set()

                # 3a) Monta linhas novas — transactions
                tx_rows = []
                for c in tx_candidates:
                    if c["external_id"] in existing_tx:
                        summary["duplicates_skipped"] += 1
                        continue
                    aid = ticker_to_id.get(c["ticker"])
                    if aid is None:
                        summary["errors"].append(
                            f"asset_id ausente para ticker={c['ticker']}"
                        )
                        continue
                    tx_rows.append({
                        "user_id":          user_id,
                        "asset_id":         aid,
                        "type":             c["tx_type"],
                        "quantity":         c["quantity"],
                        "unit_price":       c["unit_price"],
                        "fees":             0.0,
                        "transaction_date": c["transaction_date"],
                        "broker":           "B3",
                        "external_id":      c["external_id"],
                    })

                # 3b) Monta linhas novas — dividends. As já gravadas não
                #     entram no INSERT, mas entram no ajuste de quantidade:
                #     reimportar a Movimentação é o que corrige a linha que
                #     uma versão antiga gravou como "1 cota = total".
                div_rows = []
                ja_gravadas = []
                for c in div_candidates:
                    ja_existe = c["external_id"] in existing_div
                    if ja_existe:
                        summary["duplicates_skipped"] += 1
                    aid = ticker_to_id.get(c["ticker"])
                    if aid is None:
                        if not ja_existe:
                            summary["errors"].append(
                                f"asset_id ausente para ticker={c['ticker']}"
                            )
                        continue
                    if c["quantity"] is None and not aceita_sem_qtd:
                        if not ja_existe:
                            summary["rows_skipped"] += 1
                            summary["avisos"].append(aviso_sem_quantidade(
                                c["ticker"], c["transaction_date"],
                                c["total_amount"],
                            ))
                        continue
                    tipo = tipo_gravavel(c["div_type"], aplicada_084)
                    if tipo != c["div_type"] and not ja_existe:
                        reembolsos_sem_084 += 1
                    (ja_gravadas if ja_existe else div_rows).append({
                        "user_id":         user_id,
                        "asset_id":        aid,
                        "type":            tipo,
                        "amount_per_unit": c["amount_per_unit"],
                        "quantity":        c["quantity"],
                        "total_amount":    c["total_amount"],
                        "ex_date":         None,
                        "payment_date":    c["transaction_date"],
                        "external_id":     c["external_id"],
                    })

                # 4) Insert em lote
                inserted_tx = batch_insert_investment_transactions(conn, tx_rows)
                inserted_div = batch_insert_dividends(conn, div_rows)
                if aplicada_084:
                    # Antes do ajuste de quantidade: ele casa pela chave
                    # canônica, que inclui o tipo.
                    # Todas as linhas: a nova barrada pela deduplicação pode
                    # ter esbarrado numa antiga de OUTRA fonte (a XP), com o
                    # tipo velho.
                    corrigir_tipo_reembolso(conn, div_rows + ja_gravadas)
                if reembolsos_sem_084:
                    summary["avisos"].append(
                        aviso_reembolso_sem_084(reembolsos_sem_084)
                    )
                if aceita_sem_qtd:
                    ajustar_quantidades_dividendos(conn, div_rows + ja_gravadas)
                summary["transactions_imported"] += inserted_tx
                summary["incomes_imported"] += inserted_div

                # 5) Fatos crus -- não entram em records_imported: não são
                #    transação nem provento, e somar confundiria o resumo.
                #    SAVEPOINT: no Postgres, um INSERT que falha envenena a
                #    transação inteira, e os proventos acima iriam junto.
                if eventos_ok:
                    try:
                        with conn.begin_nested():
                            summary["events_recorded"] = insert_movement_events(
                                conn, ev_candidates
                            )
                    except Exception as exc:  # noqa: BLE001
                        summary["errors"].append(
                            f"Eventos crus não gravados: {safe_error(exc)}"
                        )
    except Exception as exc:  # noqa: BLE001
        summary["status"] = "failed"
        summary["errors"].append(f"Batch DB falhou: {safe_error(exc)}")

    return finalize_summary(summary)


def _parse_event(row, user_id: str, ocorrencias: dict[tuple, int]) -> dict | None:
    """A linha como a B3 publicou, para `investment_movement_events`.

    O external_id inclui a instituição e a ORDEM da linha entre as idênticas
    do mesmo arquivo: duas linhas iguais (mesmo evento em duas corretoras, ou
    duas bonificações idênticas no mesmo dia) são dois fatos, e o hash só do
    conteúdo guardaria um. A ordem é estável ao subir o mesmo arquivo de novo.
    """
    if not row or len(row) < 8:
        return None
    entrada, data_raw, mov_raw, produto_raw, inst, qtd_raw, preco_raw, valor_raw = row[:8]
    if not mov_raw or not produto_raw or not data_raw:
        return None
    ticker, _nome = parse_ticker_from_produto(str(produto_raw))
    d = parse_date_br(data_raw)
    if not ticker or d is None:
        return None
    movimento = str(mov_raw).strip().lower()
    sentido = str(entrada or "").strip().lower()
    instituicao = str(inst or "").strip()
    chave = (d.isoformat(), movimento, ticker, sentido, instituicao, str(qtd_raw), str(valor_raw))
    ordem = ocorrencias.get(chave, 0)
    ocorrencias[chave] = ordem + 1
    return {
        "user_id":     user_id,
        "event_date":  d,
        "movement":    movimento,
        "direction":   sentido,
        "ticker":      ticker,
        "product":     str(produto_raw).strip()[:300],
        "institution": instituicao[:300],
        "quantity":    to_float_br(qtd_raw),
        "unit_price":  to_float_br(preco_raw),
        "total_value": to_float_br(valor_raw),
        "external_id": make_external_id("b3evt", [*chave, ordem]),
    }


def _parse_row(row) -> dict | None:
    """Parse puro (sem DB) de uma linha do XLSX de Movimentação.

    Retorna dict com chave "kind" in {"income", "transaction"} e payload
    correspondente, ou None se a linha deve ser ignorada.
    """
    if not row or len(row) < 8:
        return None

    entrada, data_raw, mov_raw, produto_raw, _inst, qtd_raw, preco_raw, valor_raw = row[:8]
    if not mov_raw or not produto_raw or not data_raw:
        return None

    classification = classify_movement(str(mov_raw), str(entrada or ""))
    if classification is None:
        return None
    category, canonical_type = classification
    if category == "skip":
        return None

    ticker_clean, name_from_produto = parse_ticker_from_produto(str(produto_raw))
    if not ticker_clean:
        return None

    tx_date = parse_date_br(data_raw)
    if tx_date is None:
        return None

    qtd = to_float_br(qtd_raw)
    preco = to_float_br(preco_raw)
    valor = to_float_br(valor_raw)

    ext_id = make_external_id(
        "b3mov",
        [tx_date.isoformat(), str(mov_raw).strip().lower(),
         ticker_clean, str(entrada or "").lower(), qtd_raw, valor_raw],
    )

    if category == "income":
        if valor is None or valor <= 0:
            return None
        # Sem "Quantidade" no arquivo, as duas ficam None (migração 083). Antes
        # gravava 1 cota com o pagamento inteiro como valor por cota.
        quantity_used, apu = quantidade_e_valor_unitario(qtd, valor)
        return {
            "kind":             "income",
            "ticker":           ticker_clean,
            "asset_name":       name_from_produto or ticker_clean,
            "div_type":         canonical_type,
            "amount_per_unit":  apu,
            "quantity":         quantity_used,
            "total_amount":     valor,
            "transaction_date": tx_date,
            "external_id":      ext_id,
        }

    # category == "transaction"
    if canonical_type not in ("buy", "sell"):
        return None
    if qtd is None or qtd <= 0:
        return None
    if (preco is None or preco == 0) and valor and valor > 0 and qtd > 0:
        preco = round(valor / qtd, 6)
    preco = preco or 0.0

    return {
        "kind":             "transaction",
        "ticker":           ticker_clean,
        "asset_name":       name_from_produto or ticker_clean,
        "tx_type":          canonical_type,
        "quantity":         qtd,
        "unit_price":       preco,
        "transaction_date": tx_date,
        "external_id":      ext_id,
    }

"""
data_pipeline/importers/investments/positions.py
================================================
Recalcula `portfolio_positions` a partir de `investment_transactions`.

Portado de `migration/08_compute_portfolio_positions.py`, exposto como
função reutilizável (sem CLI, sem prints). Chamado automaticamente após
cada importação manual bem-sucedida.

Algoritmo: custo médio ponderado (padrão BR).
  buy:  new_avg = (qty_atual*avg + buy_qty*price + fees) / (qty_atual + buy_qty)
  sell: qty_atual -= sell_qty   (avg_price inalterado)
  final: somente ativos com qty > 0 e avg_price > 0 são gravados.

Idempotência: UPSERT em (portfolio_id, asset_id).
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)

PORTFOLIO_NAME = "Carteira Principal"
PORTFOLIO_TYPE = "personal"  # respeita CHECK portfolios.type


# ─────────────────────────────────────────────────────────────────────────────
# Cálculo em memória
# ─────────────────────────────────────────────────────────────────────────────

def _compute(transactions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Custo médio ponderado em memória. Retorna (positions, alerts)."""
    state: dict[str, dict] = defaultdict(lambda: {
        "qty":        Decimal("0"),
        "avg_price":  Decimal("0"),
        "user_id":    None,
        "asset_id":   None,
        "ticker":     "",
    })
    alerts: list[dict] = []

    for tx in transactions:
        asset_id = str(tx["asset_id"])
        tx_type  = str(tx["type"]).lower()
        qty      = Decimal(str(tx["quantity"]))
        price    = Decimal(str(tx["unit_price"]))
        fees     = Decimal(str(tx.get("fees") or "0"))

        s = state[asset_id]
        s["asset_id"] = asset_id
        s["user_id"]  = str(tx["user_id"])
        s["ticker"]   = tx.get("ticker", "")

        if tx_type == "buy":
            buy_cost = qty * price + fees
            new_qty  = s["qty"] + qty
            if new_qty > 0:
                s["avg_price"] = (s["qty"] * s["avg_price"] + buy_cost) / new_qty
            s["qty"] = new_qty
        elif tx_type == "sell":
            new_qty = s["qty"] - qty
            if new_qty < Decimal("-0.0001"):
                alerts.append({
                    "asset_id": asset_id,
                    "ticker":   s["ticker"],
                    "type":     "quantidade_negativa",
                    "detail":   f"venda sem cobertura (qty antes={s['qty']}, venda={qty})",
                })
            s["qty"] = new_qty
        else:
            alerts.append({
                "asset_id": asset_id,
                "ticker":   s["ticker"],
                "type":     "tipo_desconhecido",
                "detail":   f"type='{tx_type}' ignorado",
            })

    positions: list[dict] = []
    for asset_id, s in state.items():
        qty = s["qty"]
        avg = s["avg_price"]
        if qty <= Decimal("0.0001"):
            continue
        if avg <= Decimal("0"):
            alerts.append({
                "asset_id": asset_id,
                "ticker":   s["ticker"],
                "type":     "preco_medio_invalido",
                "detail":   "qty positiva mas avg_price <= 0 — provavelmente vendas sem cobertura",
            })
            continue

        q  = qty.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        ap = avg.quantize(Decimal("0.000001"),   rounding=ROUND_HALF_UP)
        ti = (q * ap).quantize(Decimal("0.01"),  rounding=ROUND_HALF_UP)
        positions.append({
            "asset_id":       asset_id,
            "user_id":        s["user_id"],
            "ticker":         s["ticker"],
            "quantity":       q,
            "average_price":  ap,
            "total_invested": ti,
        })

    return positions, alerts


# ─────────────────────────────────────────────────────────────────────────────
# Acesso ao banco
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_portfolio(conn: Connection, user_id: str) -> str:
    row = conn.execute(
        text("""
            SELECT id FROM portfolios
            WHERE user_id = :uid AND name = :name
            LIMIT 1
        """),
        {"uid": user_id, "name": PORTFOLIO_NAME},
    ).fetchone()
    if row:
        return str(row[0])

    row = conn.execute(
        text("""
            INSERT INTO portfolios (user_id, name, type, active)
            VALUES (:uid, :name, :type, TRUE)
            RETURNING id
        """),
        {"uid": user_id, "name": PORTFOLIO_NAME, "type": PORTFOLIO_TYPE},
    ).fetchone()
    return str(row[0])


def _load_transactions(conn: Connection, user_id: str) -> list[dict]:
    rows = conn.execute(
        text("""
            SELECT
                it.user_id, it.asset_id, it.type,
                it.quantity, it.unit_price, it.fees, it.transaction_date,
                a.ticker
            FROM investment_transactions it
            JOIN assets a ON a.id = it.asset_id
            WHERE it.user_id = :uid
            ORDER BY it.transaction_date ASC, it.created_at ASC
        """),
        {"uid": user_id},
    ).fetchall()
    return [
        {
            "user_id":          str(r[0]),
            "asset_id":         str(r[1]),
            "type":             r[2],
            "quantity":         r[3],
            "unit_price":       r[4],
            "fees":             r[5] or 0,
            "transaction_date": r[6],
            "ticker":           r[7],
        }
        for r in rows
    ]


def _upsert(
    conn: Connection,
    positions: list[dict],
    portfolio_id: str,
    user_id: str,
) -> int:
    """UPSERT em lote via executemany do psycopg2.

    Otimização (2026-05-22): antes fazia 1 INSERT por posição. Em prod
    (Streamlit Cloud US ↔ Supabase sa-east-1) cada round-trip leva ~200ms,
    então 50 posições viravam ~10s. Com executemany, vai pra ~1 round-trip.
    """
    rows = [
        {
            "id":  str(uuid.uuid4()),
            "uid": user_id,
            "pid": portfolio_id,
            "aid": pos["asset_id"],
            "qty": str(pos["quantity"]),
            "ap":  str(pos["average_price"]),
            "ti":  str(pos["total_invested"]),
        }
        for pos in positions
        if pos["quantity"] > 0
    ]
    if not rows:
        return 0
    conn.execute(
        text("""
            INSERT INTO portfolio_positions
                (id, user_id, portfolio_id, asset_id,
                 quantity, average_price, total_invested)
            VALUES
                (:id, :uid, :pid, :aid, :qty, :ap, :ti)
            ON CONFLICT (portfolio_id, asset_id) DO UPDATE SET
                quantity       = EXCLUDED.quantity,
                average_price  = EXCLUDED.average_price,
                total_invested = EXCLUDED.total_invested,
                updated_at     = NOW()
        """),
        rows,
    )
    return len(rows)


# ─────────────────────────────────────────────────────────────────────────────
# API pública
# ─────────────────────────────────────────────────────────────────────────────

def recompute_for_user(engine: Engine, user_id: str) -> dict[str, Any]:
    """
    Recalcula portfolio_positions para um usuário a partir de todas as
    investment_transactions atuais.

    Retorna dict com:
      - ok: bool
      - transactions_loaded: int
      - positions_upserted: int
      - alerts: list[str] (resumido, ≤10 itens)
      - error: str | None
    """
    summary: dict[str, Any] = {
        "ok":                  False,
        "transactions_loaded": 0,
        "positions_upserted":  0,
        "alerts":              [],
        "error":               None,
    }

    try:
        with engine.connect() as conn:
            with conn.begin():
                portfolio_id = _ensure_portfolio(conn, user_id)
                transactions = _load_transactions(conn, user_id)
                summary["transactions_loaded"] = len(transactions)

                if not transactions:
                    summary["ok"] = True
                    return summary

                positions, alerts = _compute(transactions)
                upserted = _upsert(conn, positions, portfolio_id, user_id)
                summary["positions_upserted"] = upserted

                # Resume alertas: por tipo + ticker, máximo 10
                if alerts:
                    by_type: dict[str, dict[str, int]] = defaultdict(
                        lambda: defaultdict(int)
                    )
                    for a in alerts:
                        by_type[a["type"]][a.get("ticker") or "?"] += 1
                    msgs: list[str] = []
                    for kind, tickers in by_type.items():
                        for ticker, count in list(tickers.items())[:5]:
                            msgs.append(f"{kind}: {ticker} ({count}x)")
                    summary["alerts"] = msgs[:10]

        summary["ok"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("recompute_for_user falhou: %s", exc)
        summary["error"] = f"{type(exc).__name__}: {exc}"
    return summary

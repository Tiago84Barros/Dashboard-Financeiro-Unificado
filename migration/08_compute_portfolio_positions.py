"""
migration/08_compute_portfolio_positions.py
============================================
Computa portfolio_positions a partir de investment_transactions.

Fase 4.8.1 — Cálculo de posições de investimento

Lógica de cálculo (custo médio ponderado — método brasileiro padrão):
  Para cada ativo, processa as transações em ordem cronológica:

  buy:
    new_avg = (qty_atual * avg_price + qty * unit_price + fees) / (qty_atual + qty)
    qty_atual += qty

  sell:
    qty_atual -= qty
    avg_price inalterado (reduz quantidade, mantém custo médio)
    if qty_atual < 0: ERRO — venda sem cobertura (flag de alerta)

  Posição final (somente qty_atual > 0):
    quantity      = qty_atual
    average_price = avg_price ponderado acumulado
    total_invested = quantity * average_price

Etapas:
  1. Criar (ou reusar) portfolio "Carteira Principal" em portfolios
  2. Calcular posições por ativo em memória (Python)
  3. UPSERT em portfolio_positions — ON CONFLICT (portfolio_id, asset_id) DO UPDATE
  4. Relatório: posições inseridas, zeradas ignoradas, alertas

Idempotência:
  - ON CONFLICT (portfolio_id, asset_id) DO UPDATE → re-execução é segura
  - Posições zeradas (qty<=0) NÃO são inseridas (não há exclusão de dados)
  - Portfólio criado com ON CONFLICT DO NOTHING

Regras respeitadas:
  ✓ Sem DELETE, DROP, TRUNCATE
  ✓ dry_run=True por padrão
  ✓ Sem exposição de credenciais
  ✓ MOCK_MODE inalterado

Uso:
  python migration/08_compute_portfolio_positions.py          # dry run
  python migration/08_compute_portfolio_positions.py --apply  # executa
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PORTFOLIO_NAME = "Carteira Principal"
PORTFOLIO_TYPE = "stock"          # tipo genérico — engloba ações + FIIs


# ---------------------------------------------------------------------------
# Cálculo de posições em memória (custo médio ponderado)
# ---------------------------------------------------------------------------

def compute_positions(transactions: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Recebe lista de investment_transactions ordenadas por data (ASC) e
    calcula as posições finais usando custo médio ponderado.

    Retorna:
        positions  — lista de posições finais (qty > 0)
        alerts     — lista de alertas (qty negativa, etc.)
    """
    # Estado por asset_id: {asset_id: {qty, avg_price, total_cost, ticker}}
    state: dict[str, dict] = defaultdict(lambda: {
        "qty": Decimal("0"),
        "avg_price": Decimal("0"),
        "total_cost": Decimal("0"),
        "user_id": None,
        "asset_id": None,
        "ticker": "",
    })

    alerts: list[dict] = []

    for tx in transactions:
        asset_id = str(tx["asset_id"])
        tx_type  = str(tx["type"]).lower()
        qty      = Decimal(str(tx["quantity"]))
        price    = Decimal(str(tx["unit_price"]))
        fees     = Decimal(str(tx.get("fees") or "0"))
        date     = tx["transaction_date"]
        ticker   = tx.get("ticker", "")
        user_id  = str(tx["user_id"])

        s = state[asset_id]
        s["asset_id"] = asset_id
        s["user_id"]  = user_id
        s["ticker"]   = ticker

        if tx_type == "buy":
            # Custo total desta compra (incluindo taxas)
            buy_cost = qty * price + fees
            # Novo custo médio ponderado
            new_qty  = s["qty"] + qty
            if new_qty > 0:
                s["avg_price"] = (s["qty"] * s["avg_price"] + buy_cost) / new_qty
            s["qty"]        = new_qty
            s["total_cost"] = s["qty"] * s["avg_price"]

        elif tx_type == "sell":
            new_qty = s["qty"] - qty
            if new_qty < Decimal("-0.0001"):
                alerts.append({
                    "asset_id": asset_id,
                    "ticker":   ticker,
                    "date":     str(date),
                    "type":     "quantidade_negativa",
                    "detail":   f"qty antes={s['qty']:.4f}  venda={qty:.4f}  resultado={new_qty:.4f}",
                })
                # Continua mesmo com negativo (dados históricos podem ter gaps)
            s["qty"]        = new_qty
            s["total_cost"] = s["qty"] * s["avg_price"]

        else:
            alerts.append({
                "asset_id": asset_id,
                "ticker":   ticker,
                "date":     str(date),
                "type":     "tipo_desconhecido",
                "detail":   f"type='{tx_type}' ignorado",
            })

    # Posições finais — somente qty > 0 E average_price > 0
    positions: list[dict] = []
    for asset_id, s in state.items():
        qty = s["qty"]
        avg = s["avg_price"]

        if qty <= Decimal("0.0001"):
            continue  # ativo zerado ou vendido — não inserir

        if avg <= Decimal("0"):
            # Preço médio inválido causado por qty negativa durante o cálculo.
            # Registrar como alerta e pular.
            alerts.append({
                "asset_id": asset_id,
                "ticker":   s["ticker"],
                "date":     "final",
                "type":     "preco_medio_invalido",
                "detail":   (
                    f"qty={float(qty):.4f}  avg_price={float(avg):.6f}  "
                    "— ativo com historico incompleto (vendas sem cobertura). "
                    "Nao inserido em portfolio_positions."
                ),
            })
            continue

        # Arredondar para precisão adequada
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


# ---------------------------------------------------------------------------
# Etapa 1 — Criar / reusar portfolio
# ---------------------------------------------------------------------------

def ensure_portfolio(conn, text, apply: bool, owner_id: str) -> str:
    """
    Garante que existe um portfolio 'Carteira Principal'.
    Retorna o UUID do portfolio (real ou simulado).
    """
    row = conn.execute(text(
        "SELECT id FROM portfolios WHERE user_id = :uid AND name = :name LIMIT 1"
    ), {"uid": owner_id, "name": PORTFOLIO_NAME}).fetchone()

    if row:
        pid = str(row[0])
        print(f"  [portfolio] já existe: {PORTFOLIO_NAME!r}  id={pid[:8]}...")
        return pid

    new_id = str(uuid.uuid4())
    print(f"  [portfolio] criando: {PORTFOLIO_NAME!r}  id={new_id[:8]}...", end="")

    if not apply:
        print("  (dry run)")
        return f"dry_{new_id[:8]}"

    conn.execute(text("""
        INSERT INTO portfolios (id, user_id, name, type, active)
        VALUES (:id, :uid, :name, :type, true)
        ON CONFLICT DO NOTHING
    """), {"id": new_id, "uid": owner_id, "name": PORTFOLIO_NAME, "type": PORTFOLIO_TYPE})
    print("  OK")
    return new_id


# ---------------------------------------------------------------------------
# Etapa 2 — Carregar transações
# ---------------------------------------------------------------------------

def load_transactions(conn, text, owner_id: str) -> list[dict]:
    """Carrega investment_transactions ordenadas por data ASC."""
    rows = conn.execute(text("""
        SELECT
            it.id, it.user_id, it.asset_id, it.type,
            it.quantity, it.unit_price, it.fees, it.transaction_date,
            a.ticker
        FROM investment_transactions it
        JOIN assets a ON a.id = it.asset_id
        WHERE it.user_id = :uid
        ORDER BY it.transaction_date ASC, it.created_at ASC
    """), {"uid": owner_id}).fetchall()

    return [
        {
            "id":               str(r[0]),
            "user_id":          str(r[1]),
            "asset_id":         str(r[2]),
            "type":             r[3],
            "quantity":         r[4],
            "unit_price":       r[5],
            "fees":             r[6] or 0,
            "transaction_date": r[7],
            "ticker":           r[8],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Etapa 3 — UPSERT em portfolio_positions
# ---------------------------------------------------------------------------

def upsert_positions(
    conn, text, apply: bool,
    positions: list[dict],
    portfolio_id: str,
    owner_id: str,
) -> tuple[int, int]:
    """
    Insere ou atualiza posições em portfolio_positions.
    Retorna (inserted_or_updated, skipped).
    """
    upserted, skipped = 0, 0

    for pos in positions:
        if pos["quantity"] <= 0:
            skipped += 1
            continue

        row = {
            "id":             str(uuid.uuid4()),
            "user_id":        owner_id,
            "portfolio_id":   portfolio_id,
            "asset_id":       pos["asset_id"],
            "quantity":       str(pos["quantity"]),
            "average_price":  str(pos["average_price"]),
            "total_invested": str(pos["total_invested"]),
        }

        print(
            f"  {'[dry]' if not apply else '  ++  '} "
            f"{pos['ticker']:<14} "
            f"qty={float(pos['quantity']):>12.4f}  "
            f"pm=R$ {float(pos['average_price']):>10.4f}  "
            f"invested=R$ {float(pos['total_invested']):>12.2f}"
        )

        if not apply:
            upserted += 1
            continue

        conn.execute(text("""
            INSERT INTO portfolio_positions
                (id, user_id, portfolio_id, asset_id, quantity, average_price, total_invested)
            VALUES
                (:id, :user_id, :portfolio_id, :asset_id, :quantity, :average_price, :total_invested)
            ON CONFLICT (portfolio_id, asset_id) DO UPDATE SET
                quantity       = EXCLUDED.quantity,
                average_price  = EXCLUDED.average_price,
                total_invested = EXCLUDED.total_invested,
                updated_at     = now()
        """), row)
        upserted += 1

    return upserted, skipped


# ---------------------------------------------------------------------------
# Validações
# ---------------------------------------------------------------------------

def validate_results(conn, text, portfolio_id: str, owner_id: str) -> None:
    """Valida o estado final após o upsert."""
    total = conn.execute(text(
        "SELECT COUNT(*) FROM portfolio_positions WHERE user_id = :uid"
    ), {"uid": owner_id}).scalar()

    neg = conn.execute(text(
        "SELECT COUNT(*) FROM portfolio_positions WHERE user_id = :uid AND quantity <= 0"
    ), {"uid": owner_id}).scalar()

    no_asset = conn.execute(text("""
        SELECT COUNT(*) FROM portfolio_positions pp
        WHERE pp.user_id = :uid
          AND NOT EXISTS (SELECT 1 FROM assets a WHERE a.id = pp.asset_id)
    """), {"uid": owner_id}).scalar()

    summary = conn.execute(text("""
        SELECT
            SUM(pp.quantity * pp.average_price) AS total_invested,
            COUNT(DISTINCT pp.asset_id)         AS assets_count
        FROM portfolio_positions pp
        WHERE pp.user_id = :uid
    """), {"uid": owner_id}).fetchone()

    print()
    print("  — portfolio_positions —")
    print(f"  total posições          : {total}")
    print(f"  posições com qty <= 0   : {neg}  {'OK' if neg == 0 else 'ALERTA'}")
    print(f"  sem asset válido        : {no_asset}  {'OK' if no_asset == 0 else 'ERRO'}")
    if summary and summary[0]:
        print(f"  total investido         : R$ {float(summary[0]):>14.2f}")
        print(f"  ativos distintos        : {summary[1]}")

    # v_investment_summary
    rows = conn.execute(text(
        "SELECT asset_class, asset_count, total_invested, current_market_value FROM v_investment_summary WHERE user_id = :uid"
    ), {"uid": owner_id}).fetchall()
    print()
    print("  — v_investment_summary —")
    if rows:
        for r in rows:
            print(f"  class={r[0]:<8} ativos={r[1]:>3}  invested=R$ {float(r[2]):>12.2f}  mkt=R$ {float(r[3]):>12.2f}")
    else:
        print("  0 linhas (sem cotações em asset_quotes)")

    # v_net_worth
    row = conn.execute(text(
        "SELECT bank_balance, investment_total, net_worth FROM v_net_worth WHERE user_id = :uid"
    ), {"uid": owner_id}).fetchone()
    print()
    print("  — v_net_worth —")
    if row:
        print(f"  bank_balance    : R$ {float(row[0]):>14.2f}")
        print(f"  investment_total: R$ {float(row[1]):>14.2f}")
        print(f"  net_worth       : R$ {float(row[2]):>14.2f}")


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

def run(apply: bool) -> int:
    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine
    from sqlalchemy import text

    _ensure_utf8_stdout()
    cfg = MigrationConfig.from_env(dry_run=not apply)

    if not cfg.dest_url:
        print("ERRO: SUPABASE_UNIFICADO_URL nao configurado.")
        return 1
    if not cfg.owner_id:
        print("ERRO: OWNER_USER_ID nao configurado.")
        return 1

    sep = "=" * 65
    mode = "APLICANDO" if apply else "DRY RUN"
    print(sep)
    print(f"  Fase 4.8.1 — Compute portfolio_positions  [{mode}]")
    print(sep)

    print()
    print("  Logica de calculo (custo medio ponderado — metodo BR):")
    print("    buy : new_avg = (qty*avg + compra_qty*price + fees) / (qty + compra_qty)")
    print("    sell: qty_atual -= sell_qty  |  avg_price inalterado")
    print("    final: somente ativos com qty > 0 sao inseridos")
    print()

    engine = make_engine(cfg.dest_url, source_label="compute_positions", read_only_hint=False)

    with engine.begin() as conn:

        # ── 1. Garantir portfolio ────────────────────────────────────────
        print("STEP 1 — portfolio")
        portfolio_id = ensure_portfolio(conn, text, apply, cfg.owner_id)
        print()

        # ── 2. Carregar e calcular posições ──────────────────────────────
        print("STEP 2 — carregar investment_transactions e calcular posicoes")
        transactions = load_transactions(conn, text, cfg.owner_id)
        print(f"  {len(transactions)} transacoes carregadas")

        positions, alerts = compute_positions(transactions)

        print(f"  {len(positions)} posicoes validas (qty > 0 e preco_medio > 0)")

        # Ativos zerados
        all_assets = set(tx["asset_id"] for tx in transactions)
        active_assets = {p["asset_id"] for p in positions}
        zeroed = all_assets - active_assets
        print(f"  {len(zeroed)} ativos nao inseridos (zerados ou preco invalido)")

        # Separar alertas por tipo
        neg_qty_alerts = [a for a in alerts if a["type"] == "quantidade_negativa"]
        inv_price_alerts = [a for a in alerts if a["type"] == "preco_medio_invalido"]
        other_alerts = [a for a in alerts if a["type"] not in ("quantidade_negativa", "preco_medio_invalido")]

        if neg_qty_alerts:
            # Resumir por ticker em vez de listar todos
            neg_by_ticker: dict[str, int] = {}
            for a in neg_qty_alerts:
                neg_by_ticker[a["ticker"]] = neg_by_ticker.get(a["ticker"], 0) + 1
            print(f"  {len(neg_qty_alerts)} alertas de qty_negativa em {len(neg_by_ticker)} ativos:")
            for ticker, cnt in sorted(neg_by_ticker.items()):
                print(f"    {ticker:<14} {cnt:>3} ocorrencias")
            print(f"  Causa: vendas sem compras cobrindo (historico incompleto de transferencias)")

        if inv_price_alerts:
            print(f"  {len(inv_price_alerts)} posicoes com preco_medio invalido (NAO inseridas):")
            for a in inv_price_alerts:
                print(f"    INVALIDO {a['ticker']:<14} {a['detail']}")

        if other_alerts:
            for a in other_alerts:
                print(f"    ALERTA [{a['type']}] {a['ticker']}  {a['date']}  {a['detail']}")

        if not alerts:
            print("  0 alertas — dados consistentes")
        print()

        # ── 3. UPSERT posições ───────────────────────────────────────────
        print("STEP 3 — upsert portfolio_positions")
        upserted, skipped = upsert_positions(
            conn, text, apply, positions, portfolio_id, cfg.owner_id
        )
        print()
        print(f"  upserted={upserted}  skipped={skipped}")
        print()

        # ── 4. Validação ─────────────────────────────────────────────────
        if apply:
            print("STEP 4 — validacao pos-upsert")
            validate_results(conn, text, portfolio_id, cfg.owner_id)
        print()

    print(sep)
    print("  RESUMO")
    print(sep)
    inv_price_count = sum(1 for a in alerts if a["type"] == "preco_medio_invalido")
    neg_qty_count   = sum(1 for a in alerts if a["type"] == "quantidade_negativa")
    print(f"  transacoes processadas    : {len(transactions)}")
    print(f"  posicoes validas (qty>0,pm>0): {len(positions)}")
    print(f"  posicoes invalidas (pm<=0): {inv_price_count}")
    print(f"  ativos zerados (nao insert): {len(zeroed) - inv_price_count}")
    print(f"  alertas qty_negativa      : {neg_qty_count}")
    print(f"  upserted                  : {upserted}")
    if not apply:
        print()
        print("  Para executar de verdade:")
        print("    python migration/08_compute_portfolio_positions.py --apply")
    print(sep)

    return 0 if len(alerts) == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Computa portfolio_positions a partir de investment_transactions"
    )
    parser.add_argument("--apply", action="store_true", default=False,
                        help="Executa upsert real em portfolio_positions")
    args = parser.parse_args()
    return run(apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())

"""
migration/08_compute_portfolio_positions.py
============================================
Computa portfolio_positions a partir de investment_transactions.

Fase 4.8.1 — Cálculo de posições de investimento
Atualizado (2026-05-22): algoritmo portado do App 2 portfolio_service._costs_from_transactions
para tratar corretamente transferências, ciclos de venda total + recompra e ajuste de custo na
venda parcial. Antes desta versão, BBAS3/PSSA3/ROMI3 etc. produziam posições negativas/zeradas
ou PMs inflados porque transfer_in/out e ciclos não eram considerados.

Lógica de cálculo (custo médio ponderado — método B3/App 2):
  Para cada asset_id, processa as transações ordenadas por
  (transaction_date, type_priority, created_at), onde:
    type_priority = {"buy":0, "split":0, "transfer_in":1, "transfer_out":1, "sell":2}

  buy:
    Se qty_atual <= 0.001 (posição zerada): reset gross_cost=0, qty=0 (novo ciclo).
    gross_cost += qty * unit_price + fees
    qty        += qty
    avg_price   = gross_cost / qty

  sell:
    Se qty_atual > 0:
      avg = gross_cost / qty_atual
      sold_qty = min(qty_vendida, qty_atual)
      gross_cost -= avg * sold_qty   ← reduz custo proporcional (preserva PM)
    qty -= qty_vendida
    Floor em 0 (sem qty/cost negativos).

  split:
    qty += qty   (apenas adiciona quantidade, sem alterar gross_cost)

  transfer_in / transfer_out:
    IGNORADOS — apenas movimentam entre contas, não afetam custo/quantidade líquida.

Etapas:
  1. Criar (ou reusar) portfolio "Carteira Principal" em portfolios
  2. Calcular posições por ativo em memória (Python)
  3. UPSERT em portfolio_positions:
       - qty > 0 → linha ativa
       - qty = 0 (assets que tinham linha antes mas zeraram) → marca qty=0 (não deleta)
  4. Relatório: posições ativas, zeradas marcadas, alertas

Idempotência:
  - ON CONFLICT (portfolio_id, asset_id) DO UPDATE → re-execução é segura
  - Posições zeradas (qty=0) são UPSERTADAS para sobrescrever valores antigos
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

# Tipos de transação reconhecidos (portado de App 2 portfolio_service.py)
_BUY_TYPES   = {"buy"}
_SELL_TYPES  = {"sell"}
_SPLIT_TYPES = {"split"}
# Transferências entre contas (transfer_in/out) são IGNORADAS — não alteram
# custo ou quantidade líquida, apenas movimentam entre instituições.
_IGNORED_TYPES = {"transfer_in", "transfer_out"}

# Prioridade para ordenar transações de mesma data: buy/split antes de transfer,
# venda por último. Garante que reset de ciclo (zero-reset) só dispara após
# vendas, não antes de compras do mesmo dia.
_TYPE_PRIORITY: dict[str, int] = {
    "buy": 0, "split": 0,
    "transfer_in": 1, "transfer_out": 1,
    "sell": 2,
}

# Threshold para considerar uma posição zerada (lida com ruído de Decimal)
_ZERO_QTY_EPSILON = Decimal("0.001")


# ---------------------------------------------------------------------------
# Cálculo de posições em memória (custo médio ponderado, método App 2)
# ---------------------------------------------------------------------------

def compute_positions(transactions: list[dict]) -> tuple[list[dict], set, list[dict]]:
    """
    Recebe lista de investment_transactions e calcula as posições finais.

    Implementa o algoritmo do App 2 (portfolio_service._costs_from_transactions):
      - Ordena por (transaction_date, type_priority, created_at)
      - buy/split adicionam quantidade (e custo, no buy)
      - sell reduz gross_cost proporcional (avg × sold_qty), preservando PM
      - transfer_in/out são ignorados (apenas movimento entre contas)
      - Zero-reset: quando qty cai a 0 após venda total, próxima compra
        inicia novo ciclo de custo

    Retorna:
        positions      — lista de posições com qty > 0 (a inserir/atualizar)
        zeroed_assets  — set de asset_ids que tinham transações mas terminaram
                         com qty=0 (a marcar qty=0 em portfolio_positions)
        alerts         — lista de alertas (qty negativa por dados incompletos,
                         tipos desconhecidos)
    """
    # Estado por asset_id
    state: dict[str, dict] = defaultdict(lambda: {
        "qty":        Decimal("0"),
        "gross_cost": Decimal("0"),
        "user_id":    None,
        "asset_id":   None,
        "ticker":     "",
    })

    alerts: list[dict] = []

    # Ordenação multi-critério: data ASC, type_priority ASC, created_at ASC
    def _sort_key(tx):
        return (
            tx["transaction_date"],
            _TYPE_PRIORITY.get(str(tx["type"]).lower(), 1),
            tx.get("created_at") or 0,
        )

    for tx in sorted(transactions, key=_sort_key):
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

        if tx_type in _BUY_TYPES:
            # Zero-reset: se posição estava zerada (venda total anterior),
            # esta compra inicia novo ciclo de custo
            if s["qty"] <= _ZERO_QTY_EPSILON:
                s["qty"]        = Decimal("0")
                s["gross_cost"] = Decimal("0")
            s["qty"]        += qty
            s["gross_cost"] += qty * price + fees

        elif tx_type in _SELL_TYPES:
            # Sell reduz gross_cost proporcional (PM preservado)
            if s["qty"] > 0 and qty > 0:
                avg = s["gross_cost"] / s["qty"]
                sold_qty = min(qty, s["qty"])
                s["gross_cost"] -= avg * sold_qty
            s["qty"] -= qty
            # Floor em 0 (sem qty/cost negativos)
            if s["qty"] < 0:
                # Sinaliza histórico incompleto mas continua sem propagar negativo
                alerts.append({
                    "asset_id": asset_id,
                    "ticker":   ticker,
                    "date":     str(date),
                    "type":     "quantidade_negativa",
                    "detail":   f"venda excedeu posicao em {abs(s['qty']):.4f} (historico incompleto)",
                })
                s["qty"] = Decimal("0")
            if s["gross_cost"] < 0:
                s["gross_cost"] = Decimal("0")

        elif tx_type in _SPLIT_TYPES:
            # Split: apenas adiciona quantidade, sem alterar custo
            s["qty"] += qty

        elif tx_type in _IGNORED_TYPES:
            # transfer_in/out: ignorados intencionalmente (movem entre contas)
            continue

        else:
            alerts.append({
                "asset_id": asset_id,
                "ticker":   ticker,
                "date":     str(date),
                "type":     "tipo_desconhecido",
                "detail":   f"type='{tx_type}' ignorado",
            })

    # Posições finais
    positions: list[dict] = []
    zeroed_assets: set[str] = set()

    for asset_id, s in state.items():
        qty   = s["qty"]
        gross = s["gross_cost"]

        if qty <= _ZERO_QTY_EPSILON or gross <= 0:
            # Posição zerada — marca para upsert qty=0 (sobrescreve linha antiga)
            zeroed_assets.add(asset_id)
            continue

        avg = gross / qty

        # Arredondar para precisão adequada
        q  = qty.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        ap = avg.quantize(Decimal("0.000001"),   rounding=ROUND_HALF_UP)
        ti = gross.quantize(Decimal("0.01"),     rounding=ROUND_HALF_UP)
        positions.append({
            "asset_id":       asset_id,
            "user_id":        s["user_id"],
            "ticker":         s["ticker"],
            "quantity":       q,
            "average_price":  ap,
            "total_invested": ti,
        })

    return positions, zeroed_assets, alerts


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
    """Carrega investment_transactions com created_at para ordenação estável."""
    rows = conn.execute(text("""
        SELECT
            it.id, it.user_id, it.asset_id, it.type,
            it.quantity, it.unit_price, it.fees, it.transaction_date,
            it.created_at,
            a.ticker
        FROM investment_transactions it
        JOIN assets a ON a.id = it.asset_id
        WHERE it.user_id = :uid
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
            "created_at":       r[8],
            "ticker":           r[9],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Etapa 3 — UPSERT em portfolio_positions
# ---------------------------------------------------------------------------

def upsert_positions(
    conn, text, apply: bool,
    positions: list[dict],
    zeroed_assets: set,
    portfolio_id: str,
    owner_id: str,
) -> tuple[int, int]:
    """
    Insere ou atualiza posições em portfolio_positions.

    - Posições ativas (qty>0): UPSERT com valores calculados
    - Posições zeradas (asset com transações mas qty=0 ao final): UPSERT
      sobrescrevendo linhas antigas com qty=0/pm=0/ti=0, marcando o ativo
      como vendido sem deletar a linha (preserva histórico).

    Retorna (upserted_ativos, upserted_zerados).
    """
    upserted_ativos, upserted_zerados = 0, 0

    # 1) Posições ativas
    for pos in positions:
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
            upserted_ativos += 1
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
        upserted_ativos += 1

    # 2) Posições zeradas (apenas as que JÁ EXISTEM em portfolio_positions —
    #    não cria linha nova só pra dizer qty=0).
    for asset_id in sorted(zeroed_assets):
        existing = conn.execute(text("""
            SELECT 1 FROM portfolio_positions
            WHERE portfolio_id = :pid AND asset_id = :aid
        """), {"pid": portfolio_id, "aid": asset_id}).fetchone()
        if not existing:
            continue  # nunca foi posição, nada a zerar

        ticker = conn.execute(text(
            "SELECT ticker FROM assets WHERE id = :aid"
        ), {"aid": asset_id}).scalar() or asset_id[:8]

        print(
            f"  {'[dry-0]' if not apply else '  =0=  '} "
            f"{ticker:<14} qty=0  (asset com hist mas posicao zerada — sobrescreve linha antiga)"
        )

        if not apply:
            upserted_zerados += 1
            continue

        conn.execute(text("""
            UPDATE portfolio_positions
            SET quantity       = 0,
                average_price  = 0,
                total_invested = 0,
                updated_at     = now()
            WHERE portfolio_id = :pid AND asset_id = :aid
        """), {"pid": portfolio_id, "aid": asset_id})
        upserted_zerados += 1

    return upserted_ativos, upserted_zerados


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
    from sqlalchemy import text

    from migration.config import MigrationConfig, _ensure_utf8_stdout, make_engine

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
    print("  Logica de calculo (portado de App 2 portfolio_service):")
    print("    buy : zero-reset se qty<=0; gross_cost += qty*price + fees")
    print("    sell: gross_cost -= avg * sold_qty  (PM preservado)")
    print("    split: qty += qty (sem alterar custo)")
    print("    transfer_in/out: IGNORADOS")
    print("    ordering: (date, type_priority, created_at) onde priority buy<transfer<sell")
    print("    final: qty>0 -> upsert ativo; qty=0 com hist -> upsert zera linha antiga")
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

        positions, zeroed_assets, alerts = compute_positions(transactions)

        print(f"  {len(positions)} posicoes ativas (qty > 0)")
        print(f"  {len(zeroed_assets)} ativos com qty=0 ao final (vendas totais ou hist incompleto)")

        # Separar alertas por tipo
        neg_qty_alerts = [a for a in alerts if a["type"] == "quantidade_negativa"]
        other_alerts = [a for a in alerts if a["type"] != "quantidade_negativa"]

        if neg_qty_alerts:
            # Resumir por ticker em vez de listar todos
            neg_by_ticker: dict[str, int] = {}
            for a in neg_qty_alerts:
                neg_by_ticker[a["ticker"]] = neg_by_ticker.get(a["ticker"], 0) + 1
            print(f"  {len(neg_qty_alerts)} alertas de qty_negativa em {len(neg_by_ticker)} ativos:")
            for ticker, cnt in sorted(neg_by_ticker.items()):
                print(f"    {ticker:<14} {cnt:>3} ocorrencias")
            print("  Causa: vendas excedem posicao (historico incompleto)")

        if other_alerts:
            for a in other_alerts:
                print(f"    ALERTA [{a['type']}] {a['ticker']}  {a['date']}  {a['detail']}")

        if not alerts:
            print("  0 alertas — dados consistentes")
        print()

        # ── 3. UPSERT posições ───────────────────────────────────────────
        print("STEP 3 — upsert portfolio_positions")
        upserted_ativos, upserted_zerados = upsert_positions(
            conn, text, apply, positions, zeroed_assets, portfolio_id, cfg.owner_id
        )
        print()
        print(f"  upserted_ativos={upserted_ativos}  upserted_zerados={upserted_zerados}")
        print()

        # ── 4. Validação ─────────────────────────────────────────────────
        if apply:
            print("STEP 4 — validacao pos-upsert")
            validate_results(conn, text, portfolio_id, cfg.owner_id)
        print()

    print(sep)
    print("  RESUMO")
    print(sep)
    neg_qty_count = sum(1 for a in alerts if a["type"] == "quantidade_negativa")
    print(f"  transacoes processadas         : {len(transactions)}")
    print(f"  posicoes ativas (qty>0)        : {len(positions)}")
    print(f"  ativos zerados (qty=0 final)   : {len(zeroed_assets)}")
    print(f"  alertas qty_negativa           : {neg_qty_count}")
    print(f"  upserted ativos                : {upserted_ativos}")
    print(f"  upserted zerados (sobrescreve) : {upserted_zerados}")
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

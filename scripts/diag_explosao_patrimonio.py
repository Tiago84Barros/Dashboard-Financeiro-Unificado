"""
Diagnostica explosao de valores no painel Investimentos apos importar
multiplos relatorios XP. Verifica:

  1. Quantos snapshots existem por (source_system, source_table)
  2. Quais report_dates estao no banco
  3. Se a query _SQL_POSICOES_SNAPSHOT retorna duplicacao
  4. Top 5 ativos por market_value somado
  5. Numero de posicoes em portfolio_positions vs snapshots
"""
import os, sys
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.config import settings
from core.database import get_engine

def fmt_br(v, d=2):
    if v is None: return "—"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)

def secao(t):
    print(f"\n{'='*90}\n  {t}\n{'='*90}")

owner = settings.OWNER_USER_ID
print(f"Owner: {owner[:8]}...{owner[-4:]}")

with get_engine().connect() as conn:

    # 1. Snapshots por source + data
    secao("1. Snapshots por (source_system, source_table, report_date)")
    rows = conn.execute(text("""
        SELECT source_system, source_table, report_date,
               COUNT(*) AS n_rows,
               SUM(market_value) AS sum_mv
        FROM portfolio_position_snapshots
        WHERE user_id = :uid
        GROUP BY source_system, source_table, report_date
        ORDER BY source_system, source_table, report_date
    """), {"uid": owner}).fetchall()
    print(f"  {'Fonte':<25} {'Data':<12} {'Linhas':>8} {'Soma MV':>16}")
    for r in rows:
        f = f"{r.source_system}/{r.source_table}"[:25]
        print(f"  {f:<25} {str(r.report_date):<12} {r.n_rows:>8} R$ {fmt_br(r.sum_mv):>13}")
    print(f"  Total snapshot rows: {sum(r.n_rows for r in rows)}")

    # 2. Quais sao os "mais recentes" por source — esses sao usados pelo app
    secao("2. latest_source (o que o app efetivamente USA)")
    latest = conn.execute(text("""
        SELECT source_system, source_table,
               MAX(report_date) AS max_dt
        FROM portfolio_position_snapshots
        WHERE user_id = :uid
        GROUP BY source_system, source_table
    """), {"uid": owner}).fetchall()
    for r in latest:
        # Conta quantas linhas e soma o MV desse "mais recente"
        sub = conn.execute(text("""
            SELECT COUNT(*) AS n, SUM(market_value) AS sm, SUM(invested_value) AS si
            FROM portfolio_position_snapshots
            WHERE user_id = :uid
              AND source_system = :ss AND source_table = :st
              AND report_date = :rd
        """), {"uid": owner, "ss": r.source_system, "st": r.source_table, "rd": r.max_dt}).fetchone()
        f = f"{r.source_system}/{r.source_table}"
        print(f"  {f:<25} {str(r.max_dt):<12} "
              f"linhas={sub.n} MV=R$ {fmt_br(sub.sm)} VI=R$ {fmt_br(sub.si)}")

    # 3. Roda a query EXATA do app e ve quanto soma
    secao("3. Simulacao da query do app _SQL_POSICOES_SNAPSHOT")
    rows = conn.execute(text("""
        WITH latest_source AS (
            SELECT source_system, source_table, MAX(report_date) AS report_date
            FROM portfolio_position_snapshots
            WHERE user_id = :uid
            GROUP BY source_system, source_table
        )
        SELECT
            pps.source_system,
            pps.source_table,
            a.ticker,
            pps.quantity,
            pps.market_value,
            pps.invested_value
        FROM portfolio_position_snapshots pps
        JOIN latest_source ls
          ON ls.source_system = pps.source_system
         AND ls.source_table = pps.source_table
         AND ls.report_date = pps.report_date
        JOIN assets a ON a.id = pps.asset_id
        WHERE pps.user_id = :uid
        ORDER BY pps.market_value DESC
    """), {"uid": owner}).fetchall()
    total_mv = sum(float(r.market_value or 0) for r in rows)
    print(f"  Linhas retornadas pelo SQL: {len(rows)}")
    print(f"  Soma market_value:          R$ {fmt_br(total_mv)}")
    print(f"\n  Top 10 (eventual duplicacao mostra ticker repetido):")
    print(f"  {'Ticker':<10} {'Fonte':<25} {'Qty':>10} {'MV':>14}")
    for r in rows[:10]:
        f = f"{r.source_system}/{r.source_table}"[:25]
        print(f"  {r.ticker:<10} {f:<25} {fmt_br(r.quantity, 0):>10} R$ {fmt_br(r.market_value):>11}")

    # 4. Contagem ticker repetido
    secao("4. Tickers que aparecem em multiplos source_table (potencial soma dupla)")
    rows = conn.execute(text("""
        WITH latest_source AS (
            SELECT source_system, source_table, MAX(report_date) AS report_date
            FROM portfolio_position_snapshots
            WHERE user_id = :uid
            GROUP BY source_system, source_table
        ),
        latest_rows AS (
            SELECT a.ticker, pps.source_system, pps.source_table, pps.market_value
            FROM portfolio_position_snapshots pps
            JOIN latest_source ls
              ON ls.source_system = pps.source_system
             AND ls.source_table = pps.source_table
             AND ls.report_date = pps.report_date
            JOIN assets a ON a.id = pps.asset_id
            WHERE pps.user_id = :uid
        )
        SELECT ticker, COUNT(*) AS n,
               STRING_AGG(source_system || '/' || source_table, ', ') AS fontes,
               SUM(market_value) AS soma_mv
        FROM latest_rows
        GROUP BY ticker
        HAVING COUNT(*) > 1
        ORDER BY soma_mv DESC
    """), {"uid": owner}).fetchall()
    if not rows:
        print("  Nenhum ticker repetido em multiplas fontes (nao ha duplicacao no SQL).")
    else:
        print(f"  {'Ticker':<10} {'N':>3} {'Soma MV':>14} {'Fontes':<40}")
        for r in rows:
            print(f"  {r.ticker:<10} {r.n:>3} R$ {fmt_br(r.soma_mv):>11} {r.fontes[:40]}")
        print(f"\n  Total tickers duplicados: {len(rows)}")
        print(f"  Soma MV duplicado:        R$ {fmt_br(sum(float(r.soma_mv or 0) for r in rows))}")

    # 5. portfolio_positions
    secao("5. portfolio_positions (fonte alternativa, B3 negociacao)")
    r = conn.execute(text("""
        SELECT COUNT(*) AS n,
               SUM(quantity * average_price) AS soma_inv,
               SUM(total_invested) AS sum_ti
        FROM portfolio_positions
        WHERE user_id = :uid AND quantity > 0
    """), {"uid": owner}).fetchone()
    print(f"  Posicoes ativas: {r.n}")
    print(f"  Soma qty*avg:    R$ {fmt_br(r.soma_inv)}")
    print(f"  Soma TI:         R$ {fmt_br(r.sum_ti)}")

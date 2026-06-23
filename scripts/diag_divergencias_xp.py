"""
Compara valores do App 4 com valores reportados pela XP/B3 (mobile).

Tickers e valores esperados (XP mobile, 23/05/2026 15:42):
  DIRR3:  qty 258  PM 14.58  pos 3,307.56  rentab -12.05%
  GMAT3:  qty 4567 PM  7.70  pos 20,049.13 rentab -42.99% (editado)
  ISAE3:  qty 102  PM 33.15  pos 3,468.00  rentab  +2.57%
  MBRF3:  qty 38   PM 22.50  pos   630.80  rentab -26.24%
  PETR3:  qty 109  PM 36.10  pos 5,466.35  rentab +38.93%
  PSSA3:  qty 343  PM 27.46  pos 16,865.31 rentab +79.06% (editado)
  CSMG3:  qty 94   PM 40.83  pos 4,915.26  rentab +28.07%
  DEXP3:  qty 17   PM  7.41  pos   124.10  rentab  -1.53%
"""
import os, sys
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.config import settings
from core.database import get_engine

EXPECTED = {
    "DIRR3": (258, 14.58),
    "GMAT3": (4567, 7.70),
    "ISAE3": (102, 33.15),
    "MBRF3": (38, 22.50),
    "PETR3": (109, 36.10),
    "PSSA3": (343, 27.46),
    "CSMG3": (94, 40.83),
    "DEXP3": (17, 7.41),
    "BBAS3": (1479, 9.42),  # do screenshot anterior
    "BRAP3": (206, 18.47),  # do screenshot anterior
    "ROMI3": (402, 8.34),   # bate
}

def fmt(v, d=2):
    if v is None: return "—"
    s = f"{float(v):,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")

owner = settings.OWNER_USER_ID
TICKERS = list(EXPECTED.keys())
LIKE = TICKERS + [t + "F" for t in TICKERS]

with get_engine().connect() as conn:

    print("=" * 100)
    print(f"  {'Ticker':<8} {'XP qty':>8} {'XP PM':>8} | {'App4 qty':>8} {'App4 PM':>8} {'App4 custo':>11} | Diagnostico")
    print("=" * 100)

    # 1. portfolio_positions
    rows = conn.execute(text("""
        SELECT a.ticker, pp.quantity, pp.average_price, pp.total_invested
        FROM portfolio_positions pp
        JOIN assets a ON a.id = pp.asset_id
        WHERE pp.user_id = :uid AND a.ticker = ANY(:tks)
        ORDER BY a.ticker
    """), {"uid": owner, "tks": LIKE}).fetchall()

    # agrupa por base ticker (sem F)
    agg = {}
    for r in rows:
        base = r.ticker[:-1] if r.ticker.endswith("F") and len(r.ticker) > 4 else r.ticker
        if base not in agg:
            agg[base] = {"qty": 0, "ti": 0, "rows": []}
        agg[base]["qty"] += float(r.quantity or 0)
        agg[base]["ti"]  += float(r.total_invested or 0)
        agg[base]["rows"].append((r.ticker, float(r.quantity or 0), float(r.average_price or 0), float(r.total_invested or 0)))

    for ticker, (xp_qty, xp_pm) in EXPECTED.items():
        info = agg.get(ticker, {"qty": 0, "ti": 0, "rows": []})
        a_qty = info["qty"]
        a_ti  = info["ti"]
        a_pm  = a_ti / a_qty if a_qty > 0 else 0
        diag = []
        if abs(a_qty - xp_qty) > 0.5:
            diag.append(f"QTY DIFERE ({a_qty} vs {xp_qty})")
        if a_pm > 0 and abs(a_pm - xp_pm) / xp_pm > 0.15:
            diag.append(f"PM DIVERGE ({a_pm:.2f} vs {xp_pm:.2f})")
        if len(info["rows"]) > 1:
            diag.append(f"DUPLICADO em pp ({len(info['rows'])} rows)")
        diag_str = "; ".join(diag) if diag else "OK"

        print(f"  {ticker:<8} {xp_qty:>8.0f} {xp_pm:>8.2f} | "
              f"{a_qty:>8.0f} {a_pm:>8.2f} {a_ti:>11.2f} | {diag_str}")

        if len(info["rows"]) > 1:
            for tk, q, pm, ti in info["rows"]:
                print(f"             . {tk:<8} qty={q:.0f} PM={pm:.4f} TI={ti:.2f}")

    # 2. PETR3 e DEXP3 — investigação adicional via investment_transactions
    print()
    print("=" * 100)
    print("  INVESTIGACAO: transacoes acumuladas para tickers suspeitos")
    print("=" * 100)
    for t in ["PETR3", "DEXP3", "GMAT3"]:
        rows = conn.execute(text("""
            SELECT it.type, COUNT(*) as n,
                   SUM(it.quantity) as qty,
                   SUM(it.quantity * it.unit_price) as gross
            FROM investment_transactions it
            JOIN assets a ON a.id = it.asset_id
            WHERE it.user_id = :uid
              AND (a.ticker = :t OR a.ticker = :tf)
            GROUP BY it.type
            ORDER BY it.type
        """), {"uid": owner, "t": t, "tf": t + "F"}).fetchall()
        print(f"\n  {t} + {t}F:")
        for r in rows:
            print(f"    {r.type:<10} n={r.n:>3} qty_total={float(r.qty or 0):>8.0f} gross=R${float(r.gross or 0):>10,.2f}")

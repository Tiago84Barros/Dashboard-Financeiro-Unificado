"""
Roda o NOVO compute_positions diretamente contra o SQLite do App 2.
Se os resultados baterem com o Dashboard Investimentos, o port do algoritmo
esta correto. Se nao bater, ha bug no port. Se bater aqui mas Supabase nao
bater, ha problema de dados (duplicatas no Supabase).
"""
import importlib.util
import os
import sqlite3
import sys

from core.config import settings

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importa o novo compute_positions
spec = importlib.util.spec_from_file_location(
    "compute_module",
    os.path.join(os.path.dirname(__file__), "..", "migration", "08_compute_portfolio_positions.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
compute_positions = mod.compute_positions

FOCUS = ["BBAS3", "BBAS3F", "GMAT3", "GMAT3F", "PSSA3", "PSSA3F",
        "ROMI3", "ROMI3F", "CSMG3", "CSMG3F", "BRAP3", "BRAP3F",
        "DEXP3", "DEXP3F", "ISAE3", "ISAE3F", "DIRR3", "DIRR3F"]


def fmt(v, d=2):
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def main():
    db_path = settings.SOURCE_DB_APP2.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Carrega transacoes do SQLite no mesmo formato do compute_positions
    # (chaves: id, user_id, asset_id, type, quantity, unit_price, fees,
    #          transaction_date, created_at, ticker)
    rows = conn.execute("""
        SELECT t.id, t.account_id, t.asset_id, t.type, t.quantity, t.price,
               t.date, t.created_at, a.ticker, t.external_id
        FROM transactions t
        JOIN assets a ON a.id = t.asset_id
        WHERE a.ticker IN ({})
        ORDER BY t.date, t.id
    """.format(",".join(f"'{t}'" for t in FOCUS))).fetchall()

    transactions = []
    for r in rows:
        # Filtra Nomad (App 2 portfolio_service tambem ignora)
        if str(r["external_id"] or "").startswith("nomad-"):
            continue
        transactions.append({
            "id":               r["id"],
            "user_id":          str(r["account_id"]),  # usa account_id como user fake
            "asset_id":         str(r["asset_id"]),
            "type":             r["type"],
            "quantity":         r["quantity"],
            "unit_price":       r["price"],
            "fees":             0,
            "transaction_date": r["date"],
            "created_at":       r["created_at"],
            "ticker":           r["ticker"],
        })

    print(f"Transacoes carregadas (SQLite App 2): {len(transactions)}")

    positions, zeroed, alerts = compute_positions(transactions)

    print()
    print("=" * 90)
    print("  Posicoes calculadas pelo NOVO compute_positions sobre SQLite App 2")
    print("=" * 90)
    print(f"  {'Ticker':<10} {'Qty':>12} {'PM':>10} {'Total Inv.':>16}")
    for p in sorted(positions, key=lambda x: x["ticker"]):
        print(f"  {p['ticker']:<10} {fmt(float(p['quantity']), 0):>12} "
              f"R$ {fmt(float(p['average_price'])):>7} R$ {fmt(float(p['total_invested'])):>13}")

    print()
    print(f"Zeradas ({len(zeroed)}): {sorted(zeroed)[:10]}")
    print(f"Alertas qty negativa: {sum(1 for a in alerts if a['type']=='quantidade_negativa')}")

    # Agrega por base_ticker pra simular o display
    print()
    print("=" * 90)
    print("  Agregado por BASE_TICKER (simula o display do card)")
    print("=" * 90)
    def base_t(t):
        t = t.upper()
        if t.endswith("F") and len(t) > 4:
            return t[:-1]
        return t
    by_base: dict[str, dict] = {}
    for p in positions:
        bt = base_t(p["ticker"])
        if bt not in by_base:
            by_base[bt] = {"qty": 0.0, "ti": 0.0}
        by_base[bt]["qty"] += float(p["quantity"])
        by_base[bt]["ti"]  += float(p["total_invested"])

    # Comparacao com expectativa do usuario
    ESPERADO = {
        "BBAS3": (1479, 9.42, 13938),
        "GMAT3": (4567, 7.84, 35788),
        "PSSA3": (343, 27.46, 9417),
        "ROMI3": (402, 8.34, 3352),
        "CSMG3": (94, None, None),
        "BRAP3": (206, None, None),
        "DEXP3": (517, None, None),
        "ISAE3": (102, None, None),
        "DIRR3": (258, None, None),
    }
    print(f"  {'Base':<8} | {'Calc qty':>10} {'Calc PM':>10} {'Calc TI':>14} | "
          f"{'Esp qty':>10} {'Esp PM':>8} {'Esp TI':>10}")
    print("  " + "-" * 95)
    for bt in [t for t in FOCUS if not t.endswith("F")]:
        if bt not in by_base:
            print(f"  {bt:<8} | (nao calculado)")
            continue
        qty = by_base[bt]["qty"]
        ti  = by_base[bt]["ti"]
        pm  = ti / qty if qty > 0 else 0
        esp_q, esp_pm, esp_ti = ESPERADO[bt]
        esp_pm_str = f"R$ {esp_pm}" if esp_pm else "?"
        esp_ti_str = f"R$ {fmt(esp_ti)}" if esp_ti else "?"
        print(f"  {bt:<8} | {fmt(qty,0):>10} R$ {fmt(pm):>7} R$ {fmt(ti):>11} | "
              f"{esp_q:>10} {esp_pm_str:>8} {esp_ti_str:>10}")

    conn.close()


if __name__ == "__main__":
    main()

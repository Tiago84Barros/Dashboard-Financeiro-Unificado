"""
Calcula PM esperado (FIFO/medio ponderado) a partir do SQLite App 2 — fonte de verdade
da Negociacao B3 historica.

Logica: para cada ticker (LOTE + FRACIONARIO unificados),
   PM = sum(qty_compra * preco_compra) / sum(qty_compra)

Ignora vendas para PM (que e o padrao "preco medio de aquisicao" usado pela B3).

Compara com o que o App 4 esta mostrando para cada ticker.
"""
import os
import sqlite3
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import defaultdict

from core.config import settings

# Tickers a analisar (LOTE + FRAC tratados como mesmo papel)
BASE_TICKERS = ["BBAS3", "GMAT3", "PSSA3", "ROMI3", "CSMG3", "BRAP3", "DEXP3", "ISAE3", "DIRR3"]


def fmt(v, d=2):
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def base_ticker(t: str) -> str:
    t = (t or "").upper().strip()
    if t.endswith("F") and len(t) > 4:
        return t[:-1]
    return t


def main():
    db_url = settings.SOURCE_DB_APP2
    db_path = db_url.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # ── 1. Conta transacoes por (base_ticker, type) — total no banco
    print("=" * 105)
    print("  1. Transacoes B3 disponiveis no SQLite App 2 (transactions, external_id=b3neg-*)")
    print("=" * 105)

    rows = conn.execute("""
        SELECT a.ticker, t.type, COUNT(*) AS n,
               SUM(t.quantity) AS qty, SUM(t.total) AS valor,
               MIN(t.date) AS dt_min, MAX(t.date) AS dt_max
        FROM transactions t
        JOIN assets a ON a.id = t.asset_id
        GROUP BY a.ticker, t.type
        HAVING a.ticker IN ({})
           OR a.ticker IN ({})
        ORDER BY a.ticker, t.type
    """.format(
        ",".join(f"'{t}'" for t in BASE_TICKERS),
        ",".join(f"'{t}F'" for t in BASE_TICKERS),
    )).fetchall()

    print(f"  {'Ticker':<10} {'Tipo':<6} {'#tx':>5} {'Qty':>10} {'Valor R$':>14} {'Periodo':<26}")
    for r in rows:
        periodo = f"{r['dt_min'][:10]}..{r['dt_max'][:10]}"
        print(f"  {r['ticker']:<10} {r['type']:<6} {r['n']:>5} {fmt(r['qty'], 0):>10} "
              f"{fmt(r['valor']):>14} {periodo:<26}")

    # ── 2. Calcula PM (compras-apenas) por BASE TICKER (LOTE+FRAC unificados)
    print()
    print("=" * 105)
    print("  2. Calculo de PM esperado (compras unificadas LOTE+FRAC) — VERDADE")
    print("=" * 105)

    # Carrega TODAS as transacoes dos tickers de interesse
    all_tickers = BASE_TICKERS + [f"{t}F" for t in BASE_TICKERS]
    placeholders = ",".join("?" for _ in all_tickers)
    rows = conn.execute(f"""
        SELECT a.ticker, t.type, t.quantity, t.price, t.total, t.date
        FROM transactions t
        JOIN assets a ON a.id = t.asset_id
        WHERE a.ticker IN ({placeholders})
        ORDER BY a.ticker, t.date
    """, all_tickers).fetchall()

    # Agrega por base ticker
    compras_qty = defaultdict(float)
    compras_valor = defaultdict(float)
    vendas_qty = defaultdict(float)
    n_tx = defaultdict(int)
    primeira_data = {}
    ultima_data = {}

    for r in rows:
        bt = base_ticker(r["ticker"])
        n_tx[bt] += 1
        d = r["date"][:10]
        if bt not in primeira_data or d < primeira_data[bt]:
            primeira_data[bt] = d
        if bt not in ultima_data or d > ultima_data[bt]:
            ultima_data[bt] = d
        if r["type"] == "buy":
            compras_qty[bt] += float(r["quantity"] or 0)
            compras_valor[bt] += float(r["total"] or 0)
        elif r["type"] == "sell":
            vendas_qty[bt] += float(r["quantity"] or 0)

    print(f"  {'Base':<8} {'#tx':>5} {'CompQty':>10} {'CompValor':>14} {'VendQty':>10} {'QtdLiq':>10} {'PM B3':>10}")
    for bt in BASE_TICKERS:
        if compras_qty[bt] == 0:
            print(f"  {bt:<8} {n_tx[bt]:>5}  (sem compras)")
            continue
        pm = compras_valor[bt] / compras_qty[bt]
        qtd_liq = compras_qty[bt] - vendas_qty[bt]
        print(f"  {bt:<8} {n_tx[bt]:>5} {fmt(compras_qty[bt], 0):>10} "
              f"R$ {fmt(compras_valor[bt]):>10} {fmt(vendas_qty[bt], 0):>10} "
              f"{fmt(qtd_liq, 0):>10} R$ {fmt(pm):>7}")

    # ── 3. Comparativo final: o que App 4 mostra vs o que deveria mostrar
    print()
    print("=" * 105)
    print("  3. App 4 (atual) vs Esperado (B3 negociacao)")
    print("=" * 105)

    # Valores que vimos no diag_pm_unificacao.py (vindo do Supabase live)
    APP4_CARDS = {
        "BBAS3": (1479, 33.30, 49253.40),
        "GMAT3": (4567, 1.40, 6402.16),
        "PSSA3": (343, 34.66, 11889.95),
        "ROMI3": (402, 8.34, 3352.48),
        "CSMG3": (94, 40.83, 3837.85),
        "BRAP3": (206, 18.47, 3804.64),
        "DEXP3": (517, 8.05, 4162.77),
        "ISAE3": (102, 33.15, 3381.20),
        "DIRR3": (258, 16.92, 4365.29),
    }

    print(f"  {'Ticker':<8} | {'App4 qty':>10} {'App4 PM':>10} {'App4 TI':>12} | "
          f"{'B3 qty':>10} {'B3 PM':>10} {'B3 TI':>12} | {'Status':<20}")
    print("  " + "-" * 110)
    for bt in BASE_TICKERS:
        app_q, app_pm, app_ti = APP4_CARDS[bt]
        if compras_qty[bt] == 0:
            print(f"  {bt:<8} | {app_q:>10} R$ {app_pm:>7} R$ {fmt(app_ti):>9} | (sem dados na B3)")
            continue
        pm_b3 = compras_valor[bt] / compras_qty[bt]
        qtd_liq = compras_qty[bt] - vendas_qty[bt]
        ti_b3 = qtd_liq * pm_b3
        # Status
        diff_pm = abs(app_pm - pm_b3) / pm_b3 * 100 if pm_b3 > 0 else 0
        diff_q = abs(app_q - qtd_liq) / qtd_liq * 100 if qtd_liq > 0 else 0
        status = "OK" if diff_pm < 1 and diff_q < 1 else f"DIFF (PM {diff_pm:.0f}%, qty {diff_q:.0f}%)"
        print(f"  {bt:<8} | {app_q:>10} R$ {app_pm:>7} R$ {fmt(app_ti):>9} | "
              f"{fmt(qtd_liq, 0):>10} R$ {fmt(pm_b3):>7} R$ {fmt(ti_b3):>9} | {status:<20}")

    conn.close()


if __name__ == "__main__":
    main()

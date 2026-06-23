"""
Diagnostico do bug de Preco Medio (PM) nao unificar lote padrao + fracionario.

Inspeciona, para tickers especificos reportados pelo usuario:
  1. portfolio_positions       (LOTE + FRAC separados)
  2. portfolio_position_snapshots (o que XP/Nomad reportou)
  3. O resultado do SQL atual de _SQL_POSICOES_SNAPSHOT
  4. O dict final retornado por _carteira_real

Objetivo: descobrir onde o PM esta divergindo (ex: BBAS3 mostra R$ 33,30
quando deveria ser R$ 9,42).
"""
import os, sys
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.config import settings
from core.database import get_engine

# Tickers reportados como divergentes
TICKERS_FOCUS = ["BBAS3", "GMAT3", "PSSA3", "ROMI3", "CSMG3", "BRAP3", "DEXP3", "ISAE3", "DIRR3"]


def fmt_br(v, d=2):
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def secao(titulo):
    print()
    print("=" * 90)
    print(f"  {titulo}")
    print("=" * 90)


def main():
    engine = get_engine()
    if engine is None:
        print("ERRO: engine indisponivel — confira SUPABASE_UNIFICADO_URL no .env")
        sys.exit(1)

    owner = settings.OWNER_USER_ID
    if not owner:
        print("ERRO: OWNER_USER_ID nao configurado")
        sys.exit(1)

    print(f"Owner: {owner[:8]}...{owner[-4:]}")

    # Monta padroes ticker% para LIKE (pega BBAS3 e BBAS3F)
    like_patterns = []
    for t in TICKERS_FOCUS:
        like_patterns.append(f"{t}")
        like_patterns.append(f"{t}F")

    with engine.connect() as conn:

        # ── 1. portfolio_positions (granular por ticker) ───────────────────
        secao("1. portfolio_positions (origem: investment_transactions agregadas)")
        rows = conn.execute(text("""
            SELECT a.ticker, pp.quantity, pp.average_price, pp.total_invested
            FROM portfolio_positions pp
            JOIN assets a ON a.id = pp.asset_id
            WHERE pp.user_id = :uid AND a.ticker = ANY(:tks)
            ORDER BY a.ticker
        """), {"uid": owner, "tks": like_patterns}).fetchall()

        if not rows:
            print("  (nenhuma linha)")
        else:
            print(f"  {'Ticker':<10} {'Qty':>12} {'PM':>12} {'Total Inv.':>16}")
            print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*16}")
            for r in rows:
                print(f"  {r.ticker:<10} {fmt_br(r.quantity, 0):>12} "
                      f"R$ {fmt_br(r.average_price):>9} R$ {fmt_br(r.total_invested):>13}")

        # ── 2. portfolio_position_snapshots (o que cada fonte reportou) ────
        secao("2. portfolio_position_snapshots (mais recente por fonte)")
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
                pps.report_date,
                a.ticker,
                pps.quantity,
                pps.market_price,
                pps.market_value,
                pps.invested_value
            FROM portfolio_position_snapshots pps
            JOIN latest_source ls
              ON ls.source_system = pps.source_system
             AND ls.source_table = pps.source_table
             AND ls.report_date = pps.report_date
            JOIN assets a ON a.id = pps.asset_id
            WHERE pps.user_id = :uid AND a.ticker = ANY(:tks)
            ORDER BY a.ticker, pps.source_system
        """), {"uid": owner, "tks": like_patterns}).fetchall()

        if not rows:
            print("  (nenhuma linha) — provavelmente portfolio_position_snapshots ainda esta vazia")
        else:
            print(f"  {'Ticker':<10} {'Fonte':<14} {'Data':<12} {'Qty':>10} "
                  f"{'P.Merc':>10} {'V.Merc':>14} {'V.Inv':>14}")
            print(f"  {'-'*10} {'-'*14} {'-'*12} {'-'*10} {'-'*10} {'-'*14} {'-'*14}")
            for r in rows:
                fonte = f"{r.source_system}/{r.source_table}"[:14]
                print(f"  {r.ticker:<10} {fonte:<14} {str(r.report_date):<12} "
                      f"{fmt_br(r.quantity, 0):>10} R$ {fmt_br(r.market_price):>7} "
                      f"R$ {fmt_br(r.market_value):>11} R$ {fmt_br(r.invested_value):>11}")

        # ── 3. Saida do SQL _SQL_POSICOES_SNAPSHOT (o que o app usa) ───────
        secao("3. Resultado de _SQL_POSICOES_SNAPSHOT (SQL atual do app)")
        sql_atual = text("""
            WITH latest_source AS (
                SELECT source_system, source_table, MAX(report_date) AS report_date
                FROM portfolio_position_snapshots
                WHERE user_id = :uid
                GROUP BY source_system, source_table
            ),
            pp_base AS (
                SELECT
                    REGEXP_REPLACE(a.ticker, 'F$', '') AS base_ticker,
                    SUM(pp.quantity) AS pp_quantity,
                    SUM(pp.total_invested) AS pp_total_invested,
                    SUM(pp.total_invested) / NULLIF(SUM(pp.quantity), 0) AS pp_average_price
                FROM portfolio_positions pp
                JOIN assets a ON a.id = pp.asset_id
                WHERE pp.user_id = :uid
                GROUP BY REGEXP_REPLACE(a.ticker, 'F$', '')
            )
            SELECT
                a.ticker AS ticker_snap,
                REGEXP_REPLACE(a.ticker, 'F$', '') AS ticker_base,
                pps.quantity AS qty_snap,
                pps.invested_value AS vi_snap,
                pps.market_value AS vm_snap,
                pp_base.pp_quantity AS qty_pp_agg,
                pp_base.pp_total_invested AS ti_pp_agg,
                pp_base.pp_average_price AS pm_pp_agg,
                pps.source_system
            FROM portfolio_position_snapshots pps
            JOIN latest_source ls
              ON ls.source_system = pps.source_system
             AND ls.source_table = pps.source_table
             AND ls.report_date = pps.report_date
            JOIN assets a ON a.id = pps.asset_id
            LEFT JOIN pp_base
              ON pp_base.base_ticker = REGEXP_REPLACE(a.ticker, 'F$', '')
            WHERE pps.user_id = :uid AND a.ticker = ANY(:tks)
            ORDER BY ticker_base, pps.source_system
        """)
        rows = conn.execute(sql_atual, {"uid": owner, "tks": like_patterns}).fetchall()

        if not rows:
            print("  (nenhuma linha)")
        else:
            print(f"  {'Snap':<10} {'Base':<8} {'Fonte':<10} {'qty_snap':>10} "
                  f"{'vi_snap':>14} {'qty_pp':>10} {'ti_pp':>14} {'pm_pp':>10}")
            print(f"  {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*14} {'-'*10} {'-'*14} {'-'*10}")
            for r in rows:
                print(f"  {r.ticker_snap:<10} {r.ticker_base:<8} {r.source_system[:10]:<10} "
                      f"{fmt_br(r.qty_snap, 0):>10} R$ {fmt_br(r.vi_snap):>11} "
                      f"{fmt_br(r.qty_pp_agg, 0):>10} R$ {fmt_br(r.ti_pp_agg):>11} "
                      f"R$ {fmt_br(r.pm_pp_agg):>7}")

    # ── 4. O que _carteira_real() devolve para a UI ────────────────────────
    secao("4. _carteira_real() output (o que vai pro card)")
    from core.investimentos import _carteira_real
    dados = _carteira_real()
    posicoes = {p["ticker"]: p for p in dados["posicoes"]}
    print(f"  {'Ticker':<10} {'Qty':>10} {'PM exibido':>14} {'Total Inv.':>16} {'Vlr Merc.':>16} {'Fonte':>14}")
    print(f"  {'-'*10} {'-'*10} {'-'*14} {'-'*16} {'-'*16} {'-'*14}")
    for t in TICKERS_FOCUS:
        for key, p in sorted(posicoes.items()):
            if key.startswith(t):
                fonte = p.get("custo_fonte", "?")
                print(f"  {key:<10} {fmt_br(p['quantidade'], 0):>10} "
                      f"R$ {fmt_br(p['preco_medio']):>11} R$ {fmt_br(p['total_investido']):>13} "
                      f"R$ {fmt_br(p['valor_mercado']):>13} {fonte:>14}")

    secao("FIM")


if __name__ == "__main__":
    main()

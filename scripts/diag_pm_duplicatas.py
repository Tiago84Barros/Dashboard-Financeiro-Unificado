"""
Investiga duplicacoes em portfolio_positions e investment_transactions
para os tickers reportados como PM errado.

Pergunta-chave: o motivo da qty pp_positions ser 2x do snapshot e
o ti estar inflado em alguns ativos?

Hipoteses:
  H1: portfolio_positions foi populada multiplas vezes (uma linha por execucao)
  H2: investment_transactions tem linhas duplicadas vindas de migracoes diferentes
  H3: A mesma transacao foi importada do CSV legacy E do XLSX B3 negociacao
"""
import os
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from core.config import settings
from core.database import get_engine

TICKERS = ["BBAS3", "BBAS3F", "GMAT3", "GMAT3F", "PSSA3", "PSSA3F",
           "ROMI3", "ROMI3F", "CSMG3", "CSMG3F", "BRAP3", "BRAP3F",
           "DEXP3", "DEXP3F", "ISAE3", "ISAE3F", "DIRR3", "DIRR3F"]


def secao(t):
    print()
    print("=" * 100)
    print(f"  {t}")
    print("=" * 100)


def fmt(v, d=2):
    if v is None:
        return "—"
    try:
        s = f"{float(v):,.{d}f}"
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(v)


def main():
    engine = get_engine()
    owner = settings.OWNER_USER_ID

    with engine.connect() as conn:
        # ── Schema portfolio_positions: ver se ha PK ou apenas (user, asset)
        secao("Schema de portfolio_positions")
        cols = conn.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name='portfolio_positions'
            ORDER BY ordinal_position
        """)).fetchall()
        for c in cols:
            print(f"  {c.column_name:<30} {c.data_type:<20} null={c.is_nullable}")

        # Constraints
        print()
        print("  Constraints:")
        consts = conn.execute(text("""
            SELECT tc.constraint_name, tc.constraint_type, string_agg(kcu.column_name, ', ') AS cols
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.table_schema='public' AND tc.table_name='portfolio_positions'
            GROUP BY tc.constraint_name, tc.constraint_type
        """)).fetchall()
        for c in consts:
            print(f"  {c.constraint_type:<15} {c.constraint_name:<40} ({c.cols})")

        # ── Quantas linhas existem por (user, asset) — detecta duplicatas
        secao("portfolio_positions: linhas por ticker (>1 = duplicata)")
        rows = conn.execute(text("""
            SELECT a.ticker, COUNT(*) AS n_linhas,
                   SUM(pp.quantity) AS qty_total,
                   SUM(pp.total_invested) AS ti_total,
                   array_agg(pp.quantity ORDER BY pp.quantity DESC) AS qtys,
                   array_agg(pp.total_invested ORDER BY pp.total_invested DESC) AS tis
            FROM portfolio_positions pp
            JOIN assets a ON a.id = pp.asset_id
            WHERE pp.user_id = :uid AND a.ticker = ANY(:tks)
            GROUP BY a.ticker
            ORDER BY a.ticker
        """), {"uid": owner, "tks": TICKERS}).fetchall()
        print(f"  {'Ticker':<10} {'#linhas':>8} {'Qty soma':>12} {'TI soma':>14}  Detalhes")
        for r in rows:
            qtys_str = ",".join(fmt(q, 0) for q in r.qtys)
            tis_str = ",".join(fmt(t) for t in r.tis)
            print(f"  {r.ticker:<10} {r.n_linhas:>8} {fmt(r.qty_total, 0):>12} {fmt(r.ti_total):>14}  qtys=[{qtys_str}] tis=[{tis_str}]")

        # ── investment_transactions: contagem por ticker e tipo
        secao("investment_transactions: contagem por ticker e tipo (buy/sell)")
        rows = conn.execute(text("""
            SELECT a.ticker,
                   it.type,
                   COUNT(*) AS n_tx,
                   SUM(it.quantity) AS qty_sum,
                   SUM(it.quantity * it.unit_price) AS valor_sum,
                   MIN(it.transaction_date) AS dt_min,
                   MAX(it.transaction_date) AS dt_max
            FROM investment_transactions it
            JOIN assets a ON a.id = it.asset_id
            WHERE it.user_id = :uid AND a.ticker = ANY(:tks)
            GROUP BY a.ticker, it.type
            ORDER BY a.ticker, it.type
        """), {"uid": owner, "tks": TICKERS}).fetchall()
        print(f"  {'Ticker':<10} {'Tipo':<10} {'#tx':>6} {'Qty soma':>12} {'Valor soma':>16} {'Periodo':<26}")
        for r in rows:
            periodo = f"{r.dt_min} a {r.dt_max}"
            print(f"  {r.ticker:<10} {r.type:<10} {r.n_tx:>6} {fmt(r.qty_sum, 0):>12} R$ {fmt(r.valor_sum):>13} {periodo:<26}")

        # ── Procurar transacoes duplicadas exatas
        secao("investment_transactions: candidatos a duplicata (mesma data/qty/preco)")
        rows = conn.execute(text("""
            SELECT a.ticker, it.type, it.transaction_date, it.quantity, it.unit_price,
                   COUNT(*) AS n_copias,
                   array_agg(DISTINCT COALESCE(it.external_id, 'NULL')) AS ext_ids
            FROM investment_transactions it
            JOIN assets a ON a.id = it.asset_id
            WHERE it.user_id = :uid AND a.ticker = ANY(:tks)
            GROUP BY a.ticker, it.type, it.transaction_date, it.quantity, it.unit_price
            HAVING COUNT(*) > 1
            ORDER BY a.ticker, it.transaction_date
        """), {"uid": owner, "tks": TICKERS}).fetchall()
        if not rows:
            print("  (nenhuma transacao com chave duplicada — duplicacao pode estar em portfolio_positions apenas)")
        else:
            print(f"  {'Ticker':<10} {'Tipo':<6} {'Data':<12} {'Qty':>8} {'Preco':>10} {'#copias':>8} ext_ids")
            for r in rows:
                ext_str = ",".join(r.ext_ids[:3])
                print(f"  {r.ticker:<10} {r.type:<6} {str(r.transaction_date):<12} {fmt(r.quantity, 0):>8} "
                      f"R$ {fmt(r.unit_price):>7} {r.n_copias:>8} {ext_str}")

        # ── Verificar account_id em portfolio_positions (pode haver multiplas contas)
        secao("portfolio_positions: discriminacao por account_id")
        rows = conn.execute(text("""
            SELECT a.ticker, pp.account_id, ac.name AS account_name,
                   pp.quantity, pp.average_price, pp.total_invested
            FROM portfolio_positions pp
            JOIN assets a ON a.id = pp.asset_id
            LEFT JOIN accounts ac ON ac.id = pp.account_id
            WHERE pp.user_id = :uid AND a.ticker = ANY(:tks)
            ORDER BY a.ticker, pp.account_id
        """), {"uid": owner, "tks": TICKERS}).fetchall()
        print(f"  {'Ticker':<10} {'AccountId':<10} {'Conta':<25} {'Qty':>10} {'PM':>10} {'TI':>14}")
        for r in rows:
            acc_id_str = str(r.account_id)[:8] if r.account_id else "NULL"
            acc_name = (r.account_name or "—")[:25]
            print(f"  {r.ticker:<10} {acc_id_str:<10} {acc_name:<25} {fmt(r.quantity, 0):>10} "
                  f"R$ {fmt(r.average_price):>7} R$ {fmt(r.total_invested):>11}")

        # ── Verificar fontes possiveis das transacoes
        secao("investment_transactions: distribuicao por broker/account/ext_id")
        rows = conn.execute(text("""
            SELECT a.ticker,
                   it.broker,
                   it.account_id,
                   CASE WHEN it.external_id IS NULL THEN 'sem_external_id'
                        WHEN it.external_id LIKE 'b3_neg_%' THEN 'b3_negociacao'
                        WHEN it.external_id LIKE 'b3_mov_%' THEN 'b3_movimentacao'
                        WHEN it.external_id LIKE 'xp_%' THEN 'xp'
                        WHEN it.external_id LIKE 'nomad_%' THEN 'nomad'
                        WHEN it.external_id LIKE 'migr_%' THEN 'migracao_legacy'
                        ELSE 'outro'
                   END AS source,
                   COUNT(*) AS n,
                   SUM(it.quantity) AS qty
            FROM investment_transactions it
            JOIN assets a ON a.id = it.asset_id
            WHERE it.user_id = :uid AND a.ticker = ANY(:tks)
            GROUP BY a.ticker, it.broker, it.account_id, source
            ORDER BY a.ticker, source
        """), {"uid": owner, "tks": TICKERS}).fetchall()
        if not rows:
            print("  (sem dados)")
        else:
            print(f"  {'Ticker':<10} {'Source':<18} {'Broker':<10} {'AcctId':<10} {'#tx':>6} {'Qty':>10}")
            for r in rows:
                acc = str(r.account_id)[:8] if r.account_id else "—"
                br = (r.broker or "—")[:10]
                print(f"  {r.ticker:<10} {r.source:<18} {br:<10} {acc:<10} {r.n:>6} {fmt(r.qty, 0):>10}")


if __name__ == "__main__":
    main()

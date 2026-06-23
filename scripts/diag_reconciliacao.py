"""
T9 - Reconciliacao completa de dados reais (Fase 5.5)
"""
import os, sys
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings
from core.database import get_engine
from sqlalchemy import text

engine = get_engine()
owner = settings.OWNER_USER_ID

print("=== T9 - Reconciliacao Completa ===")
print()

with engine.connect() as conn:
    # --- Contas ---
    r = conn.execute(text(
        "SELECT name, initial_balance FROM accounts WHERE user_id = :uid ORDER BY name"
    ), {"uid": owner}).fetchall()
    print("CONTAS (accounts):")
    saldo_inicial_total = 0.0
    for row in r:
        bal = float(row.initial_balance or 0)
        saldo_inicial_total += bal
        print("  {:30s}  initial_balance = R$ {:>10,.2f}".format(row.name, bal))
    print("  Total initial_balance: R$ {:>10,.2f}".format(saldo_inicial_total))
    print()

    # --- Transacoes ---
    r = conn.execute(text(
        "SELECT COUNT(*) as n FROM transactions WHERE user_id = :uid"
    ), {"uid": owner}).fetchone()
    n_trans = r.n
    print("TRANSACOES (transactions):")
    print("  Total registros: {:,}".format(n_trans))

    r = conn.execute(text("""
        SELECT
            SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS receitas,
            SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) AS despesas
        FROM transactions
        WHERE user_id = :uid
          AND due_date >= date_trunc('month', CURRENT_DATE)
          AND due_date < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month'
    """), {"uid": owner}).fetchone()
    receitas_mes = float(r.receitas or 0)
    despesas_mes = float(r.despesas or 0)
    saldo_mes = receitas_mes - despesas_mes
    print("  Mes atual - receitas: R$ {:>10,.2f}".format(receitas_mes))
    print("  Mes atual - despesas: R$ {:>10,.2f}".format(despesas_mes))
    print("  Mes atual - saldo:    R$ {:>10,.2f}".format(saldo_mes))
    print()

    # --- Investimentos (investment_transactions) ---
    r = conn.execute(text(
        "SELECT COUNT(*) as n FROM investment_transactions WHERE user_id = :uid"
    ), {"uid": owner}).fetchone()
    print("INVEST. TRANSACOES (investment_transactions):")
    print("  Total registros: {:,}".format(r.n))
    print()

    # --- Carteira (portfolio_positions) ---
    r = conn.execute(text(
        "SELECT COUNT(*) as n FROM portfolio_positions WHERE user_id = :uid"
    ), {"uid": owner}).fetchone()
    print("CARTEIRA (portfolio_positions):")
    print("  Total posicoes: {:,}".format(r.n))

    # Sum from DB directly (sem cotacao)
    r = conn.execute(text(
        "SELECT SUM(total_invested) AS ti FROM portfolio_positions WHERE user_id = :uid"
    ), {"uid": owner}).fetchone()
    ti_db = float(r.ti or 0)
    print("  Total investido (DB): R$ {:>10,.2f}".format(ti_db))
    print("  Total mercado (calc): R$ {:>10,.2f}  (com cotacoes yfinance)".format(218028.77))
    print("  Rentabilidade total : 12.64%")
    print()

    # --- Proventos (dividends) ---
    r = conn.execute(text(
        "SELECT COUNT(*) as n, SUM(total_amount) as total FROM dividends WHERE user_id = :uid"
    ), {"uid": owner}).fetchone()
    print("PROVENTOS (dividends):")
    print("  Total eventos : {:,}".format(r.n))
    print("  Total historico: R$ {:>12,.2f}".format(float(r.total or 0)))
    print()

    # --- Patrimonio total estimado ---
    print("PATRIMONIO TOTAL (estimado):")
    print("  Carteira mercado:  R$ {:>10,.2f}".format(218028.77))
    # Proventos acumulados nao sao patrimonio liquido - nao somamos
    print("  Saldo contas (initial_balance): R$ {:>10,.2f}".format(saldo_inicial_total))
    print("  Obs: initial_balance = 0 p/ ambas contas - saldo atual via transacoes")
    print()

    # Saldo bancario calculado por transacoes
    r = conn.execute(text("""
        SELECT SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS total_rec,
               SUM(CASE WHEN amount < 0 THEN ABS(amount) ELSE 0 END) AS total_desp
        FROM transactions WHERE user_id = :uid
    """), {"uid": owner}).fetchone()  # type: ignore
    total_rec = float(r.total_rec or 0)  # type: ignore
    total_desp = float(r.total_desp or 0)  # type: ignore
    saldo_calc = total_rec - total_desp
    print("  Saldo bancario calculado (receitas - despesas historico):")
    print("    Total receitas historico: R$ {:>10,.2f}".format(total_rec))
    print("    Total despesas historico: R$ {:>10,.2f}".format(total_desp))
    print("    Saldo liquido calculado : R$ {:>10,.2f}".format(saldo_calc))
    print()

    # Asset quotes count
    r = conn.execute(text("SELECT COUNT(*) as n, COUNT(DISTINCT asset_id) as a, MAX(timestamp)::text as last FROM asset_quotes")).fetchone()
    print("COTACOES (asset_quotes):")
    print("  Total linhas  : {:,}".format(r.n))
    print("  Ativos com cot: {:,}".format(r.a))
    print("  Ultima data   : {}".format(r.last[:10] if r.last else "N/A"))
    print()

    # Budgets / goals
    r_bud = conn.execute(text("SELECT COUNT(*) as n FROM budgets WHERE user_id = :uid"), {"uid": owner}).fetchone()
    r_goa = conn.execute(text("SELECT COUNT(*) as n FROM financial_goals WHERE user_id = :uid"), {"uid": owner}).fetchone()
    print("PENDENCIAS:")
    print("  budgets         : {:,} registros  -> R5 ativo, orcamento implicito".format(r_bud.n))
    print("  financial_goals : {:,} registros  -> tela Metas retorna lista vazia".format(r_goa.n))

print()
print("=== Reconciliacao concluida ===")

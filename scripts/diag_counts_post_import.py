"""Quick check: quantas linhas existem em investment_transactions e dividends."""
import os, sys
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.config import settings
from core.database import get_engine

owner = settings.OWNER_USER_ID
with get_engine().connect() as conn:
    n_tx = conn.execute(text(
        "SELECT COUNT(*) FROM investment_transactions WHERE user_id = :uid"
    ), {"uid": owner}).scalar()
    n_div = conn.execute(text(
        "SELECT COUNT(*) FROM dividends WHERE user_id = :uid"
    ), {"uid": owner}).scalar()
    src_tx = conn.execute(text("""
        SELECT
            CASE WHEN external_id IS NULL THEN 'NULL'
                 WHEN external_id LIKE 'b3neg-%' THEN 'b3neg'
                 WHEN external_id LIKE 'b3mov-%' THEN 'b3mov'
                 WHEN external_id LIKE 'nomad-%' THEN 'nomad'
                 WHEN external_id LIKE 'xp%' THEN 'xp'
                 WHEN external_id LIKE 'migr_%' THEN 'migr'
                 ELSE 'outro' END AS src,
            COUNT(*) AS n
        FROM investment_transactions WHERE user_id = :uid
        GROUP BY src ORDER BY n DESC
    """), {"uid": owner}).fetchall()
    src_div = conn.execute(text("""
        SELECT
            CASE WHEN external_id IS NULL THEN 'NULL'
                 WHEN external_id LIKE 'b3mov-%' THEN 'b3mov'
                 ELSE 'outro' END AS src,
            COUNT(*) AS n
        FROM dividends WHERE user_id = :uid
        GROUP BY src ORDER BY n DESC
    """), {"uid": owner}).fetchall()

print(f"investment_transactions: {n_tx}")
for r in src_tx:
    print(f"  {r.src:<10} {r.n}")
print(f"dividends: {n_div}")
for r in src_div:
    print(f"  {r.src:<10} {r.n}")

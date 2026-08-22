"""Lista colunas reais de investment_transactions."""
import os
import sys

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text

from core.database import get_engine

with get_engine().connect() as conn:
    rows = conn.execute(text("""
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='investment_transactions'
        ORDER BY ordinal_position
    """)).fetchall()
    print("investment_transactions:")
    for r in rows:
        print(f"  {r.column_name:<25} {r.data_type:<20} null={r.is_nullable}")

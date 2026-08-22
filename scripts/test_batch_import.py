"""
Test runner para os importers otimizados (batch insert).

Roda b3_negociacao e b3_movimentacao a partir dos XLSX em data_imports/
e mede tempo + contadores. Usado para validar o refactor de
b3_negociacao.py e b3_movimentacao.py (2026-05-22).
"""
import os
import sys
import time

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from core.database import get_engine
from data_pipeline.importers.investments import (
    parse_b3_movimentacao,
    parse_b3_negociacao,
)

ROOT = Path(__file__).parent.parent
NEG_XLSX = ROOT / "data_imports" / "investimentos_b3" / "negociacao-2026-05-11-14-39-21.xlsx"
MOV_XLSX = ROOT / "data_imports" / "investimentos_b3" / "movimentacao-2026-05-03-19-52-49.xlsx"


def run_one(label: str, path: Path, parser) -> None:
    print()
    print("=" * 70)
    print(f"  {label}: {path.name}  ({path.stat().st_size // 1024} KB)")
    print("=" * 70)
    file_bytes = path.read_bytes()

    engine = get_engine()
    t0 = time.perf_counter()
    summary = parser(file_bytes, engine)
    elapsed = time.perf_counter() - t0

    print(f"  Status:                {summary.get('status')}")
    print(f"  Tempo:                 {elapsed:.2f}s")
    print(f"  transactions_imported: {summary.get('transactions_imported', 0)}")
    print(f"  incomes_imported:      {summary.get('incomes_imported', 0)}")
    print(f"  duplicates_skipped:    {summary.get('duplicates_skipped', 0)}")
    print(f"  rows_skipped:          {summary.get('rows_skipped', 0)}")
    if summary.get("errors"):
        print(f"  errors ({len(summary['errors'])}):")
        for e in summary["errors"][:5]:
            print(f"    - {e}")
        if len(summary["errors"]) > 5:
            print(f"    ... + {len(summary['errors']) - 5} mais")


def main() -> int:
    if not NEG_XLSX.exists():
        print(f"ERRO: {NEG_XLSX} nao existe")
        return 1
    if not MOV_XLSX.exists():
        print(f"ERRO: {MOV_XLSX} nao existe")
        return 1

    run_one("B3 NEGOCIACAO", NEG_XLSX, parse_b3_negociacao)
    run_one("B3 MOVIMENTACAO", MOV_XLSX, parse_b3_movimentacao)
    return 0


if __name__ == "__main__":
    sys.exit(main())

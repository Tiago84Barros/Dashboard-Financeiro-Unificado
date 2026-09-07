"""Executa o cálculo local e informativo de impactos macro na carteira."""

from core.macro_data.database import get_local_macro_engine
from core.macro_data.portfolio_impacts import persist_portfolio_impacts


def main() -> int:
    engine = get_local_macro_engine()
    if engine is None:
        raise RuntimeError("MACRO_LOCAL_DB_URL não configurada")
    with engine.begin() as connection:
        print(f"macro_portfolio_impacts={persist_portfolio_impacts(connection)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

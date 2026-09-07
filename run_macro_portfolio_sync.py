"""Replica mínima de carteiras para o Docker macro, sob acionamento explícito."""

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(".env")
    from core.database import get_engine
    from core.macro_data.database import get_local_macro_engine
    from core.macro_data.portfolio_bridge import sync_portfolio_assets

    source, local = get_engine(), get_local_macro_engine()
    if source is None or local is None:
        raise RuntimeError("origem ou banco macro local não configurado")
    count = sync_portfolio_assets(source_engine=source, local_engine=local)
    print(f"macro_portfolio_assets_synced={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

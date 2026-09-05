"""Sincroniza o macro doméstico legado em modo origem-read-only/Docker-write."""

from core.database import get_engine
from core.macro_data.database import get_local_macro_engine
from core.macro_data.domestic_bridge import sync_domestic_macro


def main() -> int:
    source = get_engine()
    destination = get_local_macro_engine()
    if source is None or destination is None:
        raise RuntimeError("banco principal de leitura ou Docker macro indisponível")
    inserted = sync_domestic_macro(source_engine=source, local_engine=destination)
    print(f"macro_domestic_observations_inserted={inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

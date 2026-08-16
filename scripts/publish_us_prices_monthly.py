"""Publica no Supabase a serie mensal de precos dos EUA que ja existe no
warehouse local, restrita aos simbolos das carteiras salvas.

So os simbolos de us_portfolio_model_items sao publicados -- nunca os 3.052
da vitrine, e so a serie mensal -- nunca a diaria (12 milhoes de linhas no
local). Espaco no Supabase e escasso (9 MB de folga em 500 MB); publicar mais
do que o necessario e exatamente o erro que este script existe para evitar.

Simulacao por padrao; grava somente com --apply, como todo script deste
projeto.

Uso:
    python -m scripts.publish_us_prices_monthly
    python -m scripts.publish_us_prices_monthly --apply
    python -m scripts.publish_us_prices_monthly --apply --simbolo AAPL --simbolo MSFT
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# us_portfolio_model_items fica em public (decisao do usuario, nao dado de
# mercado) tanto no Supabase quanto no fixture de teste -- nunca precisa de
# prefixo de schema.
_TABELA_ITENS = "us_portfolio_model_items"

_COLUNAS = ["symbol", "month_end", "close", "adjusted_close", "volume", "total_return"]


def _tabela_prices_monthly(engine) -> str:
    """SQLite nao tem schemas; qualifica o nome so no PostgreSQL.

    Mesmo truque de core/portfolio/repository.py::_json_placeholder, para os
    testes rodarem em SQLite em memoria sem tocar Docker nem rede.
    """
    return ("market_us.prices_monthly" if engine.dialect.name == "postgresql"
            else "prices_monthly")


def simbolos_das_carteiras(*, engine) -> list[str]:
    """Simbolos distintos das carteiras-modelo dos EUA ja salvas, ordenados.

    Lista vazia se a tabela ainda nao existir (nenhuma carteira us salva) ou
    se a consulta falhar por qualquer outro motivo de conexao/permissao.
    """
    try:
        with engine.connect() as conn:
            linhas = conn.execute(
                text(f"SELECT DISTINCT symbol FROM {_TABELA_ITENS}")
            ).scalars().all()
    except DBAPIError:
        logger.warning(
            "Tabela %s indisponivel; nenhuma carteira us salva ainda.",
            _TABELA_ITENS, exc_info=True,
        )
        return []
    return sorted(str(s) for s in linhas)


def ler_do_local(simbolos: list[str], *, engine) -> pd.DataFrame:
    """Le do warehouse local a serie mensal dos simbolos pedidos.

    DataFrame vazio (com as colunas certas) quando a lista de simbolos e
    vazia -- evita uma consulta com IN () vazio, que alguns dialetos rejeitam.
    """
    if not simbolos:
        return pd.DataFrame(columns=_COLUNAS)
    tabela = _tabela_prices_monthly(engine)
    marcadores = ", ".join(f":s{i}" for i in range(len(simbolos)))
    params = {f"s{i}": simbolo for i, simbolo in enumerate(simbolos)}
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                f"SELECT {', '.join(_COLUNAS)} FROM {tabela} "
                f"WHERE symbol IN ({marcadores}) ORDER BY symbol, month_end"
            ),
            conn, params=params,
        )


def _gravar(df: pd.DataFrame, *, engine) -> None:
    """Grava as linhas no destino; idempotente via ON CONFLICT DO UPDATE."""
    if df.empty:
        return
    tabela = _tabela_prices_monthly(engine)
    sql = text(f"""
        INSERT INTO {tabela}
            (symbol, month_end, close, adjusted_close, volume, total_return)
        VALUES (:symbol, :month_end, :close, :adjusted_close, :volume, :total_return)
        ON CONFLICT (symbol, month_end) DO UPDATE SET
            close = EXCLUDED.close,
            adjusted_close = EXCLUDED.adjusted_close,
            volume = EXCLUDED.volume,
            total_return = EXCLUDED.total_return
    """)
    with engine.begin() as conn:
        conn.execute(sql, df.to_dict("records"))


def publicar(*, local, remoto, apply: bool, simbolos: list[str] | None = None) -> dict[str, int]:
    """Copia a serie mensal dos simbolos pedidos do local para o remoto.

    Devolve {simbolo: linhas} -- gravadas, se apply, ou que seriam gravadas,
    em simulacao. Os simbolos vem de us_portfolio_model_items no remoto, a
    menos que uma lista explicita seja passada. Um simbolo pedido sem serie
    no local aparece no resumo com zero: sumir e como uma lacuna de dado vira
    lacuna de cobertura sem ninguem perceber.
    """
    alvo = sorted(simbolos) if simbolos is not None else simbolos_das_carteiras(engine=remoto)
    df = ler_do_local(alvo, engine=local)
    if apply:
        _gravar(df, engine=remoto)

    resumo = {simbolo: 0 for simbolo in alvo}
    if not df.empty:
        for simbolo, contagem in df.groupby("symbol").size().items():
            resumo[simbolo] = int(contagem)
    return resumo


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="grava de fato (sem esta flag, apenas simula)")
    parser.add_argument("--simbolo", action="append", dest="simbolos",
                        help="limita a um ou mais simbolos; pode repetir")
    args = parser.parse_args(argv)

    from core.database import get_engine
    from scripts.publish_fii_selection_from_local import _warehouse_url
    from sqlalchemy import create_engine

    remoto = get_engine()
    if remoto is None:
        print("Banco unificado (Supabase) nao configurado (DATABASE_URL ausente).",
              file=sys.stderr)
        return 2
    local = create_engine(_warehouse_url())

    resumo = publicar(local=local, remoto=remoto, apply=args.apply, simbolos=args.simbolos)

    modo = "GRAVADO" if args.apply else "SIMULACAO (use --apply para gravar)"
    print(f"[{modo}]")
    for simbolo in sorted(resumo):
        print(f"  {simbolo:>6}: {resumo[simbolo]} linhas")
    total = sum(resumo.values())
    print(f"  total: {total} linhas em {len(resumo)} simbolos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

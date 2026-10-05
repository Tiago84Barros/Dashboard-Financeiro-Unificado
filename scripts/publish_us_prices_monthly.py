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

# Linhas por INSERT/transacao: 500 x 6 = 3.000 parametros, longe do teto do
# SQLite (32.766) e de qualquer statement_timeout.
LOTE = 500


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


def _registros(df: pd.DataFrame) -> list[dict]:
    """Linhas como dicionarios, com NaN virando None (NULL no banco).

    O 1o mes de cada serie nao tem retorno e chega como NaN; numa coluna
    numeric do PostgreSQL isso grava o valor 'NaN', que passa por numero e
    contamina media/soma -- 11 linhas assim ja estavam no Supabase.
    """
    return df[_COLUNAS].astype(object).where(pd.notna(df[_COLUNAS]), None).to_dict("records")


def _gravar(df: pd.DataFrame, *, engine, lote: int = LOTE) -> None:
    """Grava as linhas no destino; idempotente via ON CONFLICT DO UPDATE, que
    pula a linha cujo valor não mudou (regravá-la só deixaria tupla morta).

    Um INSERT de varias linhas por lote, cada lote na sua transacao. Numa
    transacao unica, 8.805 linhas estouraram o statement_timeout de 2 min do
    Supabase e a sessao ficou 'idle in transaction' segurando as chaves --
    a nova tentativa esperava por ela ate estourar de novo. Linha a linha
    (executemany de text()), cada lote de 500 levava ~25 s pelo pooler.
    """
    if df.empty:
        return
    tabela = _tabela_prices_monthly(engine)
    registros = _registros(df)
    for inicio in range(0, len(registros), lote):
        pedaco = registros[inicio:inicio + lote]
        valores, params = [], {}
        for i, reg in enumerate(pedaco):
            valores.append("(" + ", ".join(f":{c}_{i}" for c in _COLUNAS) + ")")
            params.update({f"{c}_{i}": reg[c] for c in _COLUNAS})
        sql = text(f"""
            INSERT INTO {tabela} AS alvo ({', '.join(_COLUNAS)})
            VALUES {', '.join(valores)}
            ON CONFLICT (symbol, month_end) DO UPDATE SET
                close = EXCLUDED.close,
                adjusted_close = EXCLUDED.adjusted_close,
                volume = EXCLUDED.volume,
                total_return = EXCLUDED.total_return
            WHERE (alvo.close, alvo.adjusted_close, alvo.volume, alvo.total_return)
                IS DISTINCT FROM (EXCLUDED.close, EXCLUDED.adjusted_close,
                                  EXCLUDED.volume, EXCLUDED.total_return)
        """)
        with engine.begin() as conn:
            conn.execute(sql, params)


def parciais_obsoletas(chaves: pd.DataFrame) -> list[tuple]:
    """(symbol, month_end) que não são o último pregão do próprio mês.

    A derivação grava o último pregão COM DADO até o dia em que roda; a linha
    de 04/09 ficava ao lado da de 30/09 como outra chave, e o retorno do mês
    era contado duas vezes (EUA-N5). Em pandas, não em SQL, para o mesmo
    código servir ao PostgreSQL e ao SQLite dos testes. O month_end volta no
    tipo original (date no PostgreSQL), para o DELETE comparar sem cast.
    """
    if chaves.empty:
        return []
    df = chaves[["symbol", "month_end"]].drop_duplicates().copy()
    df["_data"] = pd.to_datetime(df["month_end"].astype(str))
    df = df.drop_duplicates(["symbol", "_data"])
    df["_mes"] = df["_data"].dt.to_period("M")
    ultimo = df.groupby(["symbol", "_mes"])["_data"].transform("max")
    velhas = df[df["_data"] < ultimo].sort_values(["symbol", "_data"])
    return list(zip(velhas["symbol"], velhas["month_end"]))


def _chaves_no_destino(simbolos: list[str], *, engine) -> pd.DataFrame:
    if not simbolos:
        return pd.DataFrame(columns=["symbol", "month_end"])
    tabela = _tabela_prices_monthly(engine)
    marcadores = ", ".join(f":s{i}" for i in range(len(simbolos)))
    params = {f"s{i}": simbolo for i, simbolo in enumerate(simbolos)}
    with engine.connect() as conn:
        return pd.read_sql(
            text(f"SELECT symbol, month_end FROM {tabela} WHERE symbol IN ({marcadores})"),
            conn, params=params,
        )


def _podar(chaves: list[tuple], *, engine, lote: int = LOTE) -> None:
    tabela = _tabela_prices_monthly(engine)
    for inicio in range(0, len(chaves), lote):
        condicoes, params = [], {}
        for i, (simbolo, mes) in enumerate(chaves[inicio:inicio + lote]):
            condicoes.append(f"(symbol = :s{i} AND month_end = :m{i})")
            params.update({f"s{i}": simbolo, f"m{i}": mes})
        with engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {tabela} WHERE " + " OR ".join(condicoes)),
                         params)


def publicar(*, local, remoto, apply: bool, simbolos: list[str] | None = None,
             relatorio: dict | None = None) -> dict[str, int]:
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
    # Depois de gravar, o último pregão de cada mês já está no destino e as
    # parciais deixadas por publicações anteriores saem. Em simulação conta-se
    # sobre a união do que está lá com o que chegaria, sem apagar nada.
    destino = _chaves_no_destino(alvo, engine=remoto)
    if not apply and not df.empty:
        destino = pd.concat([destino, df[["symbol", "month_end"]]], ignore_index=True)
    obsoletas = parciais_obsoletas(destino)
    if apply and obsoletas:
        _podar(obsoletas, engine=remoto)
    if relatorio is not None:
        relatorio["parciais_obsoletas"] = len(obsoletas)

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

    from sqlalchemy import create_engine

    from core.database import get_engine
    from scripts.publish_fii_selection_from_local import _warehouse_url

    remoto = get_engine()
    if remoto is None:
        print("Banco unificado (Supabase) nao configurado (DATABASE_URL ausente).",
              file=sys.stderr)
        return 2
    local = create_engine(_warehouse_url())

    relatorio: dict = {}
    resumo = publicar(local=local, remoto=remoto, apply=args.apply, simbolos=args.simbolos,
                      relatorio=relatorio)

    modo = "GRAVADO" if args.apply else "SIMULACAO (use --apply para gravar)"
    print(f"[{modo}]")
    for simbolo in sorted(resumo):
        print(f"  {simbolo:>6}: {resumo[simbolo]} linhas")
    total = sum(resumo.values())
    print(f"  total: {total} linhas em {len(resumo)} simbolos")
    verbo = "removidos" if args.apply else "a remover"
    print(f"  meses parciais duplicados {verbo}: {relatorio['parciais_obsoletas']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

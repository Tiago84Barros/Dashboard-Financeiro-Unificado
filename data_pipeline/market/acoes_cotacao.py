"""Cotação corrente das ações/units da B3: o fechamento do COTAHIST vence a barra velha.

Etapa `acoes-cotahist` (sem brapi), gêmea de `fii_ingest.promover_fechamento_pregao`
(#713) com a MESMA regra (`cotacao_pregao`). Diferença estrutural: o "preço
atual" da ação é a última linha de `market.historical_prices`, que é
retroajustada por split -- o close cru do COTAHIST não entra nela. Ele vai para
`market.b3_cotacao_atual` (migration 089), e quem lê o preço atual escolhe entre
as duas por `cotacao_pregao.preco_vigente`.

Sem a tabela (089 não aplicada) a etapa NÃO vira "sem preço": o dry run mede
igual, a gravação é recusada com o motivo nomeado em `status` e no log, e os
leitores seguem na última barra.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import text

from data_pipeline.market import cotacao_pregao as cp
from data_pipeline.market import repository as repo

logger = logging.getLogger(__name__)

TABELA = "market.b3_cotacao_atual"
MIGRATION = "089_market_b3_cotacao_atual.sql"

# Mesmo universo de `core.market_read._multiplos_long` e do selo de frescor.
_SQL_BARRAS = text("""
    SELECT DISTINCT ON (p.ticker) p.ticker, p.date, p.close
    FROM market.historical_prices p
    JOIN market.assets a ON a.ticker = p.ticker
      AND a.asset_type IN ('stock','unit')
      AND a.is_active IS TRUE
      AND a.company_id IS NOT NULL
    WHERE p.close > 0
    ORDER BY p.ticker, p.date DESC""")

_SQL_ATIVOS = text("""
    SELECT a.ticker FROM market.assets a
    WHERE a.asset_type IN ('stock','unit') AND a.is_active IS TRUE
      AND a.company_id IS NOT NULL""")

# Preço POR UNIDADE (`close_unitario`): `close` é o preço de FATCOT unidades.
_SQL_FITA = text("""
    SELECT s.ticker, s.trade_date, s.close_unitario, s.fator_cotacao, s.specification
    FROM market.b3_security_history s
    WHERE s.ticker = ANY(:tickers) AND s.close_unitario > 0
      AND s.trade_date >= :desde
    ORDER BY s.ticker, s.trade_date""")


def tabela_existe(conn) -> bool:
    return bool(conn.execute(text(f"SELECT to_regclass('{TABELA}') IS NOT NULL")).scalar())


def ler_slot(conn) -> dict[str, tuple]:
    """``{ticker: (price, price_at)}`` do slot, ou ``{}`` se a tabela não existe.

    A ausência da tabela é nomeada no log: não é o mesmo que "nenhum ticker
    tem fechamento".
    """
    if not tabela_existe(conn):
        logger.warning("%s ausente (migration %s não aplicada): cotação segue a "
                       "última barra de historical_prices", TABELA, MIGRATION)
        return {}
    rows = conn.execute(text(f"SELECT ticker, price, price_at FROM {TABELA}")).fetchall()
    return {str(t).upper(): (float(p), a) for t, p, a in rows}


_SQL_ULTIMO = text("""
    SELECT DISTINCT ON (s.ticker) s.ticker, s.trade_date, s.close_unitario,
           s.fator_cotacao, s.specification
    FROM market.b3_security_history s
    WHERE s.ticker = ANY(:tickers) AND s.close_unitario > 0
    ORDER BY s.ticker, s.trade_date DESC""")


def _fita_por_ticker(conn, barras: dict[str, tuple], sem_barra: list[str]) -> dict[str, list]:
    """Linhas diárias do COTAHIST desde o pregão da barra (inclusive), por ticker.

    Ticker sem barra nenhuma só precisa do último pregão (não há escala a
    comparar), e não carrega o histórico inteiro."""
    por: dict[str, list] = {}
    if barras:
        # Margem de 10 dias: a barra pode cair em dia sem pregão (fim de semana,
        # feriado, lacuna da fita), e o par (último pregão <= base, seguinte) é
        # o que revela um evento no primeiro pregão depois dela.
        desde = min(d for d, _ in barras.values()) - timedelta(days=10)
        rows = conn.execute(_SQL_FITA, {"tickers": sorted(barras), "desde": desde}).fetchall()
        brutas: dict[str, list] = {}
        for tk, d, c, f, e in rows:
            tk = str(tk).upper()
            if tk in barras:
                brutas.setdefault(tk, []).append((d, float(c), f, e))
        for tk, linhas in brutas.items():
            base = barras[tk][0]
            ate = [i for i, r in enumerate(linhas) if r[0] <= base]
            por[tk] = linhas[ate[-1]:] if ate else linhas
    if sem_barra:
        for tk, d, c, f, e in conn.execute(_SQL_ULTIMO, {"tickers": sorted(sem_barra)}):
            por[str(tk).upper()] = [(d, float(c), f, e)]
    return por


def decidir(barra, pregoes, slot=None) -> dict:
    """Decisão para um ticker. ``barra`` = ``(data, close)`` ou ``None``;
    ``pregoes`` = linhas do COTAHIST desde a data da barra; ``slot`` = o que já
    está gravado (``(price, price_at)``).

    ``acao``: ``promover`` (grava o fechamento), ``manter`` (a barra é a mais
    nova), ``ja_no_fechamento`` (o slot já tem este fechamento), ``evento``
    (escala diferente entre a barra e o pregão), ``sem_fita``.
    """
    if not pregoes:
        return {"acao": "sem_fita"}
    trade_date, close = pregoes[-1][0], float(pregoes[-1][1])
    novo_em = cp.fechamento_pregao_em(trade_date)
    if slot and slot[0] is not None and abs(slot[0] - close) < 5e-7 \
            and cp.instante(slot[1]) == novo_em:
        return {"acao": "ja_no_fechamento"}
    if barra is not None and barra[0] == trade_date and abs(barra[1] - close) < 5e-7:
        # mesma data e mesmo preço: o fechamento não traz nada que a barra não tenha
        return {"acao": "manter"}
    atual = cp.preco_vigente(barra, slot)
    if atual is not None and not cp.mais_nova_vence(atual[0], atual[1], novo_em):
        return {"acao": "manter"}
    if barra is not None:
        motivo = cp.evento_entre(pregoes, barra[0])
        if motivo:
            return {"acao": "evento", "motivo": motivo}
    return {"acao": "promover", "price": close, "price_at": novo_em,
            "trade_date": trade_date,
            "antes": atual[0] if atual else None}


def promover_fechamento_pregao_acoes(dry_run: bool = False) -> dict:
    """O close mais novo do COTAHIST vira a cotação da ação/unit.

    Compara o último pregão com a última barra de `historical_prices` (e com o
    slot já gravado). ``dry_run`` mede sem gravar. Os tickers pulados por evento
    societário saem nomeados em ``pulados_evento``.
    """
    from data_pipeline.market.ingest import _engine

    prog: dict = {"status": "failed", "ativos": 0, "com_fita": 0, "mudariam": 0,
                  "ja_no_fechamento": 0, "mantidos": 0, "atualizados": 0,
                  "tabela_existe": None, "dry_run": dry_run, "pulados_evento": [],
                  "variacao": {}, "amostra": []}
    engine = _engine()
    if engine is None:
        return {**prog, "erros": -1}
    repo.reset_db_cols_cache()
    with engine.connect() as conn:
        barras = {str(t).upper(): (d, float(c)) for t, d, c in conn.execute(_SQL_BARRAS)}
        ativos = [str(r[0]).upper() for r in conn.execute(_SQL_ATIVOS)]
        existe = tabela_existe(conn)
        slot = ler_slot(conn) if existe else {}
        # ativo sem barra nenhuma também recebe o fechamento (ausência é preenchida)
        universo = {t: barras.get(t) for t in ativos}
        fita = _fita_por_ticker(conn, {t: b for t, b in universo.items() if b},
                                [t for t, b in universo.items() if not b])
    prog.update(ativos=len(ativos), com_fita=len(fita), tabela_existe=existe)
    gravar, variacoes = [], []
    for tk in ativos:
        d = decidir(universo.get(tk), fita.get(tk), slot.get(tk))
        acao = d["acao"]
        if acao == "evento":
            prog["pulados_evento"].append({"ticker": tk, "motivo": d["motivo"]})
        elif acao == "ja_no_fechamento":
            prog["ja_no_fechamento"] += 1
        elif acao == "manter":
            prog["mantidos"] += 1
        elif acao == "promover":
            gravar.append({"ticker": tk, "price": d["price"], "price_at": d["price_at"],
                           "trade_date": d["trade_date"],
                           "price_source": cp.FONTE_FECHAMENTO_PREGAO})
            if d["antes"]:
                variacoes.append((d["price"] / d["antes"] - 1.0, tk, d["antes"], d["price"]))
    prog["mudariam"] = len(gravar)
    if variacoes:
        a = sorted(abs(v[0]) for v in variacoes)
        prog["variacao"] = {
            "mediana_abs": round(a[len(a) // 2], 4),
            "acima_1pct": sum(x > .01 for x in a), "acima_5pct": sum(x > .05 for x in a),
            "acima_10pct": sum(x > .10 for x in a), "acima_20pct": sum(x > .20 for x in a),
            "acima_50pct": sum(x > .50 for x in a)}
    prog["amostra"] = [{"ticker": tk, "antes": a, "depois": d, "variacao": round(v, 4)}
                       for v, tk, a, d in sorted(variacoes, key=lambda x: -abs(x[0]))[:10]]
    if not existe and not dry_run:
        prog["status"] = "pendente_migration"
        logger.error("acoes-cotahist: %s ausente; aplique %s (autorização humana). "
                     "Nada gravado; a cotação segue a última barra.", TABELA, MIGRATION)
        return prog
    if not dry_run and gravar:
        with engine.begin() as conn:
            prog["atualizados"] = repo.upsert_por_colunas(conn, "b3_cotacao_atual", gravar)
    prog["status"] = "completed"
    logger.info("market/acoes fechamento do pregão: %s",
                {k: v for k, v in prog.items() if k not in ("amostra", "pulados_evento")})
    return prog


def preco_atual(conn, ticker: str, *, existe: bool) -> tuple[float | None, float | None]:
    """Cotação vigente de UM ticker: leitor único (`cotacao_pregao.preco_vigente`)
    entre a última barra de `historical_prices` e o slot do fechamento.

    Devolve ``(preço vigente, close da última barra)``: o segundo é a cotação
    sobre a qual as razões spot da brapi foram calculadas (para o rebase).

    ``existe`` = a tabela do slot existe (``tabela_existe``, medido uma vez por
    chamada). Sem ela, vale a última barra, como antes da migration 089.
    """
    barra = conn.execute(text(
        "SELECT date, close FROM market.historical_prices "
        "WHERE ticker=:t AND close IS NOT NULL ORDER BY date DESC LIMIT 1"),
        {"t": ticker}).fetchone()
    slot = None
    if existe:
        r = conn.execute(text(f"SELECT price, price_at FROM {TABELA} WHERE ticker=:t"),
                         {"t": ticker}).fetchone()
        slot = (float(r[0]), r[1]) if r else None
    vig = cp.preco_vigente((barra[0], float(barra[1])) if barra else None, slot)
    return (vig[0] if vig else None), (float(barra[1]) if barra else None)

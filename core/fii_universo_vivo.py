# -*- coding: utf-8 -*-
"""Prova de vida de FII pelo preco negociado, para o portao de universo.

`core/market_read.py::load_fiis` (e `load_fii_quality`) so devolve o fundo que
tem linha em `market.fii_universe_history`: o `JOIN LATERAL` e interno, entao
**ausencia de foto e indistinguivel de fundo encerrado**. Quem escreve essa
tabela e `_refresh_pro_universe`, que roda dentro de `ingest_v2_details` --
uma rotina que depende do endpoint Pro de listagem e que, medido em
18/09/2026, nao roda desde 16/07 no armazem local e desde **14/07** no
Supabase. A foto mais recente da producao e justamente a de 393 tickers que
`core/fii_saidas.py` documenta como truncada (38% da maior foto).

O efeito medido no Supabase em 18/09/2026: 433 fundos com preco negociado,
**38 deles sem nenhuma linha de universo** -- somem da tela sem aparecer em
lista de descartados, porque o `JOIN` os elimina antes de qualquer criterio.
Nao e politica de exclusao: FIIB11, EURO11, BTAL11, TVRI11 e HUCG11 sao
fundos de tijolo classicos, ao lado de uma dezena de Fiagros.

Por que o preco serve de evidencia -- e so num sentido
-------------------------------------------------------
Preco negociado observado hoje prova que o fundo estava listado hoje. E
evidencia direta, colhida pela mesma rotina noturna que abastece
`market.fiis`, e nao depende do endpoint que parou.

A volta nao vale: **ausencia de preco nao prova saida**. Fundo ilíquido passa
pregoes sem negocio, e derivar encerramento disso dataria saida por iliquidez
([[evidencia-de-vida-e-de-morte-sao-assimetricas]]). Por isso estas linhas
levam `availability_quality = 'price_observed_proxy'` e `fotos_do_banco` em
`core/fii_saidas.py` as exclui: derivacao de saida compara LISTAGENS, e uma
observacao de vida nao e uma foto do universo.

Por que so os ausentes
----------------------
A linha so e escrita para o ticker que **nao tem nenhuma** observacao de
universo. Registrar os 433 todo dia encheria a tabela (~31 MB/ano contra 72 MB
de folga no Supabase) sem responder pergunta nova: onde ja existe foto de
listagem, ela e a evidencia melhor.
"""
from __future__ import annotations

import json
from datetime import date, datetime

QUALIDADE = "price_observed_proxy"
FONTE = "market.fiis:preco_negociado"
STATUS = "listed"

SQL_SEM_OBSERVACAO = """
    SELECT f.ticker
      FROM market.fiis f
     WHERE f.price > 0
       AND NOT EXISTS (SELECT 1 FROM market.fii_universe_history h
                        WHERE h.ticker = f.ticker)
     ORDER BY f.ticker
"""


def tickers_sem_observacao(conn) -> list[str]:
    """Fundos com preco vivo e nenhuma linha de universo -- os invisiveis."""
    from sqlalchemy import text
    return [str(r[0]) for r in conn.execute(text(SQL_SEM_OBSERVACAO))]


def linhas_de_vida(tickers: list[str], observado_em: datetime) -> list[dict]:
    """Uma observacao de vida por ticker, datada no instante da coleta.

    `reference_date` e a data do preco observado, nao a data de estreia do
    fundo: o que se afirma e "estava listado neste dia", que e o que se viu.
    """
    dia: date = observado_em.date()
    return [{
        "ticker": ticker,
        "reference_date": dia,
        "available_at": observado_em,
        "knowledge_at": observado_em,
        "availability_quality": QUALIDADE,
        "active_status": STATUS,
        "successor_ticker": None,
        "source": FONTE,
        "metadata_json": json.dumps(
            {"regra": "preco negociado observado em market.fiis",
             "nao_serve_para_derivar_saida": True},
            ensure_ascii=False),
    } for ticker in tickers]


def registrar(conn, observado_em: datetime) -> int:
    """Grava a prova de vida dos ausentes. Devolve quantas linhas entraram."""
    from sqlalchemy import text

    from data_pipeline.market import repository as repo
    if not conn.execute(text(
            "SELECT to_regclass('market.fii_universe_history') IS NOT NULL")).scalar():
        return 0
    linhas = linhas_de_vida(tickers_sem_observacao(conn), observado_em)
    if not linhas:
        return 0
    return int(repo.upsert(conn, "fii_universe_history", linhas))

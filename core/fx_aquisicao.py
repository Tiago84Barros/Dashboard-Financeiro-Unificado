"""Câmbio médio de aquisição por ativo em moeda estrangeira.

O custo em reais de uma posição comprada em dólar é o que se pagou **na data
de cada compra**, não o que se pagaria hoje. Converter o custo histórico pelo
câmbio de hoje produz um número que parece retorno em BRL mas é o retorno em
USD com outro rótulo: a mesma taxa aparece no numerador e no denominador e se
cancela.

Este módulo recompõe o câmbio que faltava, cruzando ``investment_transactions``
(que guarda ``transaction_date`` de cada compra) com a série ``USDBRL`` de
``asset_quotes``. O resultado é a média das taxas ponderada pelo custo de cada
compra — a mesma ponderação que forma o preço médio, para que as duas pontas
do retorno falem da mesma cesta.

**Por que isso existe:** o campo ``fx_rate_compra`` nascia ``None`` fixo, e o
card de retorno consolidado dizia "falta câmbio histórico de aquisição". O dado
estava no banco desde 2021. Critério que nunca pode ser satisfeito nunca é
revisto — quando a fonte chega, ninguém percebe.

Só USD é tratado aqui. A série de câmbio é a do par USDBRL, e aplicá-la a uma
posição em euro daria um número errado com cara de certo.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Dias corridos que a busca anda para trás a partir da data da compra.
#: Compra em fim de semana ou feriado não tem fechamento no próprio dia; três
#: dias cobrem o sábado/domingo e quatro cobrem um feriado colado neles.
JANELA_DIAS = 5

_SQL_CAMBIO_AQUISICAO = """
    WITH compras AS (
        SELECT upper(a.ticker)                AS ticker,
               it.transaction_date            AS data,
               it.quantity * it.unit_price    AS custo_usd
        FROM   investment_transactions it
        JOIN   assets a ON a.id = it.asset_id
        WHERE  it.user_id = :uid
          AND  lower(it.type) = 'buy'
          AND  upper(coalesce(a.currency, 'BRL')) = 'USD'
          AND  it.quantity > 0
          AND  it.unit_price > 0
    ),
    com_fx AS (
        SELECT c.ticker, c.custo_usd, fx.close AS taxa
        FROM   compras c
        LEFT JOIN LATERAL (
            SELECT q.close
            FROM   asset_quotes q
            JOIN   assets fa ON fa.id = q.asset_id
            WHERE  fa.ticker = 'USDBRL'
              AND  q.close > 0
              AND  q.timestamp::date <= c.data
              AND  q.timestamp::date >= c.data - :janela
            ORDER  BY q.timestamp DESC
            LIMIT  1
        ) fx ON true
    )
    SELECT ticker,
           sum(custo_usd)                                      AS custo_usd,
           sum(custo_usd)        FILTER (WHERE taxa IS NOT NULL) AS custo_coberto,
           sum(custo_usd * taxa) FILTER (WHERE taxa IS NOT NULL) AS custo_brl,
           count(*)                                            AS n_compras,
           count(*)              FILTER (WHERE taxa IS NOT NULL) AS n_com_taxa
    FROM   com_fx
    GROUP  BY ticker
"""


def cambio_medio_de_aquisicao(conn, owner_id: str,
                              *, janela_dias: int = JANELA_DIAS) -> dict[str, dict]:
    """``{TICKER: {taxa_media, cobertura, custo_usd, custo_brl, n_compras}}``.

    ``cobertura`` é a fração do **custo** (não do número de compras) que achou
    câmbio na janela. Quem consome decide o que fazer com cobertura parcial;
    aqui ela é só medida e devolvida, porque uma cobertura de 0,98 e uma de
    0,02 não merecem o mesmo tratamento e este módulo não sabe qual é qual.

    Falha de leitura devolve ``{}`` — o chamador mantém o comportamento
    anterior, que já era conservador. Nunca levanta.
    """
    from sqlalchemy import text

    try:
        linhas = conn.execute(text(_SQL_CAMBIO_AQUISICAO),
                              {"uid": owner_id, "janela": janela_dias}).fetchall()
    except Exception:  # noqa: BLE001 - ausência de tabela não pode derrubar a carteira
        logger.warning("câmbio de aquisição indisponível", exc_info=True)
        return {}

    saida: dict[str, dict] = {}
    for linha in linhas:
        custo_usd = float(linha.custo_usd or 0.0)
        custo_coberto = float(linha.custo_coberto or 0.0)
        custo_brl = float(linha.custo_brl or 0.0)
        if custo_usd <= 0 or custo_coberto <= 0:
            continue
        saida[str(linha.ticker).upper()] = {
            "taxa_media": custo_brl / custo_coberto,
            "cobertura": custo_coberto / custo_usd,
            "custo_usd": custo_usd,
            "custo_brl": custo_brl,
            "n_compras": int(linha.n_compras or 0),
            "n_com_taxa": int(linha.n_com_taxa or 0),
        }
    return saida


def taxa_para(mapa: dict[str, dict], ticker: str,
              *, cobertura_minima: float = 1.0) -> float | None:
    """Taxa de aquisição de ``ticker``, ou ``None`` se a cobertura não basta.

    O padrão exige cobertura total porque custo é soma: converter metade das
    compras pelo câmbio da época e a outra metade pelo de hoje devolveria um
    custo que não existiu em nenhum dos dois mundos. Cobertura parcial cai no
    caminho antigo, que ao menos se declara estimado.
    """
    info = mapa.get(str(ticker or "").upper())
    if not info:
        return None
    if info.get("cobertura", 0.0) < cobertura_minima:
        return None
    taxa = info.get("taxa_media")
    return float(taxa) if taxa and taxa > 0 else None

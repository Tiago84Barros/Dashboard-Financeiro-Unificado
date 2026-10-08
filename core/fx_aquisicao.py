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

#: Mesma tolerância de ``data_pipeline/importers/investments/positions.py``:
#: abaixo disto a posição está zerada e a próxima compra abre base nova.
TOLERANCIA_ZERADA = 0.0001

#: USDBRL abaixo disto é dado corrompido (a série nunca esteve perto), e uma
#: taxa de 0,01 converteria o custo em reais para quase nada.
TAXA_MINIMA = 2.0

_SQL_CAMBIO_AQUISICAO = """
    SELECT upper(a.ticker)               AS ticker,
           lower(it.type)                AS tipo,
           it.quantity                   AS quantidade,
           it.quantity * it.unit_price   AS custo_usd,
           fx.close                      AS taxa
    FROM   investment_transactions it
    JOIN   assets a ON a.id = it.asset_id
    LEFT JOIN LATERAL (
        SELECT q.close
        FROM   asset_quotes q
        JOIN   assets fa ON fa.id = q.asset_id
        WHERE  lower(it.type) = 'buy'
          AND  fa.ticker = 'USDBRL'
          AND  q.close >= :taxa_minima
          AND  q.timestamp::date <= it.transaction_date
          AND  q.timestamp::date >= it.transaction_date - :janela
        ORDER  BY q.timestamp DESC
        LIMIT  1
    ) fx ON true
    WHERE  it.user_id = :uid
      AND  lower(it.type) IN ('buy', 'sell')
      AND  upper(coalesce(a.currency, 'BRL')) = 'USD'
      AND  it.quantity > 0
    ORDER  BY upper(a.ticker), it.transaction_date, it.created_at, it.id
"""


def _lote_corrente(movimentos) -> dict[str, dict]:
    """Custo em USD e em BRL da posição **atual**, movimento a movimento.

    Espelha o custo médio de ``positions.py::_compute``, que é quem forma o
    ``total_invested`` que esta taxa vai converter:

    - compra soma quantidade e custo;
    - venda parcial tira a mesma fração da quantidade, do custo em USD e do
      custo em BRL — o preço médio não muda, e a taxa média também não;
    - venda que zera a posição (ou passa dela) descarta o lote, e a compra
      seguinte começa base nova.

    Sem isso, um lote comprado a 4,00 e vendido inteiro continuava pesando na
    taxa da recompra a 6,00, e o custo em reais da posição de hoje saía com
    câmbio de ações que já não existem.
    """
    estado: dict[str, dict] = {}
    for m in movimentos:
        ticker = str(m.ticker).upper()
        e = estado.setdefault(ticker, {"qtd": 0.0, "custo_usd": 0.0, "custo_coberto": 0.0,
                                       "custo_brl": 0.0, "n_compras": 0, "n_com_taxa": 0})
        qtd = float(m.quantidade or 0.0)
        if m.tipo == "buy":
            custo = float(m.custo_usd or 0.0)
            e["qtd"] += qtd
            if custo <= 0:
                continue  # quantidade sem custo: nada a converter, nada a cobrir
            e["custo_usd"] += custo
            e["n_compras"] += 1
            if m.taxa is not None and float(m.taxa) >= TAXA_MINIMA:
                e["custo_coberto"] += custo
                e["custo_brl"] += custo * float(m.taxa)
                e["n_com_taxa"] += 1
            continue
        restante = e["qtd"] - qtd
        if restante <= TOLERANCIA_ZERADA:
            e.update(qtd=0.0, custo_usd=0.0, custo_coberto=0.0, custo_brl=0.0,
                     n_compras=0, n_com_taxa=0)
            continue
        fracao = restante / e["qtd"]
        e["qtd"] = restante
        for chave in ("custo_usd", "custo_coberto", "custo_brl"):
            e[chave] *= fracao
    return estado


def cambio_medio_de_aquisicao(conn, owner_id: str,
                              *, janela_dias: int = JANELA_DIAS) -> dict[str, dict]:
    """``{TICKER: {taxa_media, cobertura, custo_usd, custo_brl, n_compras}}``.

    Só as compras que formam a posição atual entram (ver ``_lote_corrente``).

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
                              {"uid": owner_id, "janela": janela_dias,
                               "taxa_minima": TAXA_MINIMA}).fetchall()
    except Exception:  # noqa: BLE001 - ausência de tabela não pode derrubar a carteira
        logger.warning("câmbio de aquisição indisponível", exc_info=True)
        return {}

    saida: dict[str, dict] = {}
    for ticker, e in _lote_corrente(linhas).items():
        custo_usd, custo_coberto = e["custo_usd"], e["custo_coberto"]
        if custo_usd <= 0 or custo_coberto <= 0:
            continue
        saida[ticker] = {
            "taxa_media": e["custo_brl"] / custo_coberto,
            "cobertura": custo_coberto / custo_usd,
            "custo_usd": custo_usd,
            "custo_brl": e["custo_brl"],
            "n_compras": e["n_compras"],
            "n_com_taxa": e["n_com_taxa"],
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

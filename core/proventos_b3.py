"""Proventos de renda da B3 pela resposta mais nova da brapi.

``market.dividends`` não serve de fonte para a soma anual. A tabela é única
por (ticker, event_date, tipo, valor) e o upsert não apaga: cada revisão da
brapi (data de pagamento corrigida, data-ex deslocada, a duplicata que a
própria brapi limpou depois) deixa a linha antiga ao lado da nova. Medido em
29/10/2026 no armazém local, contra a resposta mais nova da brapi arquivada em
``market.brapi_raw_payloads``: a soma crua por ano batia em 65,6% dos
ticker-anos, e a melhor regra de leitura
(:func:`core.dividend_types.renda_por_evento`) em 86,0%. Nenhuma regra de
leitura reconstrói o que a fonte respondeu por último; a resposta arquivada é
esse dado.

O publicador (``scripts/publish_proventos_b3.py``) lê a foto no armazém e grava
a soma anual em ``data/public/proventos_b3.json.gz``. Este módulo guarda as
duas metades:

* :func:`eventos_das_fotos` -- a regra única que transforma fotos em eventos,
  usada pelo publicador e pelo valuation histórico;
* :func:`renda_anual` -- a leitura do arquivo publicado, com o banco como
  reserva só para o ticker que o arquivo não cobre.
"""
from __future__ import annotations

from pathlib import Path

ARQUIVO = Path(__file__).resolve().parents[1] / "data" / "public" / "proventos_b3.json.gz"

#: Rótulos de procedência em ``attrs["fonte"]`` de :func:`renda_anual`.
FONTE_ARQUIVO = "brapi (resposta mais nova)"
FONTE_BANCO = "market.dividends"

#: Colunas de :func:`eventos_das_fotos`.
COLUNAS = ("ticker", "type", "ex_date", "payment_date", "event_date", "amount", "foto_em")

#: A resposta mais nova de cada ticker que trouxe a lista de proventos. Lista
#: vazia fica de fora: a brapi sem nada a dizer não é prova de que a ação não
#: paga, e o leitor cai no banco para esse ticker.
SQL_FOTOS = """
    SELECT ticker, fetched_at, symbol, dd FROM (
        SELECT DISTINCT ON (ticker) ticker, fetched_at,
               payload_json->>'symbol' AS symbol,
               payload_json->'dividendsData' AS dd
          FROM market.brapi_raw_payloads
         WHERE endpoint = 'quote'
           AND jsonb_typeof(payload_json->'dividendsData'->'cashDividends') = 'array'
         ORDER BY ticker, fetched_at DESC
    ) AS u
     WHERE jsonb_array_length(dd->'cashDividends') > 0
"""


def eventos_das_fotos(fotos):
    """Um provento de renda por linha, com as colunas de :data:`COLUNAS`.

    ``fotos`` são tuplas ``(ticker, fetched_at, symbol, dividendsData)``, como
    :data:`SQL_FOTOS` devolve. Cada foto passa pela mesma normalização da
    ingestão (``data_pipeline.market.normalize.dividend_rows``, que já tira o
    eco de classe) e depois:

    * só renda (A-128): amortização e restituição de capital saem;
    * a cópia com pagamento no dia da data-ex sai quando o mês tem a linha de
      calendário real (A-131);
    * linha idêntica repetida conta uma vez.

    Valores diferentes com a mesma data-ex e o mesmo pagamento ficam todos:
    dentro de uma resposta só, são declarações distintas (306 grupos medidos
    em 29/10/2026, todos com valores diferentes de verdade).

    ``event_date`` é o pagamento, ou a data-ex quando a brapi não informa o
    pagamento -- a mesma regra de ``market.dividends``.
    """
    import pandas as pd

    from core.dividend_types import apenas_renda, descarta_safra_colapsada
    from data_pipeline.market.normalize import dividend_rows

    linhas = []
    for ticker, foto_em, symbol, dd in fotos:
        tk = str(ticker).strip().upper()
        for r in dividend_rows({"symbol": symbol or tk, "dividendsData": dd}):
            r["ticker"] = tk
            r["foto_em"] = foto_em
            linhas.append(r)
    if not linhas:
        return pd.DataFrame(columns=list(COLUNAS))
    d = pd.DataFrame(linhas)
    for c in ("ex_date", "payment_date"):
        d[c] = pd.to_datetime(d[c], errors="coerce")
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce").round(6)
    d = descarta_safra_colapsada(apenas_renda(d))
    d = d.drop_duplicates(["ticker", "type", "ex_date", "payment_date", "amount"])
    d = d.assign(event_date=d["payment_date"].fillna(d["ex_date"]))
    d = d[d["amount"].gt(0) & d["event_date"].notna()]
    return (d[list(COLUNAS)].sort_values(["ticker", "event_date", "type"])
             .reset_index(drop=True))


def eventos_do_armazem(conn):
    """Todos os eventos de renda, para quem já lê o armazém local.

    Ticker com foto sai da foto; o resto, de ``market.dividends`` pela regra
    de leitura :func:`core.dividend_types.renda_por_evento` -- a mesma partilha
    de :func:`renda_anual`. Sem a coluna ``foto_em``.
    """
    import pandas as pd
    from sqlalchemy import text

    from core.dividend_types import renda_por_evento, sql_proventos_de_renda

    fotos = conn.execute(text(SQL_FOTOS)).fetchall()
    cobertos = sorted({str(f[0]).strip().upper() for f in fotos})
    bruto = pd.read_sql_query(
        text(sql_proventos_de_renda("NOT (upper(d.ticker) = ANY(:cobertos))")),
        conn, params={"cobertos": cobertos})
    partes = [eventos_das_fotos(fotos).drop(columns="foto_em"), renda_por_evento(bruto)]
    partes = [p for p in partes if not p.empty]
    if not partes:
        return pd.DataFrame(columns=[c for c in COLUNAS if c != "foto_em"])
    return (pd.concat(partes, ignore_index=True)
              .sort_values(["ticker", "event_date", "type"]).reset_index(drop=True))


def carregar() -> dict:
    """O arquivo publicado, ou ``{}`` se ausente ou ilegível. Não mutar."""
    from core.inteligencia_ativos import arquivo_publicado

    return arquivo_publicado.ler(str(ARQUIVO), "proventos_b3") or {}


def renda_anual(tickers):
    """``ticker, ano, total`` dos proventos de renda por ano de pagamento.

    O ticker que o arquivo publicado cobre sai inteiro dele: a resposta mais
    nova da brapi é a história completa do ticker, e misturar anos do banco
    traria de volta as cópias que o arquivo existe para não ter. O ticker que
    o arquivo não cobre -- ou todos, sem arquivo -- vem de ``market.dividends``
    pela regra de leitura :func:`core.dividend_types.renda_por_ano`. Falha do
    banco chega em ``attrs["load_error"]``; ``attrs["fonte"]`` nomeia de onde
    o resultado veio, para o rótulo da tela.
    """
    import pandas as pd

    from core.dividend_types import renda_por_ano, sql_proventos_de_renda

    tks = sorted({str(t).strip().upper().removesuffix(".SA") for t in tickers if t})
    publicado = carregar().get("por_ticker") or {}
    linhas = [(tk, int(ano), float(total))
              for tk in tks if tk in publicado
              for ano, total in (publicado[tk].get("anos") or {}).items()]
    out = pd.DataFrame(linhas, columns=["ticker", "ano", "total"])
    faltam = [tk for tk in tks if tk not in publicado]
    fontes = [FONTE_ARQUIVO] if len(faltam) < len(tks) else []
    erro = None
    if faltam:
        from core.market_read import _q

        bruto = _q(sql_proventos_de_renda("d.ticker = ANY(:tks)"), {"tks": faltam})
        erro = bruto.attrs.get("load_error")
        reserva = renda_por_ano(bruto)
        if not reserva.empty:
            out = pd.concat([out, reserva], ignore_index=True) if not out.empty else reserva
        fontes.append(FONTE_BANCO)
    out = out.sort_values(["ticker", "ano"]).reset_index(drop=True)
    out.attrs["fonte"] = " + ".join(fontes)
    if erro:
        out.attrs["load_error"] = erro
    return out

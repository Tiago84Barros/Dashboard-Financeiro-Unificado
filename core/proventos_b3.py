"""Proventos de renda da B3 pela resposta mais nova da brapi.

``market.dividends`` não serve de fonte para a soma anual. A tabela é única
por (ticker, event_date, tipo, valor) e o upsert não apaga: cada revisão da
brapi (data de pagamento corrigida, data-ex deslocada, a duplicata que a
própria brapi limpou depois) deixa a linha antiga ao lado da nova. Medido em
29/10/2026 por ``scripts/medir_proventos_b3.py`` (ticker-ano dos 883 tickers do
arquivo, 1% de tolerância, junção externa): contra a resposta mais nova da
brapi, a soma crua por ano bate em 60,3% dos ticker-anos e a melhor regra de
leitura (:func:`core.dividend_types.renda_por_evento`) em 86,0%. Nenhuma regra
de leitura reconstrói o que a fonte respondeu por último; a resposta arquivada
é esse dado.

Essa medida é circular: a referência é a própria fonte do arquivo, que bate
100% por construção. Ela mede a distância até o que a brapi responde hoje, não
se a brapi está certa. A única referência independente é a CVM, e só para FII
(rendimento implícito do informe mensal, pago no mês seguinte ao da
competência, 1.450 ticker-anos de 327 fundos): o arquivo fica a até 5% dela em
69,7% dos ticker-anos, a regra de leitura em 69,5% e a soma crua em 60,4%. O
número depende da convenção: com o pagamento no próprio mês ou dois meses
depois, e contando o ano que o arquivo não tem, o arquivo fica a até 5% em
56,1% a 69,7%. Em todas as variantes ele e a regra empatam, e a soma crua fica
8 a 9 pontos abaixo. A CVM também erra -- o fundo que não paga no mês suposto
desloca valor entre anos --, e o desacordo é entre duas fontes imperfeitas: não
é o erro da brapi nem um limite para ele, porque as duas podem errar no mesmo
sentido. Na ação não há fonte independente para conferir.

O publicador (``scripts/publish_proventos_b3.py``) lê a foto no armazém e grava
a soma anual em ``data/public/proventos_b3.json.gz``. Este módulo guarda as
duas metades:

* :func:`eventos_das_fotos` -- a regra única que transforma fotos em eventos,
  usada pelo publicador e pelo valuation histórico;
* :func:`renda_anual` -- a leitura do arquivo publicado, com o banco como
  reserva só para o ticker que o arquivo não cobre.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parents[1] / "data" / "public" / "proventos_b3.json.gz"

#: Rótulos de procedência em ``attrs["fonte"]`` de :func:`renda_anual`.
FONTE_ARQUIVO = "brapi (resposta mais nova)"
FONTE_BANCO = "market.dividends"

#: `b3_brapi` renova a resposta de cada ticker toda noite. Dez dias corridos
#: cobrem algumas noites perdidas sem deixar passar coleta realmente parada.
#: O publicador recusa o arquivo quando a resposta mais nova de todas passa
#: disso; :func:`procedencia` marca o ticker cuja resposta passou.
FOTO_MAXIMA_DIAS = 10

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


def anos_que_a_foto_nao_tem(foto, banco):
    """Eventos do banco nos anos em que a foto do ticker não tem provento.

    ``foto`` e ``banco`` têm as colunas de eventos (:func:`eventos_das_fotos`
    sem ``foto_em``; ``banco`` já passou por
    :func:`core.dividend_types.renda_por_evento`). O ano é o da data-ex (o
    pagamento quando falta a data-ex), a mesma régua da soma do DY. Só entra
    o ticker que tem foto, e só o ano inteiro que ela não tem: num ano que a
    foto cobre, a resposta mais nova decide, e o banco não completa parcela.

    A foto mais nova perde linhas que a brapi já deu (medido em 29/10/2026:
    354 tickers com mais de 10% menos linhas que a maior foto); o ano que
    some inteiro deixa o DY e o payout do valuation anual sem número. O
    banco devolve esse ano -- menos a cópia de um provento que a foto tem
    num ano vizinho: mesmo tipo, mesmo valor (``TOLERANCIA_VALOR``) e data-ex
    a até ``JANELA_EX_DIAS`` dias -- e, quando os dois lados têm pagamento
    válido (depois da data-ex), pagamentos a até ``JANELA_PAGAMENTO_DIAS``
    dias. São as janelas de :func:`core.dividend_types.renda_por_evento`:
    data-ex vizinha com pagamentos meses à parte são dois proventos, e data-ex
    distante com o mesmo pagamento também (JCPs declarados em meses
    diferentes e pagos juntos). O mesmo pagamento só basta quando falta a
    data-ex num dos lados. O tipo entra porque um JCP e um DIVIDENDO do mesmo
    valor, perto da virada, são dois proventos (``renda_por_evento`` também
    os separa); tipo
    ausente num dos lados não separa. É o caso da data-ex regravada
    na virada do ano (30/12 na foto, 03/01 no banco), que contaria duas vezes.
    """
    import numpy as np
    import pandas as pd

    from core.dividend_types import (
        JANELA_EX_DIAS,
        JANELA_PAGAMENTO_DIAS,
        TOLERANCIA_VALOR,
    )

    colunas = [c for c in COLUNAS if c != "foto_em"]
    if banco is None or banco.empty or foto is None or foto.empty:
        return pd.DataFrame(columns=colunas)

    def _ex(d):
        return pd.to_datetime(d["ex_date"], errors="coerce").fillna(
            pd.to_datetime(d["event_date"], errors="coerce"))

    f = foto.assign(_ex=_ex(foto), ticker=foto["ticker"].astype(str).str.upper())
    b = banco.assign(_ex=_ex(banco), ticker=banco["ticker"].astype(str).str.upper())
    anos_f = set(zip(f["ticker"], f["_ex"].dt.year))
    b = b[b["ticker"].isin(set(f["ticker"]))]
    b = b[[(t, a) not in anos_f for t, a in zip(b["ticker"], b["_ex"].dt.year)]]
    if b.empty:
        return pd.DataFrame(columns=colunas)
    b = b.reset_index(drop=True).assign(_i=lambda x: x.index)
    cols = ["ticker", "type", "ex_date", "_ex", "payment_date", "amount"]
    par = b[["_i", *cols]].merge(f[cols], on="ticker", suffixes=("", "_f"))
    tipo, tipo_f = (par[c].astype("string").str.strip().str.upper() for c in ("type", "type_f"))
    mesmo_tipo = tipo.isna() | tipo_f.isna() | tipo.eq(tipo_f).fillna(False)
    mesmo_valor = ((par["amount"] - par["amount_f"]).abs()
                   <= TOLERANCIA_VALOR * np.maximum(par["amount"], par["amount_f"]))
    pag, pag_f = (pd.to_datetime(par[c], errors="coerce") for c in ("payment_date", "payment_date_f"))
    sem_ex = (pd.to_datetime(par["ex_date"], errors="coerce").isna()
              | pd.to_datetime(par["ex_date_f"], errors="coerce").isna())
    mesmo_pagamento = sem_ex & pag.notna() & pag.eq(pag_f)
    ex_perto = (par["_ex"] - par["_ex_f"]).abs() <= pd.Timedelta(days=JANELA_EX_DIAS)
    validos = (pag.notna() & pag.gt(par["_ex"])) & (pag_f.notna() & pag_f.gt(par["_ex_f"]))
    pag_perto = (pag - pag_f).abs() <= pd.Timedelta(days=JANELA_PAGAMENTO_DIAS)
    ex_perto = ex_perto & (~validos | pag_perto)
    copia = set(par.loc[mesmo_tipo & mesmo_valor & (mesmo_pagamento | ex_perto), "_i"])
    return b[~b["_i"].isin(copia)][colunas].reset_index(drop=True)


def eventos_do_armazem(conn):
    """Todos os eventos de renda, para quem já lê o armazém local.

    Ticker com foto sai da foto, mais os anos que ela não tem
    (:func:`anos_que_a_foto_nao_tem`); o resto, de ``market.dividends`` pela
    regra de leitura :func:`core.dividend_types.renda_por_evento` -- a mesma
    partilha de :func:`renda_anual`. Sem a coluna ``foto_em``. É a fonte do
    DY e do payout dos dois caminhos do valuation anual (o reprocess de
    ``data_pipeline.market.ingest`` e ``scripts/publish_valuation_historico``).
    ``attrs["anos_do_banco"]`` conta os ticker-anos que vieram do banco para
    tickers com foto. A soma publicada em :data:`ARQUIVO` continua só da
    foto: ela é a resposta da brapi, e o banco é reserva.
    """
    import pandas as pd
    from sqlalchemy import text

    from core.dividend_types import renda_por_evento, sql_proventos_de_renda

    fotos = conn.execute(text(SQL_FOTOS)).fetchall()
    cobertos = {str(f[0]).strip().upper() for f in fotos}
    bruto = pd.read_sql_query(text(sql_proventos_de_renda("TRUE")), conn)
    banco = renda_por_evento(bruto)
    da_foto = eventos_das_fotos(fotos).drop(columns="foto_em")
    coberto = banco["ticker"].isin(cobertos) if not banco.empty else []
    preenche = anos_que_a_foto_nao_tem(da_foto, banco[coberto] if len(banco) else banco)
    partes = [da_foto, preenche, banco[~coberto] if len(banco) else banco]
    partes = [p for p in partes if not p.empty]
    if not partes:
        out = pd.DataFrame(columns=[c for c in COLUNAS if c != "foto_em"])
    else:
        out = (pd.concat(partes, ignore_index=True)
                 .sort_values(["ticker", "event_date", "type"]).reset_index(drop=True))
    ex = pd.to_datetime(preenche["ex_date"], errors="coerce").fillna(
        pd.to_datetime(preenche["event_date"], errors="coerce"))
    out.attrs["anos_do_banco"] = int(len(set(zip(preenche["ticker"], ex.dt.year))))
    return out


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
    o resultado veio e ``attrs["foto_em"]`` traz o dia da resposta de cada
    ticker coberto, para o rótulo da tela (:func:`procedencia`).

    ``ano`` pode passar do ano corrente: é o pagamento declarado e ainda não
    feito (41 tickers com total em 2027 no arquivo de 29/10/2026). O ano
    corrente também está pela metade. Quem compara anos corta os dois
    (:func:`core.crescimento.sem_ano_em_curso`). A série por exercício
    (``core.market_read.load_demonstracoes``) só lê os anos com demonstração
    publicada e deixa vazio o ano civil que não fechou.
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
    out.attrs["foto_em"] = {tk: publicado[tk].get("foto_em") for tk in tks if tk in publicado}
    if erro:
        out.attrs["load_error"] = erro
    return out


def idade_da_foto(attrs: dict, hoje: date | None = None) -> tuple[date, int] | None:
    """A resposta mais velha entre os tickers que o arquivo cobriu e a idade
    dela em dias, ou ``None`` sem nenhum ticker coberto com data."""
    dias = []
    for v in (attrs.get("foto_em") or {}).values():
        try:
            dias.append(date.fromisoformat(str(v)))
        except ValueError:
            continue
    if not dias:
        return None
    dia = min(dias)
    return dia, ((hoje or date.today()) - dia).days


def procedencia(attrs: dict, hoje: date | None = None) -> str:
    """``attrs["fonte"]`` de :func:`renda_anual` com o dia da resposta da brapi.

    O publicador só recusa a coleta parada do conjunto. O ticker que a brapi
    deixou de renovar segue no arquivo com a resposta antiga (32 de 883 com
    mais de :data:`FOTO_MAXIMA_DIAS` dias em 29/10/2026, a mais velha com
    118), e sem a data passaria por atual.
    """
    fonte = attrs.get("fonte") or ""
    foto = idade_da_foto(attrs, hoje)
    if foto is None or FONTE_ARQUIVO not in fonte:
        return fonte
    dia, idade = foto
    rotulo = f"brapi, resposta de {dia:%d/%m/%Y}"
    if idade > FOTO_MAXIMA_DIAS:
        rotulo += f" — VELHA: {idade} dias sem renovação"
    return fonte.replace(FONTE_ARQUIVO, rotulo)


def fotos_velhas(lote: dict, hoje: date | None = None) -> list[tuple[str, str]]:
    """``(ticker, aviso)`` de cada tabela do lote cuja resposta da brapi passou
    de :data:`FOTO_MAXIMA_DIAS` dias sem renovação.

    ``lote`` é o de ``load_demonstracoes_batch``: cada tabela leva no próprio
    ``attrs`` o dia da resposta do seu ticker. Quem lê a coluna ``Dividendos``
    do lote manda cada aviso para a fila de Restrições.
    """
    out = []
    for tk, df in (lote or {}).items():
        foto = idade_da_foto(getattr(df, "attrs", None) or {}, hoje)
        if foto is not None and foto[1] > FOTO_MAXIMA_DIAS:
            out.append((tk, f"Proventos de {tk}: a resposta mais nova da brapi é de "
                            f"{foto[0]:%d/%m/%Y}, {foto[1]} dias sem renovação."))
    return out

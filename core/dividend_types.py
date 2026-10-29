"""Distingue renda de devolucao de capital em ``market.dividends``.

A-128: ``AMORTIZACAO`` e ``REST CAP DIN`` nao sao rendimento -- sao devolucao
do capital do proprio cotista. Somados ao provento, inflam o dividend yield e,
em FII, inflam justamente a metrica de manchete da decisao. Medido em
2026-08-24 sobre o Supabase: 415 pares ticker-ano inflados em 234 tickers,
inflacao media de 139%; RBRI11/2026 exibia 252,20 de "provento" com renda
real zero.

A-129: a mesma agregacao somava o eco de classe da brapi (o mesmo evento
gravado sob CEBR5 e CEBR6, por exemplo). Dentro de um mesmo (data, tipo), o
valor honesto e o MINIMO -- somar repete o evento. Entre tipos DISTINTOS na
mesma data, somar e correto: dividendo e JCP no mesmo dia sao dois eventos de
verdade, e sao 1.120 ocorrencias na base.
"""
from __future__ import annotations

#: Eventos que remuneram o investidor sem devolver o principal.
TIPOS_RENDA: frozenset[str] = frozenset({"RENDIMENTO", "JCP", "DIVIDENDO"})

#: Eventos que devolvem capital. Entram no fluxo de caixa, nunca no yield.
TIPOS_DEVOLUCAO_CAPITAL: frozenset[str] = frozenset({"AMORTIZAÇÃO", "REST CAP DIN"})


def eh_renda(tipo: str | None) -> bool:
    """``True`` para evento de renda. Tipo desconhecido conta como renda.

    A escolha e deliberada: um tipo novo que a ingestao traga aparece no yield
    (e portanto e visivel) em vez de sumir em silencio. Sumir seria o erro
    caro -- a-124 mostrou o que custa um sinal que ninguem ve.
    """
    if tipo is None:
        return True
    return str(tipo).strip().upper() not in _CAPITAL_UPPER


_CAPITAL_UPPER = frozenset(t.upper() for t in TIPOS_DEVOLUCAO_CAPITAL)

#: Predicado SQL que mantem apenas eventos de renda. ``col`` e a coluna tipo.
def sql_apenas_renda(col: str = "type") -> str:
    lista = ", ".join(f"'{t}'" for t in sorted(TIPOS_DEVOLUCAO_CAPITAL))
    return f"({col} IS NULL OR upper({col}) NOT IN ({lista}))"


# ---------------------------------------------------------------------------
# A-131: as duas safras do mesmo pagamento
# ---------------------------------------------------------------------------
# `market.dividends` guarda o mesmo evento duas vezes para 307 tickers. Uma
# linha traz o calendario real da B3 (RELG11: ex 05/09, pagamento 12/09); a
# outra, criada em bloco entre 23 e 25/07/2026, projeta o evento para o dia 1
# do mes de pagamento e grava `payment_date = ex_date`.
#
# Esse colapso e a assinatura: nenhum evento real paga no dia em que fica ex --
# a mediana da defasagem na tabela e de 14 dias. O dia colapsado nao e uma
# data, e a ausencia de uma.
#
# Custo medido nos FIIs investiveis: 187 fundos com renda de 12 meses inflada,
# mediana +35,8%, maximo +90,9%. HGLG11 sai 64,7% acima; RELG11 quase dobra.
#
# A chave e o MES DO PAGAMENTO, nao o valor. HGLG11 tem copias de 0,9574 e
# 1,0734 ao lado de reais de 1,1000: casar por valor deixa as duas passarem, e
# alargar a tolerancia comeca a apagar evento legitimo. Tambem nao serve o mes
# do EX -- o par de HGLG11 fica ex em 31/10 e paga em 14/11, e a copia cai em
# 01/11; pelo mes do ex os dois nunca se encontrariam.
#
# Uma colapsada SEM gemea de calendario real sobrevive. Sao ~20 mil linhas de
# historico antigo em que a brapi nunca forneceu as duas datas, e descarta-las
# por formato apagaria evento que nao tem substituto. A regra so age quando ha
# o que preferir.
#
# Nada disso apaga linha: e filtro de leitura. A safra colapsada continua na
# tabela como evidencia de que a ingestao a produziu.


def apenas_renda(df):
    """Mantem so as linhas de renda de um quadro de ``market.dividends``.

    Irma em pandas de :func:`sql_apenas_renda`, para os consumidores que leem a
    tabela inteira e filtram em memoria (a safra PIT). O predicado sai de
    :func:`eh_renda`, entao a regra continua tendo um dono so -- guarda
    duplicada nao fica igual, e foi exatamente assim que o PIT passou a comer
    ``AMORTIZACAO`` na serie de renda enquanto a producao ja a excluia.

    Quadro sem a coluna ``type`` volta intacto: pela mesma escolha de
    :func:`eh_renda`, o que nao se sabe classificar conta como renda e fica
    visivel, em vez de sumir em silencio.
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return df if df is not None else pd.DataFrame()
    if "type" not in df.columns:
        return df
    return df.loc[df["type"].map(eh_renda)]


def eh_safra_colapsada(ex_date, payment_date) -> bool:
    """``True`` quando o pagamento cai no proprio dia da data-ex.

    Datas ausentes devolvem ``False``: nao ha prova de colapso, e presumir
    seria descartar por ignorancia.
    """
    if ex_date is None or payment_date is None:
        return False
    try:
        import pandas as _pd
        if _pd.isna(ex_date) or _pd.isna(payment_date):
            return False
    except Exception:  # pragma: no cover - pandas sempre presente no app
        pass
    return bool(ex_date == payment_date)


def descarta_safra_colapsada(df):
    """Remove a copia colapsada quando o mesmo (ticker, tipo, mes de pagamento)
    tem linha de calendario real. Devolve um quadro novo.

    Quadro sem as colunas de data volta intacto -- nao ha o que julgar.
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return df if df is not None else pd.DataFrame()
    if not {"ex_date", "payment_date"} <= set(df.columns):
        return df

    ex = pd.to_datetime(df["ex_date"], errors="coerce")
    pay = pd.to_datetime(df["payment_date"], errors="coerce")
    colapsada = ex.notna() & pay.notna() & (ex == pay)
    if not colapsada.any():
        return df

    chave = pd.MultiIndex.from_arrays([
        df["ticker"].astype(str) if "ticker" in df.columns else pd.Series([""] * len(df)),
        df["type"].astype(str) if "type" in df.columns else pd.Series([""] * len(df)),
        pay.dt.to_period("M").astype(str),
    ])
    # Meses que ja tem a versao de calendario real: so neles a copia sobra.
    com_real = set(chave[~colapsada & pay.notna()])
    descartar = colapsada & pd.Series([k in com_real for k in chave], index=df.index)
    return df.loc[~descartar]


def sql_safra_canonica(alias: str = "d", tabela: str = "market.dividends") -> str:
    """Predicado ``WHERE`` que aplica a mesma regra dentro do banco.

    O ``COALESCE`` nao e decorativo: ``payment_date = ex_date`` vale NULL
    quando uma das datas falta, e ``NOT NULL`` tambem e NULL -- a linha sairia
    do resultado em silencio. Descartar o que nao se sabe julgar e o erro que
    este modulo existe para nao cometer.
    """
    return f"""NOT COALESCE(
        {alias}.payment_date = {alias}.ex_date
        AND EXISTS (SELECT 1 FROM {tabela} _sc
                     WHERE _sc.ticker = {alias}.ticker
                       AND _sc.type IS NOT DISTINCT FROM {alias}.type
                       AND _sc.payment_date IS NOT NULL
                       AND _sc.payment_date <> _sc.ex_date
                       AND date_trunc('month', _sc.payment_date)
                           = date_trunc('month', {alias}.payment_date)),
        false)"""


# ---------------------------------------------------------------------------
# A agregacao anual unica
# ---------------------------------------------------------------------------
# Tres regras ja existiam, cada uma num leitor diferente -- e cada leitor
# aplicava as que lembrava. `load_demonstracoes`, a fonte do card e do grafico
# de dividendos da Empresas B3 e da serie de dividendos da LLM, somava a coluna
# crua, com a copia por lote de mil e a amortizacao dentro.
#
# A identidade de um provento nao cabe numa chave de colunas. A tabela e unica
# por (ticker, event_date, tipo, valor) e o upsert nao apaga: quando a brapi
# muda a data de pagamento, a linha antiga fica ao lado da nova. O mesmo
# provento aparece entao
#   - com dois pagamentos a poucos dias um do outro (a revisao da data);
#   - com pagamento nulo, ou anterior a data-ex, ao lado do pagamento real;
#   - com a data-ex deslocada em poucos dias e o mesmo valor.
# E ha o contrario, o que parece copia e nao e:
#   - a mesma declaracao paga em parcelas iguais (mesma data-ex, pagamentos em
#     junho e em dezembro);
#   - declaracoes distintas pagas no mesmo dia (JCP trimestrais com datas-ex
#     de meses diferentes, todos com pagamento "a definir" em 31/12).
# Agrupar por dia de pagamento, a primeira tentativa, juntava o segundo caso
# com o primeiro: medido em 29/10/2026 no armazem local, eram 663 grupos de
# acoes com datas-ex a mais de 15 dias uma da outra.

#: Duas linhas sao o mesmo provento quando o valor coincide a 0,1%, a data-ex
#: difere em ate 15 dias e o pagamento em ate 31. Fora disso sao parcelas ou
#: declaracoes distintas, e somam. Valor e data-ex sao os da regra B de
#: `core.brapi.dedup_cash_dividends`.
TOLERANCIA_VALOR = 1e-3
JANELA_EX_DIAS = 15
JANELA_PAGAMENTO_DIAS = 31

#: Colunas que :func:`renda_por_evento` devolve.
COLUNAS_EVENTO = ("ticker", "type", "ex_date", "payment_date", "event_date", "amount")


def sql_proventos_de_renda(filtro: str, *, alias: str = "d",
                           tabela: str = "market.dividends") -> str:
    """SQL das linhas que :func:`renda_por_evento` deduplica.

    O banco so filtra o que decide melhor: renda (A-128), safra canonica
    (A-131, que precisa ver a tabela inteira para achar a gemea) e valor
    positivo. ``filtro`` e o predicado de ticker sobre ``alias``, com os
    parametros de quem chama.
    """
    return f"""
        SELECT {alias}.ticker, {alias}.type, {alias}.ex_date, {alias}.payment_date,
               {alias}.event_date, {alias}.amount, {alias}.created_at
        FROM {tabela} {alias}
        WHERE ({filtro}) AND {alias}.event_date IS NOT NULL AND {alias}.amount > 0
          AND {sql_apenas_renda(f'{alias}.type')}
          AND {sql_safra_canonica(alias, tabela)}"""


def renda_por_evento(df):
    """Um provento de renda por linha, com as colunas de :data:`COLUNAS_EVENTO`.

    ``df`` e um quadro de ``market.dividends`` -- de preferencia o de
    :func:`sql_proventos_de_renda`; renda e safra canonica sao reaplicadas aqui,
    e reaplica-las nao muda nada. ``created_at`` e opcional.

    1. Mesmo valor, data-ex e pagamento dentro das janelas: o mesmo provento
       regravado. Fica a linha gravada por ultimo, que e a informacao mais nova
       da fonte.
    2. Pagamento nulo, ou nao posterior a data-ex, nao e data. A linha sai
       quando ha, no mesmo valor e na janela de data-ex, uma com pagamento real
       -- e se junta a uma so, para nao fundir duas parcelas.

    O resto soma: parcelas da mesma declaracao, declaracoes distintas no mesmo
    dia, tipos distintos no mesmo dia -- e valores diferentes com a mesma
    data-ex e o mesmo pagamento. Ficar com o minimo desse grupo (a regra do
    A-129) parecia matar o eco de classe, mas a resposta mais nova da brapi
    tem 306 grupos assim, todos declaracoes distintas, e o eco ja sai na
    ingestao. Medido em 29/10/2026 contra essa resposta
    (``scripts/medir_proventos_b3.py``): 86,0% dos ticker-anos batem a 1% sem
    o minimo, 83,9% com ele. A medida e circular -- a resposta mais nova e a
    referencia, nao a verdade --; diante da CVM, nos FIIs, as duas variantes
    empatam (69,5% e 69,6% a ate 5%).

    Dois limites do desenho, ambos so no caminho de reserva (o ticker que o
    arquivo publicado de ``core.proventos_b3`` nao cobre):

    * o par decide, mas a uniao e transitiva: 1,0000, 1,0009 e 1,0018 com as
      mesmas datas viram um evento so (1,0018), embora as pontas fiquem a 0,18%;
    * a revisao que muda o valor em mais de 0,1% e tambem o pagamento conta
      duas vezes: 0,50 e depois 0,51 viram dois proventos. ``created_at`` nao
      separa revisao de declaracao nova, que tambem e gravada depois; separar
      exigiria saber se a linha antiga continuou na resposta seguinte, e o
      upsert nao registra isso.
    """
    import numpy as np
    import pandas as pd

    vazio = pd.DataFrame(columns=list(COLUNAS_EVENTO))
    if df is None or len(df) == 0:
        return vazio
    d = descarta_safra_colapsada(apenas_renda(df)).copy()
    for c in ("ex_date", "payment_date", "event_date"):
        d[c] = pd.to_datetime(d[c], errors="coerce") if c in d.columns else pd.NaT
    d["event_date"] = d["event_date"].fillna(d["payment_date"]).fillna(d["ex_date"])
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    d = d[d["amount"].gt(0) & d["event_date"].notna()]
    if d.empty:
        return vazio
    d["ticker"] = d["ticker"].astype(str).str.strip().str.upper()
    if "type" not in d.columns:
        d["type"] = None
    d["_tipo"] = d["type"].fillna("").astype(str).str.strip().str.upper()
    d["_ex"] = d["ex_date"].fillna(d["event_date"])
    criado = (pd.to_datetime(d["created_at"], errors="coerce", utc=True)
              if "created_at" in d.columns
              else pd.Series(pd.NaT, index=d.index, dtype="datetime64[ns, UTC]"))
    d["_criado"] = criado.dt.tz_localize(None)

    # Faixas de valor: vizinhos a ate 0,1% um do outro, dentro de (ticker, tipo).
    # A faixa so separa quem nunca pode ser o mesmo provento: 1,0000 -> 1,0009
    # -> 1,0018 cabe numa faixa so, e as pontas ficam a 0,18%. Quem decide e o
    # par, com o proprio valor (`_mesmo_valor`).
    d = d.sort_values(["ticker", "_tipo", "amount"], kind="mergesort").reset_index(drop=True)
    salto = d["amount"] / d["amount"].shift() - 1 > TOLERANCIA_VALOR
    d["_faixa"] = ((d["ticker"] != d["ticker"].shift()) | (d["_tipo"] != d["_tipo"].shift())
                   | salto).cumsum()
    valido = (d["payment_date"].notna() & (d["payment_date"] > d["_ex"])).to_numpy()
    ex = d["_ex"].to_numpy("datetime64[D]").astype("int64")
    pag = d["payment_date"].to_numpy("datetime64[D]").astype("int64")
    valor = d["amount"].to_numpy("float64")

    def _mesmo_valor(a, b):
        # a regra B de `core.brapi.dedup_cash_dividends`
        return abs(valor[a] - valor[b]) <= TOLERANCIA_VALOR * max(valor[a], valor[b])

    raiz = np.arange(len(d))

    def acha(i):
        while raiz[i] != i:
            raiz[i] = raiz[raiz[i]]
            i = raiz[i]
        return i

    def une(i, j):
        raiz[acha(i)] = acha(j)

    for idx in d.groupby("_faixa").indices.values():
        if len(idx) < 2:
            continue
        reais = [i for i in idx if valido[i]]
        # 1. Revisao: valor, data-ex e pagamento dentro das janelas.
        for a_pos, a in enumerate(reais):
            for b in reais[a_pos + 1:]:
                if (abs(ex[a] - ex[b]) <= JANELA_EX_DIAS
                        and abs(pag[a] - pag[b]) <= JANELA_PAGAMENTO_DIAS
                        and _mesmo_valor(a, b)):
                    une(a, b)
        # 2. Sem pagamento real: junta a UMA linha real na janela de data-ex,
        # a mais proxima. Sem nenhuma, as sem data se juntam entre si.
        sem_data = [i for i in idx if not valido[i]]
        orfas = []
        for a in sem_data:
            perto = [b for b in reais
                     if abs(ex[a] - ex[b]) <= JANELA_EX_DIAS and _mesmo_valor(a, b)]
            if perto:
                une(a, min(perto, key=lambda b: abs(ex[a] - ex[b])))
                continue
            for b in orfas:
                if abs(ex[a] - ex[b]) <= JANELA_EX_DIAS and _mesmo_valor(a, b):
                    une(a, b)
            orfas.append(a)

    d["_grupo"] = [acha(i) for i in range(len(d))]
    d["_valido"] = valido
    d = (d.sort_values(["_grupo", "_valido", "_criado", "payment_date"],
                       na_position="first", kind="mergesort")
          .drop_duplicates("_grupo", keep="last"))
    return (d[list(COLUNAS_EVENTO)].sort_values(["ticker", "event_date", "type"])
             .reset_index(drop=True))


def soma_por_ano(eventos, *, data: str = "event_date"):
    """``ticker, ano, total``: a soma por acao de eventos ja deduplicados,
    com o ano tirado da coluna ``data``; linha sem essa data fica de fora."""
    import pandas as pd

    datas = pd.to_datetime(eventos[data], errors="coerce")
    ev = eventos[datas.notna()]
    if ev.empty:
        return pd.DataFrame(columns=["ticker", "ano", "total"])
    out = (ev.assign(ano=datas[datas.notna()].dt.year.astype(int))
             .groupby(["ticker", "ano"], as_index=False)["amount"].sum()
             .rename(columns={"amount": "total"}))
    return out.sort_values(["ticker", "ano"]).reset_index(drop=True)


def renda_por_ano(df, *, data: str = "event_date"):
    """``ticker, ano, total`` dos eventos de :func:`renda_por_evento`."""
    return soma_por_ano(renda_por_evento(df), data=data)

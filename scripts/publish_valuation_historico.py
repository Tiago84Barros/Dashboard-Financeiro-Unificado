"""Publica o histórico de valuation em ``data/public/valuation_historico.json.gz``.

Lê o armazém local (Docker ``dfu_warehouse``), só leitura, e grava o que a
camada de valuation da Inteligência dos Ativos precisa e o app publicado não
alcança: múltiplos históricos recalculados com preço efetivamente negociado,
porte e volatilidade de cada ação (atributos da seleção de pares) e o
universo de ações americanas com a classificação SIC.

Por que recalcular em vez de ler ``market.calculated_metrics`` anual: o P/L e
o P/VP gravados lá dividem o preço RETROAJUSTADO por split e proventos pelo
lucro por ação PUBLICADO (sem ajuste). Em 26/09/2026: WEGE3 2014 saía com
P/L 4,97 contra 25,85 com o preço da fita da B3; PETR4 2012, 2,8 contra 12,0.
Quanto mais antigo o ano, maior o erro, e todo o histórico parecia "abaixo
da média". Aqui cada ano usa o fechamento da fita oficial
(``market.b3_security_history``, sem ajuste) contra o LPA do mesmo exercício
-- os dois na escala da época.

Métodos (repetidos no campo ``metodo`` do arquivo):

- B3, anual: P/L = fechamento do último pregão do ano ÷ LPA; valor de mercado
  = fechamento × ações implícitas (lucro ÷ LPA); P/VP = valor de mercado ÷
  patrimônio; DY = proventos com data-ex no ano ÷ fechamento. P/L que foge
  mais de 20x da mediana do próprio ticker é descartado (LPA gravado em escala
  errada: ITUB4 2019 tinha LPA 2.780); o mesmo teste sobre o valor de mercado
  implícito vale para o P/VP. O P/L não depende do lucro absoluto -- bancos
  não o têm na base (ITUB4 em todos os anos, BBAS3 antes de 2020).
- FII, mensal: P/VP = fechamento do mês na fita da B3 ÷ VPA; DY 12m =
  rendimentos com data-ex nos 12 meses ÷ fechamento. Mês com VPA que salta e
  volta é descartado; janela de DY que atravessa grupamento/desdobramento
  (preço muda mais de 3x de um mês para o outro) também.
- EUA, anual: valor de mercado no fim do exercício
  (``market_us.market_cap_history``, preço × ações do ponto-no-tempo) ÷ lucro,
  ÷ patrimônio e dividendos pagos ÷ valor de mercado. O preço de
  ``prices_monthly.close`` já vem ajustado por split e o LPA não, então P/L
  por ação misturaria escalas; e as ações do ponto-no-tempo chegam atrasadas
  depois de um split e não são retroajustadas (AAPL até set/2020 saía com 1/4
  do valor). Mês cujo valor ÷ preço foge mais de 1,5x da mediana móvel de 13
  meses é descartado, e do degrau que sobra só vale o trecho posterior.

Momento de preço (desde 01/10/2026): retorno de 12 e de 3 meses com preço
ajustado por proventos e eventos (retorno total), do fechamento mensal mais
recente contra o de 12 (3) meses antes. Para B3 e FII o preço vem do
Supabase (``market.historical_prices``), que a rotina diária mantém em dia; o
espelho local fica semanas para trás na maioria dos tickers, e o momento de
julho lido em outubro diria o contrário do pregão. Sem Supabase, cai no
local e só publica o que tiver preço de até ``MOMENTO_MAX_IDADE_DIAS``. Mês
com salto de mais de 3x contra o anterior na janela (ajuste de split que
não chegou) descarta o ticker.

Alavancagem da B3 (desde 01/10/2026): dívida líquida ÷ EBITDA e cobertura de
juros saem do último ``quote`` da brapi com o módulo ``financialData``
(EBITDA dos 12 meses, dívida e caixa totais da mesma foto) e do último
demonstrativo anual do mesmo payload (EBIT ÷ despesas financeiras). A base
contábil do armazém não tem EBITDA, e sem ele o critério eliminatório de
endividamento nunca disparava.

Ticker renomeado (desde 08/10/2026): a fita da B3 troca de código na
renomeação (ELET3 → AXIA3, EMBR3 → EMBJ3) e o universo não. Os pregões de
cada código da cadeia valem para todos os da mesma cadeia presentes no
universo; a cadeia sai de ``data_pipeline.market.b3_sucessao`` (mesmo código
CVM, mesma classe, um código para de negociar dias antes de o outro começar).

Recusa publicar (saída 1) se a fita da B3 estiver parada há mais de
``FITA_MAXIMA_DIAS``.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO_PADRAO = ROOT / "data" / "public" / "valuation_historico.json.gz"
VERSAO = 1
ANO_INICIAL = 2010          # a fita da B3 no armazém começa em 2010
FITA_MAXIMA_DIAS = 45       # a fita chega por arquivo mensal da B3
FATOR_MC_IMPLAUSIVEL = 20.0
JANELA_VOL_MESES = 36
MOMENTO_MAX_IDADE_DIAS = 45     # preço mais velho que isso não mede momento
MOMENTO_SALTO = 3.0             # mês que muda mais que 3x: ajuste faltando
ALAVANCAGEM_MAX_IDADE_DIAS = 200

METODO = {
    "b3": ("Anual. P/L = fechamento do último pregão do ano na fita da B3 "
           "(sem retroajuste) ÷ LPA publicado do exercício; valor de mercado "
           "= fechamento × (lucro ÷ LPA); P/VP = valor de mercado ÷ "
           "patrimônio líquido; DY = proventos com data-ex no ano ÷ "
           "fechamento. P/L fora de 1/20x a 20x da mediana do ticker é "
           "descartado; o mesmo teste sobre o valor de mercado implícito "
           "vale para o P/VP."),
    "fii": ("Mensal. P/VP = fechamento do último pregão do mês na fita da B3 "
            "÷ VPA do informe mensal; DY 12m = rendimentos com data-ex nos "
            "12 meses ÷ fechamento. Descartados: VPA que salta e volta e "
            "janelas que atravessam grupamento ou desdobramento."),
    "eua": ("Anual. Valor de mercado no fim do exercício (preço × ações do "
            "ponto-no-tempo) ÷ lucro líquido (P/L), ÷ patrimônio (P/VP); "
            "dividendos pagos ÷ valor de mercado (DY). Meses em que valor ÷ "
            "preço foge mais de 1,5x da mediana móvel de 13 meses são "
            "descartados, e também tudo antes do último degrau dessa razão "
            "(ações do ponto-no-tempo não retroajustadas por split)."),
    "volatilidade": (f"Desvio-padrão dos retornos mensais dos últimos "
                     f"{JANELA_VOL_MESES} meses (preço ajustado), anualizado "
                     "(× √12), em %."),
    "momento": ("Retorno total (preço ajustado por proventos e eventos) de 12 "
                "e de 3 meses: último fechamento mensal ÷ o de 12 (3) meses "
                "antes − 1, em %. B3 e FII com preço do Supabase "
                "(market.historical_prices); EUA com market_us.prices_monthly. "
                f"Só com preço de até {MOMENTO_MAX_IDADE_DIAS} dias; ticker "
                f"com mês que salta mais de {MOMENTO_SALTO:.0f}x na janela é "
                "descartado."),
    "alavancagem": ("B3. Dívida líquida ÷ EBITDA = (dívida total − caixa) ÷ "
                    "EBITDA dos 12 meses, os três da mesma foto do "
                    "financialData da brapi; cobertura de juros = EBIT ÷ "
                    "despesas financeiras do último exercício anual do mesmo "
                    "payload. EBITDA é o do provedor, não o ajustado que a "
                    "empresa divulga; despesa financeira inclui variação "
                    "cambial e monetária, então a cobertura sai conservadora "
                    "em empresa com dívida em moeda forte."),
}


def _r(v, casas: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, casas)


def _serie(pares) -> list:
    return [[k, _r(v)] for k, v in pares if _r(v) is not None]


def _ler(conn, sql: str, **params):
    import pandas as pd
    from sqlalchemy import text
    return pd.read_sql(text(sql), conn, params=params)


# -- volatilidade -------------------------------------------------------------------

def volatilidade(precos, *, janela: int = JANELA_VOL_MESES) -> dict[str, float]:
    """``precos``: ticker, mes, preco (último do mês). → {ticker: vol % a.a.}"""
    import numpy as np
    saida = {}
    for tk, g in precos.sort_values("mes").groupby("ticker"):
        p = g["preco"].astype(float)
        p = p[p > 0]
        if len(p) < janela + 1:
            continue
        r = np.log(p.iloc[-(janela + 1):].to_numpy())
        r = np.diff(r)
        saida[str(tk)] = float(np.std(r, ddof=1) * math.sqrt(12) * 100)
    return saida


# -- momento ------------------------------------------------------------------------

def momento(precos, hoje, *, max_idade: int = MOMENTO_MAX_IDADE_DIAS
            ) -> tuple[dict[str, dict], dict]:
    """``precos``: ticker, mes (1º dia), data (do último preço do mês), preco
    (ajustado). → ({ticker: {retorno_12m, retorno_3m, momento_ref}},
    {motivo: contagem}). Puro."""
    import pandas as pd
    excl = {"preco_velho": 0, "salto_na_janela": 0, "historico_curto": 0}
    saida: dict[str, dict] = {}
    hoje = pd.Timestamp(hoje)
    hoje = (hoje.tz_localize(None) if hoje.tzinfo is None
            else hoje.tz_convert(None)).normalize()
    for tk, g in precos.sort_values("mes").groupby("ticker"):
        g = g[g["preco"].astype(float) > 0].copy()
        if g.empty:
            continue
        g["_p"] = pd.to_datetime(g["mes"], utc=True).dt.tz_convert(None) \
            .dt.to_period("M")
        g = g.drop_duplicates("_p", keep="last")
        ult = g.iloc[-1]
        if (hoje - pd.Timestamp(str(ult["data"])[:10])).days > max_idade:
            excl["preco_velho"] += 1
            continue
        serie = g.set_index("_p")["preco"].astype(float)
        fim = serie.index[-1]
        ini12 = fim - 12
        if ini12 not in serie.index:
            excl["historico_curto"] += 1
            continue
        janela = serie[serie.index >= ini12]
        razao = (janela / janela.shift(1)).dropna()
        if ((razao > MOMENTO_SALTO) | (razao < 1 / MOMENTO_SALTO)).any():
            excl["salto_na_janela"] += 1
            continue
        item = {"retorno_12m": _r(100.0 * (serie[fim] / serie[ini12] - 1), 2)}
        if (fim - 3) in serie.index:
            item["retorno_3m"] = _r(100.0 * (serie[fim] / serie[fim - 3] - 1), 2)
        base = g[g["_p"] == ini12].iloc[-1]
        item["momento_ref"] = (f"{str(base['data'])[:10]} a "
                               f"{str(ult['data'])[:10]}")
        saida[str(tk)] = item
    return saida, excl


_SQL_PRECOS_MENSAIS = """
    SELECT DISTINCT ON (ticker, date_trunc('month', date))
           ticker, date_trunc('month', date) AS mes, date AS data,
           COALESCE(adjusted_close, close) AS preco
      FROM market.historical_prices
     WHERE date >= now() - interval '40 months'
     ORDER BY ticker, date_trunc('month', date), date DESC
"""


def precos_mensais_supabase():
    """Último preço ajustado de cada mês, 40 meses, do Supabase. ``None`` se
    a conexão falhar (o chamador cai no espelho local e o relatório diz)."""
    try:
        from sqlalchemy import text

        from core.database import get_engine
        with get_engine().connect() as c:
            c.execute(text("SET TRANSACTION READ ONLY"))
            df = _ler(c, _SQL_PRECOS_MENSAIS)
        df["preco"] = df["preco"].astype(float)
        return df
    except Exception as exc:  # noqa: BLE001 -- fonte opcional, nomeada no relatório
        print(f"Supabase indisponível para preços: {type(exc).__name__}: {exc}")
        return None


# -- alavancagem B3 -----------------------------------------------------------------

def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def alavancagem_brapi(linhas, hoje, *, max_idade: int = ALAVANCAGEM_MAX_IDADE_DIAS
                      ) -> tuple[dict[str, dict], dict]:
    """``linhas``: dicts com ticker, fetched_at, financial_data (dict) e
    anuais (lista do incomeStatementHistory). Uma linha por ticker, a mais
    recente com financialData. Puro.

    EBITDA ≤ 0 não dá razão (dívida ÷ número negativo inverte o sentido);
    vai como ``ebitda_negativo`` para o leitor dizer isso em vez de calar.
    """
    excl = {"foto_velha": 0, "sem_ebitda": 0}
    saida: dict[str, dict] = {}
    hoje_d = hoje.date() if hasattr(hoje, "date") else hoje
    for r in linhas:
        tk = str(r["ticker"]).upper()
        quando = r["fetched_at"]
        if (hoje_d - quando.date()).days > max_idade:
            excl["foto_velha"] += 1
            continue
        fd = r.get("financial_data") or {}
        ebitda, divida, caixa = (_f(fd.get("ebitda")), _f(fd.get("totalDebt")),
                                 _f(fd.get("totalCash")))
        item: dict = {"alavancagem_ref": f"brapi financialData de "
                                         f"{quando.date().isoformat()}"}
        if ebitda is None or divida is None or caixa is None:
            excl["sem_ebitda"] += 1
        elif ebitda <= 0:
            item["ebitda_negativo"] = True
        else:
            item["divida_liquida_ebitda"] = _r((divida - caixa) / ebitda, 2)
            item["ebitda_12m"] = _r(ebitda, 0)
        anuais = [a for a in (r.get("anuais") or [])
                  if isinstance(a, dict) and a.get("type") == "yearly"
                  and a.get("endDate")]
        if anuais:
            a = max(anuais, key=lambda x: x["endDate"])
            ebit, desp = _f(a.get("ebit")), _f(a.get("financialExpenses"))
            if ebit is not None and desp is not None and desp < 0:
                item["cobertura_juros"] = _r(ebit / abs(desp), 2)
                item["cobertura_ref"] = (f"exercício encerrado em "
                                         f"{str(a['endDate'])[:10]}")
        if len(item) > 1:
            saida[tk] = item
    return saida, excl


def coletar_alavancagem(conn, hoje) -> tuple[dict, dict]:
    from sqlalchemy import text
    linhas = [dict(r._mapping) for r in conn.execute(text("""
        SELECT DISTINCT ON (ticker) ticker, fetched_at,
               payload_json->'financialData' AS financial_data,
               payload_json->'incomeStatementHistory' AS anuais
          FROM market.brapi_raw_payloads
         WHERE endpoint = 'quote' AND request_status = 'success'
           AND payload_json ? 'financialData'
         ORDER BY ticker, fetched_at DESC
    """))]
    return alavancagem_brapi(linhas, hoje)


# -- B3 -----------------------------------------------------------------------------

def historico_b3(fechamentos, demos, proventos) -> tuple[dict, dict]:
    """Séries anuais por ticker. Puro sobre DataFrames.

    ``fechamentos``: ticker, ano, close (fita, sem ajuste).
    ``demos``: ticker, ano, lucro, lpa, patrimonio.
    ``proventos``: ticker, ano, total (soma por ação, data-ex no ano).
    Devolve ({ticker: {"historico": {...}, "acoes": float, "ano_acoes": int}},
    {motivo: contagem}).
    """
    import pandas as pd
    df = (fechamentos.merge(demos, on=["ticker", "ano"], how="inner")
          .merge(proventos, on=["ticker", "ano"], how="left"))
    exclusoes = {"lpa_implausivel": 0, "acoes_implausiveis": 0}
    saida: dict[str, dict] = {}

    def _fora(serie):
        med = serie.median()
        if pd.isna(med) or med <= 0:
            return serie.isna() & False
        return ((serie / med > FATOR_MC_IMPLAUSIVEL)
                | (serie / med < 1 / FATOR_MC_IMPLAUSIVEL))

    for tk, g in df.sort_values("ano").groupby("ticker"):
        g = g.copy()
        # P/L só precisa de preço e LPA, os dois na escala da época; bancos
        # não têm lucro absoluto na base e ficam só com este caminho. A
        # guarda é sobre o próprio múltiplo, que não muda com desdobramento.
        g["pl"] = (g["close"] / g["lpa"]).where((g["lpa"] > 0) & (g["close"] > 0))
        fora_pl = _fora(g["pl"])
        exclusoes["lpa_implausivel"] += int(fora_pl.sum())
        g.loc[fora_pl, "pl"] = None
        valido = (g["lpa"].abs() > 0) & g["lucro"].notna() & (g["close"] > 0)
        g["acoes"] = (g["lucro"] / g["lpa"]).where(valido)
        g.loc[g["acoes"] <= 0, "acoes"] = None
        g["mc"] = g["close"] * g["acoes"]
        fora = _fora(g["mc"])
        exclusoes["acoes_implausiveis"] += int(fora.sum())
        g.loc[fora, ["acoes", "mc"]] = None
        pl = [(int(r.ano), r.pl) for r in g.itertuples() if pd.notna(r.pl)]
        pvp = [(int(r.ano), r.mc / r.patrimonio) for r in g.itertuples()
               if pd.notna(r.mc) and pd.notna(r.patrimonio) and r.patrimonio > 0]
        dy = [(int(r.ano), 100.0 * r.total / r.close) for r in g.itertuples()
              if pd.notna(r.total) and r.total > 0 and r.close > 0]
        com_acoes = g.dropna(subset=["acoes"])
        item = {"historico": {"p_l": _serie(pl), "p_vp": _serie(pvp),
                              "dividend_yield": _serie(dy)}}
        if not com_acoes.empty:
            ult = com_acoes.iloc[-1]
            item["acoes"] = float(ult["acoes"])
            item["ano_acoes"] = int(ult["ano"])
        saida[str(tk)] = item
    return saida, exclusoes


def fita_pelo_universo(df, sucessoes: dict, universo, chave: list[str]):
    """Leva o pregão do código novo ao ticker do universo, e vice-versa.

    A fita registra ELET3 até 07/11/2025 e AXIA3 dali em diante; o universo
    (demonstrações, ``public.setores``) segue com ELET3. Sem isso, ELET3 fica
    sem fechamento de 2026 e sem preço recente, e AXIA3 sem o histórico de
    antes da troca. Depois de copiar, fica o pregão mais recente por
    ``chave``. Puro sobre DataFrame com ``ticker`` e ``trade_date``.
    """
    from data_pipeline.market.b3_sucessao import expandir_fita
    df = expandir_fita(df, sucessoes, universo)
    return (df.sort_values("trade_date", kind="stable")
            .drop_duplicates(chave, keep="last").reset_index(drop=True))


def _sucessoes(conn) -> dict:
    from data_pipeline.market.b3_sucessao import carregar_sucessoes
    try:
        return carregar_sucessoes(conn)
    except Exception as exc:  # sem a tabela, segue sem sucessão, e diz
        print(f"sucessão de tickers indisponível: {type(exc).__name__}: {exc}")
        return {}


def coletar_b3(conn, precos_supabase=None, hoje=None, sucessoes=None
               ) -> tuple[dict, dict, str | None]:
    fech = _ler(conn, """
        SELECT DISTINCT ON (ticker, EXTRACT(YEAR FROM trade_date))
               ticker, EXTRACT(YEAR FROM trade_date)::int AS ano, trade_date,
               COALESCE(close_unitario, close / NULLIF(fator_cotacao, 0)) AS close
          FROM market.b3_security_history
         WHERE close > 0 AND trade_date >= make_date(:ini, 1, 1)
         ORDER BY ticker, EXTRACT(YEAR FROM trade_date), trade_date DESC,
                  collected_at DESC
    """, ini=ANO_INICIAL)
    ultimo = _ler(conn, """
        SELECT DISTINCT ON (ticker) ticker, trade_date,
               COALESCE(close_unitario, close / NULLIF(fator_cotacao, 0)) AS close
          FROM market.b3_security_history
         WHERE close > 0 AND trade_date >= now() - interval '120 days'
         ORDER BY ticker, trade_date DESC, collected_at DESC
    """)
    demos = _ler(conn, """
        SELECT DISTINCT ON (i.ticker, i.year) i.ticker, i.year AS ano,
               i.net_income AS lucro, i.eps AS lpa, b.equity AS patrimonio
          FROM market.income_statements i
          LEFT JOIN market.balance_sheets b
            ON b.ticker = i.ticker AND b.year = i.year AND b.period = 'annual'
         WHERE i.period = 'annual' AND i.year >= :ini
           AND i.year < EXTRACT(YEAR FROM now())::int
         ORDER BY i.ticker, i.year, i.updated_at DESC
    """, ini=ANO_INICIAL)
    prov = _ler(conn, """
        SELECT ticker, EXTRACT(YEAR FROM ex_date)::int AS ano, SUM(amount) AS total
          FROM (SELECT DISTINCT ticker, ex_date, amount FROM market.dividends
                 WHERE ex_date IS NOT NULL AND amount > 0) d
         GROUP BY 1, 2
    """)
    # Ticker renomeado: a fita troca de código, o universo não.
    sucessoes = _sucessoes(conn) if sucessoes is None else sucessoes
    universo = set(demos["ticker"].astype(str))
    fech = fita_pelo_universo(fech, sucessoes, universo, ["ticker", "ano"])
    ultimo = fita_pelo_universo(ultimo, sucessoes, universo, ["ticker"])
    precos = (precos_supabase if precos_supabase is not None
              else _ler(conn, _SQL_PRECOS_MENSAIS))
    for c in ("close", "lucro", "lpa", "patrimonio", "total"):
        for df in (fech, demos, prov):
            if c in df:
                df[c] = df[c].astype(float)
    hist, exclusoes = historico_b3(fech, demos, prov)
    exclusoes["sucessoes_de_ticker"] = dict(sorted(sucessoes.items()))
    hoje = hoje or datetime.now(timezone.utc)
    precos["preco"] = precos["preco"].astype(float)
    vol = volatilidade(precos)
    mom, excl_mom = momento(precos, hoje)
    exclusoes["momento"] = excl_mom
    alav, excl_alav = coletar_alavancagem(conn, hoje)
    exclusoes["alavancagem"] = excl_alav
    px = {str(r.ticker): (float(r.close), str(r.trade_date)[:10])
          for r in ultimo.itertuples()}
    for tk, item in hist.items():
        acoes = item.pop("acoes", None)
        ano = item.pop("ano_acoes", None)
        if acoes and tk in px:
            item["porte"] = _r(px[tk][0] * acoes, 0)
            item["porte_ref"] = (f"fechamento de {px[tk][1]} × ações implícitas "
                                 f"do exercício {ano}")
        if tk in vol:
            item["volatilidade"] = _r(vol[tk], 2)
        item.update(mom.get(tk, {}))
        item.update(alav.get(tk, {}))
    fita = str(ultimo["trade_date"].max())[:10] if not ultimo.empty else None
    return hist, exclusoes, fita


# -- FII ----------------------------------------------------------------------------

def historico_fii(vpa, fechamentos, proventos) -> tuple[dict, dict]:
    """Séries mensais por fundo. Puro sobre DataFrames.

    ``vpa``: ticker, mes (Timestamp 1º dia), vpa.
    ``fechamentos``: ticker, mes, close (fita, sem ajuste).
    ``proventos``: ticker, ex_date, amount.
    """
    import pandas as pd
    exclusoes = {"vpa_salta_e_volta": 0, "janela_com_grupamento": 0}
    saida: dict[str, dict] = {}
    fech_por = {tk: g.set_index("mes")["close"].sort_index()
                for tk, g in fechamentos.groupby("ticker")}
    prov_por = {tk: g for tk, g in proventos.groupby("ticker")}
    for tk, g in vpa.sort_values("mes").groupby("ticker"):
        v = g.set_index("mes")["vpa"].astype(float)
        v = v[v > 0]
        viz = v.rolling(5, center=True, min_periods=3).median()
        salto = (v / viz > 1.5) | (v / viz < 1 / 1.5)
        exclusoes["vpa_salta_e_volta"] += int(salto.sum())
        v = v[~salto]
        px = fech_por.get(tk)
        if px is None or px.empty:
            continue
        pvp = [(m.strftime("%Y-%m"), px[m] / v[m]) for m in v.index if m in px.index]
        # DY 12m: rendimentos com data-ex nos 12 meses até o fim do mês.
        dy = []
        pv = prov_por.get(tk)
        razao = px / px.shift(1)
        quebra = razao[(razao > 3) | (razao < 1 / 3)].index
        if pv is not None and not pv.empty:
            datas = pd.to_datetime(pv["ex_date"])
            valores = pv["amount"].astype(float).to_numpy()
            for m, fecha in px.items():
                if m < px.index[0] + pd.DateOffset(months=11):
                    continue
                ini = m - pd.DateOffset(months=11)
                if any(ini < q <= m for q in quebra):
                    exclusoes["janela_com_grupamento"] += 1
                    continue
                fim = m + pd.offsets.MonthEnd(0)
                soma = float(valores[((datas >= ini) & (datas <= fim)).to_numpy()].sum())
                d = 100.0 * soma / fecha if fecha > 0 else None
                if d is not None and 0 < d <= 30:
                    dy.append((m.strftime("%Y-%m"), d))
        saida[str(tk)] = {"historico": {"p_vp": _serie(pvp),
                                        "dividend_yield": _serie(dy)}}
    return saida, exclusoes


def coletar_fii(conn, precos=None, hoje=None) -> tuple[dict, dict]:
    import pandas as pd
    vpa = _ler(conn, """
        SELECT ticker, ref_month AS mes, vpa FROM market.fii_metrics_monthly
         WHERE vpa > 0
    """)
    fech = _ler(conn, """
        SELECT DISTINCT ON (ticker, date_trunc('month', trade_date))
               ticker, date_trunc('month', trade_date) AS mes, close
          FROM market.fii_b3_security_history
         WHERE close > 0 AND trade_date >= make_date(2022, 1, 1)
         ORDER BY ticker, date_trunc('month', trade_date), trade_date DESC,
                  collected_at DESC, id DESC
    """)
    prov = _ler(conn, """
        SELECT DISTINCT ticker, ex_date, amount FROM market.dividends
         WHERE ex_date >= make_date(2022, 1, 1) AND amount > 0
           AND ticker IN (SELECT DISTINCT ticker FROM market.fii_metrics_monthly)
    """)
    for df, c in ((vpa, "mes"), (fech, "mes")):
        df[c] = pd.to_datetime(df[c]).dt.tz_localize(None)
    fech["close"] = fech["close"].astype(float)
    hist, excl = historico_fii(vpa, fech, prov)
    if precos is None:
        precos = _ler(conn, _SQL_PRECOS_MENSAIS)
    precos = precos[precos["ticker"].isin(set(vpa["ticker"]))].copy()
    precos["preco"] = precos["preco"].astype(float)
    mom, excl["momento"] = momento(precos, hoje or datetime.now(timezone.utc))
    for tk, item in hist.items():
        item.update(mom.get(tk, {}))
    return hist, excl


# -- EUA ----------------------------------------------------------------------------

def _pct(v):
    f = _r(v, 8)
    return None if f is None else f * 100.0


def capitalizacao_limpa(capitalizacao, precos) -> tuple:
    """Descarta meses em que o valor de mercado não acompanha o preço.

    ``market_cap_history`` multiplica o preço (ajustado por split) pelas
    ações do ponto-no-tempo, que chegam com atraso depois de um
    desdobramento: AAPL de jun a set/2020 saía com 1/4 do valor (4:1 em
    ago/2020). Ações implícitas = valor ÷ preço mudam devagar (recompra,
    emissão); mês que foge mais de 1,5x da mediana móvel de 13 meses é
    descartado. ``precos``: symbol, mes, close.
    """
    import numpy as np
    import pandas as pd
    c = capitalizacao.dropna(subset=["date", "market_cap"]).copy()
    c["date"] = pd.to_datetime(c["date"])
    c["mes"] = c["date"].dt.to_period("M")
    p = precos.dropna(subset=["close"]).copy()
    p["mes"] = pd.to_datetime(p["mes"]).dt.to_period("M")
    c = c.merge(p[["symbol", "mes", "close"]], on=["symbol", "mes"], how="left")
    c = c.sort_values(["symbol", "date"])
    c["acoes"] = c["market_cap"] / c["close"]
    c["ref"] = (c.groupby("symbol")["acoes"]
                .transform(lambda s: s.rolling(13, center=True, min_periods=5)
                           .median()))
    razao = (c["acoes"] / c["ref"]).astype(float)
    fora = razao.notna() & (np.abs(np.log(razao.where(razao > 0))) > math.log(1.5))
    c = c.loc[~fora].copy()
    # Depois de tirar os meses isolados, sobra o degrau: as ações do
    # ponto-no-tempo NÃO são retroajustadas, então todo mês anterior ao split
    # multiplica o preço já ajustado pela contagem velha (AAPL até set/2020
    # inteira em 1/4). Fica só o trecho depois do último degrau.
    salto = (c.groupby("symbol")["acoes"].transform(lambda s: s / s.shift(1))
             .astype(float))
    degrau = salto.notna() & (np.abs(np.log(salto.where(salto > 0))) > math.log(1.5))
    c["_bloco"] = degrau.astype(int).groupby(c["symbol"]).cumsum()
    ultimo = c.groupby("symbol")["_bloco"].transform("max")
    antes = c["_bloco"] < ultimo
    c = c.loc[~antes]
    return (c[["symbol", "date", "market_cap"]].reset_index(drop=True),
            int(fora.sum()) + int(antes.sum()))


def historico_eua(anuais, capitalizacao) -> dict[str, dict]:
    """``anuais``: symbol, fiscal_year, reference_date, net_income, equity,
    dividends_paid. ``capitalizacao``: symbol, date, market_cap (já limpa)."""
    import pandas as pd
    a = anuais.dropna(subset=["reference_date"]).copy()
    a["reference_date"] = pd.to_datetime(a["reference_date"])
    c = capitalizacao.dropna(subset=["date", "market_cap"]).copy()
    c["date"] = pd.to_datetime(c["date"])
    a = a.sort_values("reference_date")
    c = c.sort_values("date")
    m = pd.merge_asof(a, c, left_on="reference_date", right_on="date",
                      by="symbol", direction="backward",
                      tolerance=pd.Timedelta(days=45))
    saida: dict[str, dict] = {}
    for sym, g in m.dropna(subset=["market_cap"]).groupby("symbol"):
        g = g.sort_values("fiscal_year")
        pl = [(int(r.fiscal_year), r.market_cap / r.net_income)
              for r in g.itertuples() if pd.notna(r.net_income) and r.net_income > 0]
        pvp = [(int(r.fiscal_year), r.market_cap / r.equity)
               for r in g.itertuples() if pd.notna(r.equity) and r.equity > 0]
        dy = [(int(r.fiscal_year), 100.0 * abs(r.dividends_paid) / r.market_cap)
              for r in g.itertuples()
              if pd.notna(r.dividends_paid) and r.dividends_paid != 0]
        saida[str(sym)] = {"p_l": _serie(pl), "p_vp": _serie(pvp),
                           "dividend_yield": _serie(dy)}
    return saida


def coletar_eua(conn) -> tuple[dict, dict]:
    snap = _ler(conn, """
        SELECT s.symbol, s.name, s.cik, s.metrics, s.last_fiscal_year,
               e.sic, e.sic_descricao
          FROM market_us.company_snapshots s
          LEFT JOIN market_us.sec_entidade e ON e.cik = NULLIF(s.cik, '')::bigint
         WHERE COALESCE(s.is_reit, false) = false
    """)
    anuais = _ler(conn, """
        SELECT DISTINCT ON (i.symbol, i.fiscal_year)
               i.symbol, i.fiscal_year, i.reference_date, i.net_income,
               b.total_equity AS equity, c.dividends_paid
          FROM market_us.income_statements i
          LEFT JOIN market_us.balance_sheets b
            ON b.symbol = i.symbol AND b.fiscal_year = i.fiscal_year
           AND b.period = 'annual'
          LEFT JOIN market_us.cash_flow_statements c
            ON c.symbol = i.symbol AND c.fiscal_year = i.fiscal_year
           AND c.period = 'annual'
         WHERE i.period = 'annual' AND i.fiscal_year >= :ini
           AND i.symbol IN (SELECT symbol FROM market_us.company_snapshots)
         ORDER BY i.symbol, i.fiscal_year, i.updated_at DESC
    """, ini=ANO_INICIAL)
    cap = _ler(conn, """
        SELECT symbol, date, market_cap FROM market_us.market_cap_history
         WHERE market_cap > 0 AND date >= make_date(:ini, 1, 1)
           AND symbol IN (SELECT symbol FROM market_us.company_snapshots)
    """, ini=ANO_INICIAL)
    mensais = _ler(conn, """
        SELECT symbol, month_end AS mes, month_end AS data, close,
               adjusted_close
          FROM market_us.prices_monthly
         WHERE month_end >= make_date(:ini, 1, 1) AND close > 0
           AND symbol IN (SELECT symbol FROM market_us.company_snapshots)
    """, ini=ANO_INICIAL)
    for col in ("net_income", "equity", "dividends_paid"):
        anuais[col] = anuais[col].astype(float)
    cap["market_cap"] = cap["market_cap"].astype(float)
    mensais["close"] = mensais["close"].astype(float)
    mensais["adjusted_close"] = mensais["adjusted_close"].astype(float)
    cap, descartados = capitalizacao_limpa(cap, mensais)
    hist = historico_eua(anuais, cap)
    import pandas as pd
    recentes = mensais[pd.to_datetime(mensais["mes"])
                       >= pd.Timestamp.now() - pd.DateOffset(months=40)]
    recentes = recentes.rename(columns={"symbol": "ticker",
                                        "adjusted_close": "preco"})
    vol = volatilidade(recentes)
    mom, excl_mom = momento(recentes.dropna(subset=["preco"]),
                            datetime.now(timezone.utc))
    saida = {}
    for r in snap.itertuples():
        met = r.metrics if isinstance(r.metrics, dict) else json.loads(r.metrics or "{}")
        mc, eq = _r(met.get("_market_cap"), 0), _r(met.get("_equity"), 0)
        pe = _r(met.get("pe"))
        ev_ebit = _r(met.get("ev_ebit"))
        atual = {
            "p_l": pe if pe is not None and pe > 0 else None,
            "p_vp": _r(mc / eq) if mc and eq and eq > 0 else None,
            "dividend_yield": _pct(met.get("dividend_yield")),
            "ev_ebit": ev_ebit if ev_ebit is not None and ev_ebit > 0 else None,
            "roe": _pct(met.get("roe")),
            "margem_liquida": _pct(met.get("net_margin")),
        }
        sic = str(int(r.sic)) if r.sic is not None and str(r.sic) not in ("nan", "None") else None
        saida[str(r.symbol)] = {
            "nome": r.name, "cik": r.cik, "sic": sic,
            "sic_descricao": r.sic_descricao,
            "porte": mc,
            "volatilidade": _r(vol.get(str(r.symbol)), 2),
            **mom.get(str(r.symbol), {}),
            "atual": {k: _r(v) for k, v in atual.items() if _r(v) is not None},
            "atual_ref": (f"exercício {int(r.last_fiscal_year)}"
                          if r.last_fiscal_year is not None
                          and str(r.last_fiscal_year) != "nan" else None),
            "historico": hist.get(str(r.symbol), {}),
        }
    return saida, {"capitalizacao_fora_do_preco": descartados,
                   "momento": excl_mom}


# -- saída --------------------------------------------------------------------------

def serializar(payload: dict, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(caminho, "wb"),
                       mtime=0) as gz:
        gz.write(bruto)


def desserializar(caminho: Path) -> dict:
    with gzip.open(caminho, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from sqlalchemy import create_engine, text

    from scripts.publish_fii_selection_from_local import _warehouse_url
    try:
        engine = create_engine(_warehouse_url())
        conn = engine.connect()
    except Exception as exc:
        print(f"armazém local indisponível: {type(exc).__name__}: {exc}")
        return 2
    agora = datetime.now(timezone.utc)
    precos = precos_mensais_supabase()
    with conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        b3, excl_b3, fita = coletar_b3(conn, precos, agora)
        fii, excl_fii = coletar_fii(conn, precos, agora)
        eua, excl_eua = coletar_eua(conn)

    relatorio = {
        "b3": len(b3), "fii": len(fii), "eua": len(eua),
        "fita_b3_mais_recente": fita, "exclusoes_b3": excl_b3,
        "precos_b3_fii": ("Supabase" if precos is not None
                          else "armazém local (Supabase indisponível)"),
        "exclusoes_fii": excl_fii, "exclusoes_eua": excl_eua,
    }
    if fita is None or (agora.date() - datetime.fromisoformat(fita).date()).days \
            > FITA_MAXIMA_DIAS:
        relatorio["erro"] = (f"fita da B3 parada há mais de {FITA_MAXIMA_DIAS} "
                             "dias; o porte sairia com preço velho.")
        print(json.dumps(relatorio, ensure_ascii=False, indent=2))
        return 1
    payload = {
        "versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
        "fita_b3": fita, "metodo": METODO,
        "precos_b3_fii": ("supabase" if precos is not None else "local"),
        "exclusoes": {"b3": excl_b3, "fii": excl_fii, "eua": excl_eua},
        "b3": b3, "fii": fii, "eua": eua,
    }
    if not args.dry_run:
        serializar(payload, args.saida)
        relatorio["arquivo"] = str(args.saida)
        relatorio["bytes"] = args.saida.stat().st_size
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

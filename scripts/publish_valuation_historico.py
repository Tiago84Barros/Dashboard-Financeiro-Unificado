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
  do exercício = fechamento × ações implícitas (lucro ÷ LPA); P/VP = esse
  valor ÷ patrimônio; DY = proventos com data-ex no ano ÷ fechamento. P/L que
  foge mais de 20x da mediana do próprio ticker é descartado (LPA gravado em
  escala errada: ITUB4 2019 tinha LPA 2.780); o mesmo teste sobre o valor de
  mercado implícito vale para o P/VP. O P/L não depende do lucro absoluto --
  bancos não o têm na base (ITUB4 em todos os anos, BBAS3 antes de 2020).
- B3, unit: a unit carrega k ações (``data_pipeline.market.acoes``), então
  P/L = fechamento ÷ (k × LPA) e valor de mercado = fechamento × ações
  implícitas ÷ k. k é o de hoje, derivado do quote mais recente, aplicado a
  todos os exercícios. Unit sem k fica sem P/L e sem P/VP (só DY).
- B3, porte (seleção de pares): preço × ações emitidas ÷ k do quote mais
  recente da brapi, de até 30 dias; sem ações emitidas, o ``marketCap`` do
  mesmo quote. Não usa as ações implícitas: ver ``valor_de_mercado_brapi``.
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
  do valor). O degrau da contagem é retroajustado: o valor anterior é
  multiplicado pela razão do split (``market_us.splits``, ou a razão medida
  quando é uma que a bolsa pratica); degrau que não é split (emissão) fica,
  e o de escala do XBRL (1000x) também. Degrau sem split que leva as ações a
  menos da metade é grupamento misturado com emissão: o histórico anterior
  sai (desde 12/11/2026; antes ele ficava e saía P/VP na casa dos milhões).
  Antes de medir os degraus sai o bloco de até 3 meses cujo valor ÷ preço
  destoa dos dois lados (AAPL abr--jun/2014, que escondia o 7:1). Depois,
  mês isolado cujo valor ÷ preço foge mais de 1,5x da mediana móvel de 13
  meses é descartado. Até 08/11/2026 descartava-se tudo antes do último
  degrau, e 2.852 das 4.350 empresas ficavam sem histórico de P/L.

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

Mercado B3 (desde 08/10/2026): P/L mediano ponto-no-tempo, mês a mês desde
2011, das ``UNIVERSO_MERCADO`` ações mais negociadas no ano anterior (uma
classe por empresa). É a régua de "a bolsa está cara ou barata contra a
própria história" que o contexto das LLMs não tinha: o índice não está em
tabela nenhuma, e o P/L de cada ação sozinho não diz nada do mercado. O
fechamento é o da fita no último pregão do mês; o LPA é o do último exercício
já publicado naquele mês (o do ano anterior a partir de abril, quando sai a
DFP). Mediana do lucro ÷ preço, negativos inclusos, e P/L = 1 ÷ mediana: a
mediana resiste a LPA em escala errada, a média ponderada por liquidez não
(saía P/L 0,1 e 331 em meses vizinhos). NÃO é o P/L do Ibovespa, que pondera
por valor de mercado e fica bem abaixo por causa de bancos, Petrobras e Vale.
Desde 28/10/2026 o prejuízo entra de qualquer tamanho (a CSAN3 de 2025 ficava
fora por ser maior que o preço) e o LPA é rebaseado pelo desdobramento ou
grupamento que a B3 marcou na fita depois da DFP (``desdobramentos``): a
brapi dá o LPA na base de ações do exercício e nunca o retroajusta.
Desde 10/11/2026 a unit entra com o LPA do título, k × o LPA por ação (k =
ações por unit, ``acoes_por_titulo_do_ticker``, a mesma regra dos múltiplos
anuais); unit sem k fica fora, em vez de entrar com P/L k vezes maior.

Recusa publicar (saída 1) se a fita da B3 estiver parada há mais de
``FITA_MAXIMA_DIAS``, se o mês mais recente da fita ficar fora da série do
P/L mediano ou se ele tiver mais de ``MERCADO_IMPLAUSIVEIS_MAX`` ações com LPA
em escala errada (``motivo_para_recusar``).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO_PADRAO = ROOT / "data" / "public" / "valuation_historico.json.gz"
VERSAO = 2  # 2: k ações por título na unit (P/L, P/VP e porte)
ANO_INICIAL = 2010          # a fita da B3 no armazém começa em 2010
FITA_MAXIMA_DIAS = 45       # a fita chega por arquivo mensal da B3
FATOR_MC_IMPLAUSIVEL = 20.0
JANELA_VOL_MESES = 36
MOMENTO_MAX_IDADE_DIAS = 45     # preço mais velho que isso não mede momento
MOMENTO_SALTO = 3.0             # mês que muda mais que 3x: ajuste faltando
ALAVANCAGEM_MAX_IDADE_DIAS = 200
PORTE_MAX_IDADE_DIAS = 30       # valor de mercado mais velho que isso não é porte
UNIVERSO_MERCADO = 50           # ações mais negociadas no ano anterior
MERCADO_MIN_VALIDAS = 20        # mês com menos que isso não entra na série
MERCADO_PL_MAXIMO = 200.0       # P/L acima disso: LPA em escala errada
MERCADO_PL_MINIMO = 1.0         # abaixo disso também (ITUB4 2019, LPA 2.780)
MERCADO_IMPLAUSIVEIS_MAX = 10   # no último mês: de 2023 em diante foram 0 a 2
MERCADO_MARCA_DIAS = 30         # depois de um desdobramento aceito, a marca que segue é a dele
MERCADO_SALTO_MINIMO = 2.5      # salto sem fator redondo que ainda é desdobramento

METODO = {
    "b3": ("Anual. P/L = fechamento do último pregão do ano na fita da B3 "
           "(sem retroajuste) ÷ (k × LPA publicado do exercício), com k = "
           "ações que cada título carrega (1 na ação; na unit, derivado do "
           "quote mais recente); valor de mercado do exercício = fechamento "
           "× (lucro ÷ LPA) ÷ k; P/VP = esse valor ÷ patrimônio líquido. "
           "As ações do exercício (lucro ÷ LPA) ficam na base da época "
           "enquanto, × o fator dos desdobramentos e grupamentos posteriores, "
           "não passam de 5x as de hoje; acima disso, só o LPA reapresentado "
           "depois de um desdobramento volta para a época, e o resto fica sem "
           "P/L e sem P/VP. Unit sem k fica sem P/L e sem P/VP; DY = proventos "
           "de renda com data-ex no ano (resposta mais nova da brapi, mais os "
           "anos inteiros que ela não tem, de market.dividends) ÷ fechamento. P/L "
           "fora de 1/20x a 20x da mediana do ticker é "
           "descartado; o mesmo teste sobre o valor de mercado implícito "
           "vale para o P/VP. Porte (tamanho na seleção de pares) = preço × "
           "ações emitidas ÷ k do quote mais recente da brapi (sem ações "
           "emitidas, o marketCap do mesmo quote), de até "
           f"{PORTE_MAX_IDADE_DIAS} dias; sem ele (quote velho), o "
           "ticker fica sem porte e o tamanho não filtra os pares dele."),
    "fii": ("Mensal. P/VP = fechamento do último pregão do mês na fita da B3 "
            "÷ VPA do informe mensal; DY 12m = rendimentos com data-ex nos "
            "12 meses ÷ fechamento. Descartados: VPA que salta e volta e "
            "janelas que atravessam grupamento ou desdobramento."),
    "eua": ("Anual. Valor de mercado no fim do exercício (preço × ações do "
            "ponto-no-tempo) ÷ lucro líquido (P/L), ÷ patrimônio (P/VP); "
            "dividendos pagos ÷ valor de mercado (DY). As ações do "
            "ponto-no-tempo não são retroajustadas por split: o valor "
            "anterior ao primeiro balanço pós-split é multiplicado pela razão "
            "do split (market_us.splits, ou a razão medida quando é uma que a "
            "bolsa pratica); degrau que não é split (emissão, recompra) fica, e o "
            "de escala do XBRL (1000x) também. Degrau sem split que leva as "
            "ações a menos da metade (grupamento misturado com emissão) "
            "descarta o histórico anterior a ele. Bloco de até 3 meses em que "
            "valor ÷ preço destoa dos dois lados sai antes de medir os "
            "degraus; mês em que foge mais de 1,5x da mediana móvel de 13 "
            "meses é descartado, e mês sem preço também."),
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
    "mercado_b3": (f"Mensal. P/L mediano das {UNIVERSO_MERCADO} ações mais "
                   "negociadas da B3 no ano anterior (volume financeiro da "
                   "fita, uma classe por empresa): fechamento do último "
                   "pregão do mês na fita ÷ LPA do último exercício publicado "
                   "(ano anterior a partir de abril, senão o retrasado). "
                   "P/L = 1 ÷ mediana do lucro ÷ preço, prejuízos de "
                   "qualquer tamanho inclusos. Desdobramento ou grupamento "
                   "marcado pela B3 (EB/EG) depois de março do ano seguinte "
                   "ao exercício divide o LPA pelo fator, porque ele vem na "
                   "base de ações da DFP. Na unit, o LPA é o do título "
                   "(k ações por unit × LPA por ação). "
                   f"Fora da conta: sem LPA do exercício, unit sem o número "
                   f"de ações por unit, P/L acima de "
                   f"{MERCADO_PL_MAXIMO:.0f} ou abaixo de "
                   f"{MERCADO_PL_MINIMO:.0f} "
                   "(LPA em escala errada). Mês com menos de "
                   f"{MERCADO_MIN_VALIDAS} ações válidas fica fora. NÃO é o "
                   "P/L do Ibovespa (ponderado por valor de mercado)."),
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


def valor_de_mercado_brapi(linhas, universo, hoje, *, sucessoes=None,
                           max_idade: int = PORTE_MAX_IDADE_DIAS
                           ) -> tuple[dict[str, dict], dict]:
    """Porte de cada ticker do ``universo``: o valor de mercado do quote mais
    recente da brapi. ``linhas``: dicts com ticker, fetched_at, market_cap e
    ``origem`` ("acoes" = preço × ações emitidas ÷ k; senão o ``marketCap``
    da brapi), uma por ticker (``linhas_de_valor_de_mercado``). Puro.

    Por que não fechamento × ações implícitas (lucro ÷ LPA) do último
    exercício: em 29/10/2026, 46 dos 322 tickers que tinham as duas medidas
    divergiam mais de 3x. PRIO3 saía com 0,0002x o valor de mercado, AERI3
    com 987x, e SBSP3 com 0,19x depois do desdobramento. O LPA da base
    mistura escalas, e as ações do exercício não acompanham desdobramento.
    Sem valor de mercado recente, o ticker fica sem porte, e a seleção de
    pares diz que o tamanho não filtrou. Uma estimativa que erra 1000x
    não entra no lugar dele. A brapi não dá marketCap de unit (BPAC11,
    TAEE11); por ações emitidas ÷ k, a unit tem porte.

    Ticker renomeado (ELET3 → AXIA3): vale o quote mais recente da cadeia.
    """
    from data_pipeline.market.b3_sucessao import cadeias
    fotos = {}
    for r in linhas:
        mc = _f(r.get("market_cap"))
        if mc is not None and mc > 0:
            fotos[str(r["ticker"]).upper()] = (r["fetched_at"], mc,
                                               r.get("origem"))
    por_tk = cadeias(sucessoes or {})
    hoje_d = hoje.date() if hasattr(hoje, "date") else hoje
    excl = {"foto_velha": 0, "sem_valor_de_mercado": 0}
    saida: dict[str, dict] = {}
    for tk in sorted(universo):
        da_cadeia = [(fotos[c], c) for c in por_tk.get(tk, [tk]) if c in fotos]
        if not da_cadeia:
            excl["sem_valor_de_mercado"] += 1
            continue
        (quando, mc, origem), codigo = max(da_cadeia, key=lambda x: x[0][0])
        if (hoje_d - quando.date()).days > max_idade:
            excl["foto_velha"] += 1
            continue
        de = "" if codigo == tk else f"{codigo} em "
        conta = ("valor de mercado: preço × ações emitidas"
                 if origem == "acoes" else "valor de mercado da brapi")
        saida[tk] = {"porte": _r(mc, 0),
                     "porte_ref": (f"{conta} (quote de "
                                   f"{de}{quando.date().isoformat()})")}
    return saida, excl


def linhas_de_valor_de_mercado(fotos) -> tuple[list[dict], dict]:
    """(linhas para ``valor_de_mercado_brapi``, {ticker: k}). Puro.

    ``fotos``: dicts com ticker, fetched_at, payload (o quote mais novo) e,
    opcional, composicao (o ``defaultKeyStatistics`` do quote completo mais
    novo, ``acoes.quote_com_composicao``). A conta é a de
    ``data_pipeline.market.acoes``, a mesma da linha spot ``marketCap``.
    k é 1 na ação e None na unit sem k derivável."""
    from data_pipeline.market import acoes as ac
    from data_pipeline.market.normalize import _infer_asset_type

    linhas, por_titulo = [], {}
    for r in fotos:
        tk = str(r["ticker"]).upper()
        q = ac.quote_com_composicao(r.get("payload") or {}, r.get("composicao"))
        unit = ac.exige_k(_infer_asset_type(tk, q), tk)
        k = ac.acoes_por_titulo(q, unit=unit)
        por_titulo[tk] = k
        mc = ac.valor_de_mercado(q.get("regularMarketPrice"),
                                 ac.acoes_emitidas(q), k)
        origem = "acoes"
        if mc is None:
            mc, origem = ac._positivo(q.get("marketCap")), "brapi"
        if mc is not None:
            preco = ac._positivo(q.get("regularMarketPrice"))
            linhas.append({"ticker": tk, "fetched_at": r["fetched_at"],
                           "market_cap": mc, "origem": origem,
                           # títulos de hoje: a régua da guarda das ações do ano
                           "titulos": (mc / preco) if preco else None})
    return linhas, por_titulo


def coletar_fotos_de_valor(conn) -> tuple[list[dict], dict]:
    """O quote mais recente por ticker, só com os campos da conta, e a
    composição (ações emitidas e os campos de k) do quote completo mais novo:
    o do refresh diário vem sem ``defaultKeyStatistics``. Os ids saem antes,
    sem abrir o payload: montar o JSON de cada uma das ~100 mil respostas
    para depois ficar com uma por ticker levava minutos."""
    from sqlalchemy import text
    fotos = [dict(r._mapping) for r in conn.execute(text("""
        SELECT b.ticker, b.fetched_at,
               jsonb_build_object(
                 'symbol', b.payload_json->'symbol',
                 'longName', b.payload_json->'longName',
                 'shortName', b.payload_json->'shortName',
                 'summaryProfile', jsonb_build_object(
                     'sector', b.payload_json->'summaryProfile'->'sector'),
                 'regularMarketPrice', b.payload_json->'regularMarketPrice',
                 'marketCap', b.payload_json->'marketCap',
                 'sharesOutstanding', b.payload_json->'sharesOutstanding',
                 'defaultKeyStatistics', jsonb_build_object(
                     'sharesOutstanding',
                     b.payload_json->'defaultKeyStatistics'->'sharesOutstanding',
                     'trailingEps',
                     b.payload_json->'defaultKeyStatistics'->'trailingEps',
                     'netIncomeToCommon',
                     b.payload_json->'defaultKeyStatistics'->'netIncomeToCommon')
               ) AS payload,
               jsonb_build_object(
                 'sharesOutstanding',
                 c.payload_json->'defaultKeyStatistics'->'sharesOutstanding',
                 'trailingEps', c.payload_json->'defaultKeyStatistics'->'trailingEps',
                 'netIncomeToCommon',
                 c.payload_json->'defaultKeyStatistics'->'netIncomeToCommon'
               ) AS composicao
          FROM (SELECT DISTINCT ON (ticker) ticker, id
                  FROM market.brapi_raw_payloads
                 WHERE endpoint = 'quote' AND request_status = 'success'
                   AND payload_json IS NOT NULL
                 ORDER BY ticker, fetched_at DESC, id DESC) mais_novo
          JOIN market.brapi_raw_payloads b ON b.id = mais_novo.id
          LEFT JOIN (SELECT DISTINCT ON (ticker) ticker, id
                       FROM market.brapi_raw_payloads
                      WHERE endpoint = 'quote' AND request_status = 'success'
                        AND payload_json->'defaultKeyStatistics' ?&
                            array['sharesOutstanding','trailingEps','netIncomeToCommon']
                      ORDER BY ticker, fetched_at DESC, id DESC) completo
                 ON completo.ticker = mais_novo.ticker
          LEFT JOIN market.brapi_raw_payloads c ON c.id = completo.id
    """))]
    return linhas_de_valor_de_mercado(fotos)


def coletar_alavancagem(conn, hoje) -> tuple[dict, dict]:
    from sqlalchemy import text
    linhas = [dict(r._mapping) for r in conn.execute(text("""
        SELECT DISTINCT ON (ticker) ticker, fetched_at,
               payload_json->'financialData' AS financial_data,
               payload_json->'incomeStatementHistory' AS anuais
          FROM market.brapi_raw_payloads
         WHERE endpoint = 'quote' AND request_status = 'success'
           AND payload_json ? 'financialData'
         ORDER BY ticker, fetched_at DESC, id DESC
    """))]
    return alavancagem_brapi(linhas, hoje)


# -- B3 -----------------------------------------------------------------------------

def acoes_por_titulo_do_ticker(tk: str, mapa: dict, units=()) -> int | None:
    """k de um ticker para os múltiplos anuais e o P/L mediano: o do mapa
    (``linhas_de_valor_de_mercado``); fora dele, 1, salvo unit (listada em
    ``units`` ou com sufixo 11, unit de companhia sem quote), que fica None
    porque o LPA da base é por ação e o preço é o da unit."""
    if tk in mapa:
        return mapa[tk]
    return None if (tk in set(units) or tk.endswith("11")) else 1


def historico_b3(fechamentos, demos, proventos, *, acoes_por_titulo=None,
                 units=(), ajustados=None, titulos_atuais=None) -> tuple[dict, dict]:
    """Séries anuais por ticker. Puro sobre DataFrames.

    ``fechamentos``: ticker, ano, close (fita, sem ajuste).
    ``demos``: ticker, ano, lucro, lpa, patrimonio.
    ``proventos``: ticker, ano, total (soma por ação, data-ex no ano).
    ``acoes_por_titulo``: {ticker: k} (``linhas_de_valor_de_mercado``). O
    ticker com k None no mapa, ou listado em ``units``, fica sem P/L e sem
    P/VP: o LPA da base é por ação e o preço é o da unit. Fora do mapa, k = 1,
    salvo sufixo 11 (unit de companhia sem quote).
    ``ajustados``: ticker, ano, close_ajustado (último close > 0 do ano em
    ``market.historical_prices``). ``titulos_atuais``: {ticker: títulos de
    hoje}. As ações do ano passam pela conta e pela guarda de
    `data_pipeline.market.acoes.acoes_e_lpa_do_ano`, a mesma do reprocess
    (`ingest.reprocess_metrics`): época até 5x as ações de hoje; o fator sem ajuste ÷
    ajustado só resgata o ano cujo LPA veio na base de hoje depois de um
    desdobramento; acima do teto e sem esse resgate, o ano fica sem P/L e sem
    P/VP. Sem ``titulos_atuais`` do
    ticker não há régua, e vale a época. Ano sem lucro absoluto (bancos na
    base) não tem ações do ano e fica com P/L = fechamento ÷ (k × LPA), sem
    essa guarda -- diferença conhecida do reprocess, que deixa o ano sem
    valuation. A guarda da mediana (``FATOR_MC_IMPLAUSIVEL``) é só deste
    caminho e vem depois.
    Devolve ({ticker: {"historico": {...}}}, {motivo: contagem}).
    """
    import pandas as pd

    from data_pipeline.market import acoes as ac
    df = (fechamentos.merge(demos, on=["ticker", "ano"], how="inner")
          .merge(proventos, on=["ticker", "ano"], how="left"))
    if ajustados is not None and not ajustados.empty:
        df = df.merge(ajustados[["ticker", "ano", "close_ajustado"]],
                      on=["ticker", "ano"], how="left")
    else:
        df["close_ajustado"] = float("nan")
    mapa = dict(acoes_por_titulo or {})
    atuais = dict(titulos_atuais or {})
    units = set(units)
    exclusoes = {"lpa_implausivel": 0, "acoes_implausiveis": 0,
                 "unit_sem_acoes_por_titulo": 0, "acoes_fora_da_guarda": 0,
                 "anos_lpa_base_de_hoje": 0}
    saida: dict[str, dict] = {}

    def _fora(serie):
        med = serie.median()
        if pd.isna(med) or med <= 0:
            return serie.isna() & False
        return ((serie / med > FATOR_MC_IMPLAUSIVEL)
                | (serie / med < 1 / FATOR_MC_IMPLAUSIVEL))

    def _num(v):
        return None if v is None or pd.isna(v) else float(v)

    for tk, g in df.sort_values("ano").groupby("ticker"):
        g = g.copy()
        k = acoes_por_titulo_do_ticker(tk, mapa, units)
        if k is None:
            exclusoes["unit_sem_acoes_por_titulo"] += 1
        pls, acoes_ano = [], []
        for r in g.itertuples():
            close, lucro, lpa = _num(r.close), _num(r.lucro), _num(r.lpa)
            adj = _num(r.close_ajustado)
            pl = acoes = None
            if k is not None and close and close > 0 and lpa:
                if lucro is None:
                    # P/L só precisa de preço e LPA, os dois na escala da época.
                    pl = close / (lpa * k) if lpa * k > 0 else None
                else:
                    base = ac.acoes_e_lpa_do_ano(
                        lucro=lucro, lpa=lpa, por_titulo=k,
                        acoes_atuais=atuais.get(tk),
                        fator_base=(close / adj) if adj and adj > 0 else None)
                    if base is None:
                        exclusoes["acoes_fora_da_guarda"] += 1
                    else:
                        acoes, lpa_titulo, qual = base
                        if qual == ac.BASE_DE_HOJE:
                            exclusoes["anos_lpa_base_de_hoje"] += 1
                        pl = close / lpa_titulo if lpa_titulo > 0 else None
            pls.append(pl)
            acoes_ano.append(acoes)
        g["pl"] = pd.Series(pls, index=g.index, dtype="float64")
        fora_pl = _fora(g["pl"])
        exclusoes["lpa_implausivel"] += int(fora_pl.sum())
        g.loc[fora_pl, "pl"] = None
        g["acoes"] = pd.Series(acoes_ano, index=g.index, dtype="float64")
        g["mc"] = g["close"] * g["acoes"]
        fora = _fora(g["mc"])
        exclusoes["acoes_implausiveis"] += int(fora.sum())
        g.loc[fora, ["acoes", "mc"]] = None
        pl = [(int(r.ano), r.pl) for r in g.itertuples() if pd.notna(r.pl)]
        pvp = [(int(r.ano), r.mc / r.patrimonio) for r in g.itertuples()
               if pd.notna(r.mc) and pd.notna(r.patrimonio) and r.patrimonio > 0]
        dy = [(int(r.ano), 100.0 * r.total / r.close) for r in g.itertuples()
              if pd.notna(r.total) and r.total > 0 and r.close > 0]
        saida[str(tk)] = {"historico": {"p_l": _serie(pl), "p_vp": _serie(pvp),
                                        "dividend_yield": _serie(dy)}}
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


def coletar_b3(conn, precos_supabase=None, hoje=None, sucessoes=None,
               proventos=None, fotos_valor=None) -> tuple[dict, dict, str | None]:
    from core.dividend_types import soma_por_ano
    from core.proventos_b3 import eventos_do_armazem

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
    # A resposta mais nova da brapi (core.proventos_b3): o DISTINCT por
    # (ticker, ex_date, amount) em market.dividends deixava passar a
    # amortização, a cópia por lote de mil e as revisões que o upsert não apaga.
    eventos = eventos_do_armazem(conn) if proventos is None else proventos
    prov = soma_por_ano(eventos, data="ex_date")
    # Ticker renomeado: a fita troca de código, o universo não.
    sucessoes = _sucessoes(conn) if sucessoes is None else sucessoes
    universo = set(demos["ticker"].astype(str))
    fech = fita_pelo_universo(fech, sucessoes, universo, ["ticker", "ano"])
    ultimo = fita_pelo_universo(ultimo, sucessoes, universo, ["ticker"])
    # Último close ajustado do ano, do próprio ticker (sem a cadeia): é o
    # denominador do fator de base das ações do ano, a mesma leitura do
    # reprocess (`acoes_cotacao.ajustados_de_fim_de_ano`).
    ajust = _ler(conn, """
        SELECT DISTINCT ON (ticker, EXTRACT(YEAR FROM date))
               ticker, EXTRACT(YEAR FROM date)::int AS ano, close AS close_ajustado
          FROM market.historical_prices
         WHERE close > 0 AND date >= make_date(:ini, 1, 1)
         ORDER BY ticker, EXTRACT(YEAR FROM date), date DESC
    """, ini=ANO_INICIAL)
    precos = (precos_supabase if precos_supabase is not None
              else _ler(conn, _SQL_PRECOS_MENSAIS))
    for c in ("close", "lucro", "lpa", "patrimonio", "total", "close_ajustado"):
        for df in (fech, demos, prov, ajust):
            if c in df:
                df[c] = df[c].astype(float)
    linhas_valor, por_titulo = (fotos_valor if fotos_valor is not None
                                else coletar_fotos_de_valor(conn))
    titulos = {str(lin["ticker"]).upper(): lin["titulos"] for lin in linhas_valor
               if lin.get("titulos")}
    hist, exclusoes = historico_b3(fech, demos, prov, acoes_por_titulo=por_titulo,
                                   ajustados=ajust, titulos_atuais=titulos)
    exclusoes["sucessoes_de_ticker"] = dict(sorted(sucessoes.items()))
    hoje = hoje or datetime.now(timezone.utc)
    precos["preco"] = precos["preco"].astype(float)
    vol = volatilidade(precos)
    mom, excl_mom = momento(precos, hoje)
    exclusoes["momento"] = excl_mom
    alav, excl_alav = coletar_alavancagem(conn, hoje)
    exclusoes["alavancagem"] = excl_alav
    porte, excl_porte = valor_de_mercado_brapi(linhas_valor, hist, hoje,
                                               sucessoes=sucessoes)
    exclusoes["porte"] = excl_porte
    for tk, item in hist.items():
        item.update(porte.get(tk, {}))
        if tk in vol:
            item["volatilidade"] = _r(vol[tk], 2)
        item.update(mom.get(tk, {}))
        item.update(alav.get(tk, {}))
    fita = str(ultimo["trade_date"].max())[:10] if not ultimo.empty else None
    return hist, exclusoes, fita


# -- mercado B3 ---------------------------------------------------------------------

def universo_mercado(volumes, com_lpa, *, n: int = UNIVERSO_MERCADO
                     ) -> dict[int, list[str]]:
    """{ano: os ``n`` tickers mais negociados no ano anterior}, uma classe por raiz.

    ``volumes``: ticker, ano, vol. Só entra quem tem demonstração na base
    (``com_lpa``): ETF e BDR também lideram o volume e não têm lucro.
    """
    saida: dict[int, list[str]] = {}
    for ano, g in volumes[volumes["ticker"].isin(com_lpa)].groupby("ano"):
        vistos, sel = set(), []
        for tk in g.sort_values("vol", ascending=False)["ticker"]:
            raiz = str(tk)[:4]
            if raiz in vistos:
                continue
            vistos.add(raiz)
            sel.append(str(tk))
            if len(sel) == n:
                break
        saida[int(ano) + 1] = sel
    return saida


def exercicio_publicado(mes) -> int:
    """Último exercício anual já publicado no mês (a DFP sai até março)."""
    return mes.year - 1 if mes.month >= 4 else mes.year - 2


def desdobramentos(marcas) -> dict[str, list]:
    """{ticker: [EventoCapital]} dos desdobramentos e grupamentos marcados pela B3.

    ``marcas``: ticker, data, espec, close, anterior -- os pregões com B ou G
    na marcação "ex" (``b3_saidas.marcador_b3``) e o fechamento do pregão
    anterior. Todo pregão marcado é candidato: fator = anterior ÷
    fechamento, levado ao fator redondo mais próximo a ``TOL_MARCADOR``; sem
    redondo, o próprio salto, se passar de ``MERCADO_SALTO_MINIMO`` no
    sentido da marca (grupamento de 15:1 da HAPV3 em 06/2025). A B3 mantém
    a marca por dias, então um evento aceito cala os candidatos dos
    ``MERCADO_MARCA_DIAS`` seguintes. O salto decide, não o começo da
    sequência de marcas: a marca de juros ou bonificação de semanas antes
    escondia o desdobramento (SBSP3 1:5 em 04/2026, a 29 dias de um EJB;
    CIEL3 3:1 em 05/2011). Bonificação (até 1,5x) fica fora: não tira P/L
    plausível da faixa, e com a marca intermitente um pregão de 5% virava
    bonificação de 5% (SBSP3, 12/2025).
    """
    import pandas as pd

    from data_pipeline.market.b3_saidas import (
        FATORES_REDONDOS,
        TOL_MARCADOR,
        EventoCapital,
        marcador_b3,
    )
    saida: dict[str, list] = {}
    if marcas.empty:
        return saida
    m = marcas.assign(marca=marcas["espec"].map(marcador_b3),
                      data=pd.to_datetime(marcas["data"]))
    m = (m[m["marca"].str.contains("[BG]")]
         .sort_values(["ticker", "data"]).drop_duplicates(["ticker", "data"]))
    janela = pd.Timedelta(days=MERCADO_MARCA_DIAS)
    for r in m.itertuples():
        k = float(r.anterior) / float(r.close)
        cands = ([1.0 / f for f in FATORES_REDONDOS] if "G" in r.marca else []) \
            + ([float(f) for f in FATORES_REDONDOS] if "B" in r.marca else [])
        c = min(cands, key=lambda c: abs(math.log(c / k)))
        if abs(math.log(c / k)) <= TOL_MARCADOR:
            fator = c
        elif (("G" in r.marca and 1e-3 <= k <= 1 / MERCADO_SALTO_MINIMO)
              or ("B" in r.marca and MERCADO_SALTO_MINIMO <= k <= 1e3)):
            fator = round(k, 4)
        else:
            continue
        aceitos = saida.setdefault(str(r.ticker), [])
        if aceitos and r.data - aceitos[-1].data <= janela:
            continue
        aceitos.append(EventoCapital(r.data, fator, "marcador_b3"))
    return saida


def mercado_b3(fechamentos, volumes, lpa, *, eventos: dict | None = None,
               n: int = UNIVERSO_MERCADO, ano_inicial: int = ANO_INICIAL + 1,
               acoes_por_titulo: dict | None = None, units=()) -> dict:
    """Série mensal do P/L mediano ponto-no-tempo. Puro sobre DataFrames.

    ``fechamentos``: ticker, data (último pregão do mês), close (fita, sem
    ajuste). ``volumes``: ticker, ano, vol. ``lpa``: ticker, ano, lpa.
    ``eventos``: {ticker: [EventoCapital]} (``desdobramentos``). O LPA vem
    na base de ações da DFP; desdobramento ou grupamento depois dela (abril
    do ano seguinte ao exercício) e até o pregão divide o LPA pelo fator --
    sem isso o 1:5 da SBSP3 em 04/2026 e o da ASAI3 em 08/2021 davam P/L 5x
    menor até a DFP seguinte.
    ``acoes_por_titulo``: {ticker: k}. Na unit o LPA entra × k, o LPA do
    título (``acoes_por_titulo_do_ticker``); unit sem k fica fora, em
    ``sem_acoes_por_titulo``, em vez de entrar com P/L k vezes maior.
    Devolve {"serie": [[data, p_l, lucro_sobre_preco_pct, n], ...],
    "ultimo": {...}, "descartado": {...} | None} -- ``ultimo`` diz quem ficou
    fora no último mês da série e por quê (e o fator de quem foi rebaseado);
    ``descartado``, o mesmo do mês mais recente da fita quando ele não entrou
    (menos de MERCADO_MIN_VALIDAS).

    Prejuízo entra de qualquer tamanho: a mediana só depende do sinal dele,
    e cortá-lo puxava o P/L mediano para baixo (a CSAN3 de 2025, prejuízo
    maior que o preço, é real). LPA em escala errada ainda cai pelo lado
    positivo -- e lá a faixa fica, porque lucro real minúsculo (VALE3 2013,
    P/L 900-1600) e LPA na base errada (CSNA3 2017, 10x menor; AZUL4, LPA
    da ON, que vale 1/75 da PN) dão P/L na mesma faixa.
    """
    import pandas as pd

    from data_pipeline.market.b3_saidas import fator_acumulado
    eventos = eventos or {}
    mapa = dict(acoes_por_titulo or {})
    units = set(units)
    lpa_d = {(str(r.ticker), int(r.ano)): float(r.lpa) for r in lpa.itertuples()
             if pd.notna(r.lpa)}
    universo = universo_mercado(volumes, set(lpa["ticker"].astype(str)), n=n)
    f = fechamentos.copy()
    f["data"] = pd.to_datetime(f["data"])
    f["mes"] = f["data"].dt.to_period("M")
    serie, ultimo, descartado = [], None, None
    for mes, g in f.groupby("mes"):
        if mes.year < ano_inicial or mes.year not in universo:
            continue
        px = {str(r.ticker): (float(r.close), r.data) for r in g.itertuples()}
        exerc = exercicio_publicado(mes)
        dfp = pd.Timestamp(exerc + 1, 3, 31)
        eys, sem_lpa, sem_preco, implausiveis, sem_k = [], [], [], [], []
        rebaseadas = {}
        for tk in universo[mes.year]:
            if tk not in px or not px[tk][0] > 0:
                sem_preco.append(tk)
                continue
            k = acoes_por_titulo_do_ticker(tk, mapa, units)
            if k is None:
                sem_k.append(tk)
                continue
            lucro = lpa_d.get((tk, exerc))
            if lucro is None or lucro == 0:
                sem_lpa.append(tk)
                continue
            lucro *= k
            fator = fator_acumulado(eventos.get(tk, []), dfp, px[tk][1])
            if fator != 1.0:
                lucro /= fator
                rebaseadas[tk] = _r(fator)
            ey = lucro / px[tk][0]
            if ey > 0 and not MERCADO_PL_MINIMO <= 1 / ey <= MERCADO_PL_MAXIMO:
                implausiveis.append(tk)
                continue
            eys.append(ey)
        data = max(d for _, d in px.values()).date().isoformat()
        quem = {"data": data, "exercicio": exerc,
                "universo": len(universo[mes.year]), "validas": len(eys),
                "negativas": sum(1 for y in eys if y < 0),
                "sem_lpa": sem_lpa, "sem_preco": sem_preco,
                "implausiveis": implausiveis, "sem_acoes_por_titulo": sem_k,
                "rebaseadas": rebaseadas}
        if len(eys) < MERCADO_MIN_VALIDAS:
            descartado = quem
            continue
        mediana = float(pd.Series(eys).median())
        serie.append([data, _r(1 / mediana, 2) if mediana > 0 else None,
                      _r(100 * mediana, 2), len(eys)])
        ultimo, descartado = quem, None
    return {"serie": serie, "ultimo": ultimo, "descartado": descartado}


def motivo_para_recusar(fita, mercado: dict, hoje) -> str | None:
    """Por que o arquivo não pode sair hoje, ou None. Puro.

    O mês com menos de MERCADO_MIN_VALIDAS ações válidas fica fora da série,
    e o leitor vê só o último que entrou: em 26-27/10/2026, demonstrações
    regravadas com o LPA 1000x tiraram abril a outubro da série calculada do
    armazém, e ela parava em março sem erro nenhum. Recusar faz a rotina
    noturna acusar a falha em vez de publicar o ponto velho como atual.
    """
    if fita is None or (hoje - date.fromisoformat(fita)).days > FITA_MAXIMA_DIAS:
        return (f"fita da B3 parada há mais de {FITA_MAXIMA_DIAS} dias; o P/L "
                "mediano e os múltiplos sairiam com preço velho.")
    d, u = mercado.get("descartado"), mercado.get("ultimo")
    if d:
        ate = mercado["serie"][-1][0] if mercado["serie"] else "nenhum mês"
        return (f"P/L mediano B3: {d['data']} ficou fora da série, só "
                f"{d['validas']} de {d['universo']} ações válidas (LPA em escala "
                f"errada: {', '.join(d['implausiveis']) or '-'}; sem LPA do "
                f"exercício {d['exercicio']}: {', '.join(d['sem_lpa']) or '-'}); "
                f"a série pararia em {ate}.")
    if u is None:
        return "P/L mediano B3: série vazia."
    if len(u["implausiveis"]) > MERCADO_IMPLAUSIVEIS_MAX:
        return (f"P/L mediano B3: {len(u['implausiveis'])} ações com LPA em escala "
                f"errada em {u['data']} ({', '.join(u['implausiveis'])}), mais que "
                f"{MERCADO_IMPLAUSIVEIS_MAX}; a mediana sairia de amostra torta.")
    return None


_SQL_FECHAMENTO_MENSAL = """
    SELECT DISTINCT ON (ticker, date_trunc('month', trade_date))
           ticker, trade_date AS data,
           COALESCE(close_unitario, close / NULLIF(fator_cotacao, 0)) AS close
      FROM market.b3_security_history
     WHERE close > 0 AND trade_date >= make_date(:ini, 1, 1)
     ORDER BY ticker, date_trunc('month', trade_date), trade_date DESC,
              collected_at DESC
"""


# Pregões com B ou G na marca "ex" e o fechamento do pregão anterior; o
# filtro de texto é largo e ``desdobramentos`` refaz com ``marcador_b3``.
_SQL_DESDOBRAMENTOS = r"""
    WITH d AS (
        SELECT DISTINCT ON (ticker, trade_date) ticker, trade_date, specification,
               COALESCE(close_unitario, close / NULLIF(fator_cotacao, 0)) AS close
          FROM market.b3_security_history
         WHERE close > 0 AND trade_date >= make_date(:ini, 1, 1)
         ORDER BY ticker, trade_date, collected_at DESC
    ), l AS (
        SELECT ticker, trade_date, specification, close,
               LAG(close) OVER (PARTITION BY ticker ORDER BY trade_date) AS anterior
          FROM d
    )
    SELECT ticker, trade_date AS data, specification AS espec, close, anterior
      FROM l
     WHERE specification ~ '(^| )\*?E[A-Z]*[BG]' AND anterior > 0
"""


def coletar_mercado_b3(conn, sucessoes=None, acoes_por_titulo=None) -> dict:
    fech = _ler(conn, _SQL_FECHAMENTO_MENSAL, ini=ANO_INICIAL + 1)
    vol = _ler(conn, """
        SELECT ticker, EXTRACT(YEAR FROM trade_date)::int AS ano,
               SUM(financial_volume) AS vol
          FROM market.b3_security_history
         WHERE trade_date >= make_date(:ini, 1, 1) AND financial_volume > 0
         GROUP BY 1, 2
    """, ini=ANO_INICIAL)
    lpa = _ler(conn, """
        SELECT DISTINCT ON (ticker, year) ticker, year AS ano, eps AS lpa
          FROM market.income_statements
         WHERE period = 'annual' AND year >= :ini
         ORDER BY ticker, year, updated_at DESC
    """, ini=ANO_INICIAL)
    marcas = _ler(conn, _SQL_DESDOBRAMENTOS, ini=ANO_INICIAL)
    fech["close"] = fech["close"].astype(float)
    vol["vol"] = vol["vol"].astype(float)
    lpa["lpa"] = lpa["lpa"].astype(float)
    suc = sucessoes if sucessoes is not None else _sucessoes(conn)
    com_lpa = set(lpa["ticker"].astype(str))
    fech, vol = mercado_pela_sucessao(fech, vol, suc, com_lpa)
    if suc:
        from data_pipeline.market.b3_sucessao import canonizar_fita
        marcas = canonizar_fita(marcas, suc, com_lpa)
    if acoes_por_titulo is None:
        acoes_por_titulo = coletar_fotos_de_valor(conn)[1]
    return mercado_b3(fech, vol, lpa, eventos=desdobramentos(marcas),
                      acoes_por_titulo=acoes_por_titulo)


def mercado_pela_sucessao(fech, vol, sucessoes: dict, com_lpa):
    """Uma empresa, um código: a cadeia renomeada vira o seu representante.

    Sem isso ELET3 sai do universo de 2026 sem preço (a fita passou a AXIA3)
    e, com as duas na base de demonstrações, as duas podiam entrar no top
    como empresas diferentes. Volume do ano soma; fechamento do mês fica o
    do pregão mais recente. Puro sobre DataFrames.
    """
    import pandas as pd

    from data_pipeline.market.b3_sucessao import canonizar_fita
    if not sucessoes:
        return fech, vol
    fech = canonizar_fita(fech, sucessoes, com_lpa)
    mes = pd.to_datetime(fech["data"]).dt.to_period("M")
    fech = (fech.assign(_mes=mes).sort_values("data", kind="stable")
            .drop_duplicates(["ticker", "_mes"], keep="last")
            .drop(columns="_mes").reset_index(drop=True))
    vol = (canonizar_fita(vol, sucessoes, com_lpa)
           .groupby(["ticker", "ano"], as_index=False)["vol"].sum())
    return fech, vol


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


def coletar_fii(conn, precos=None, hoje=None, proventos=None) -> tuple[dict, dict]:
    import pandas as pd

    from core.proventos_b3 import eventos_do_armazem

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
    # Só rendimento, da mesma fonte da B3: o DISTINCT em market.dividends
    # deixava a amortização e as revisões entrarem no DY.
    fiis = set(_ler(conn, "SELECT DISTINCT ticker FROM market.fii_metrics_monthly")["ticker"])
    eventos = eventos_do_armazem(conn) if proventos is None else proventos
    datas = pd.to_datetime(eventos["ex_date"])
    prov = eventos.loc[(datas >= pd.Timestamp(2022, 1, 1)) & eventos["ticker"].isin(fiis),
                       ["ticker", "ex_date", "amount"]]
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


#: Salto nas ações implícitas (valor ÷ preço) de um mês para o seguinte que
#: caracteriza troca de regime na contagem do ponto-no-tempo: o primeiro
#: balanço depois de um split. Recompra e emissão correntes mudam a contagem
#: alguns pontos percentuais por trimestre, nunca 45%.
SALTO_ACOES = 1.45
#: Razões de split que a bolsa pratica. Um degrau que casa com uma delas (ou
#: com o inverso, no grupamento) dentro de ``TOLERANCIA_SPLIT`` é split mesmo
#: sem registro em ``market_us.splits``; 1,6x não é: é emissão, e emissão muda
#: o valor de mercado de verdade.
RAZOES_DE_SPLIT = (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50, 100,
                   3 / 2, 4 / 3, 5 / 4)
TOLERANCIA_SPLIT = 0.025
#: O balanço que traz a contagem nova sai até este tanto depois do split
#: (empresa que só reporta no 10-K anual).
DIAS_ATE_O_BALANCO = 300
#: Mês isolado cujo valor foge disto da mediana móvel das vizinhanças é
#: contagem errada daquele mês, não regime novo.
DESVIO_MES_ISOLADO = 1.5
#: Até quantos meses seguidos de contagem errada são reconhecidos como bloco
#: errado, e não como regime. A AAPL tem abr--jun/2014 cerca de 1000x abaixo,
#: três meses colados ao split 7:1: com só o mês isolado reconhecido, o bloco
#: escondia o split e o histórico até 2013 ficava 7x baixo.
MESES_DO_BLOCO_ERRADO = 3
#: Os meses de um bloco errado têm a mesma contagem errada entre si, dentro
#: desta tolerância.
COERENCIA_DO_BLOCO = 1.25
#: Degrau para baixo sem split que leva as ações a menos desta fração da
#: contagem anterior. Recompra não faz isso; é grupamento misturado com
#: emissão, sem razão de split que o explique, e o passado não tem como ser
#: posto na escala de hoje -- sai.
ACOES_CAEM_A_MENOS_DE = 0.5
#: Degrau de 1000^k (dentro desta tolerância) é erro de escala do XBRL
#: (milhares contra unidades) na contagem do balanço. Patrimônio e lucro do
#: mesmo balanço costumam vir com o mesmo erro, então o degrau fica.
TOLERANCIA_DEGRAU_ESCALA = 1.25


def _parece_split(razao: float) -> bool:
    if not razao or razao <= 0:
        return False
    for r in RAZOES_DE_SPLIT:
        for alvo in (r, 1.0 / r):
            if abs(math.log(razao) - math.log(alvo)) <= math.log(1 + TOLERANCIA_SPLIT):
                return True
    return False


#: Quanto a razão registrada pode diferir do salto medido e ainda explicá-lo:
#: o salto carrega a recompra ou emissão do trimestre.
TOLERANCIA_SALTO_REGISTRADO = 1.25


def _split_registrado(splits, simbolo: str, quando, medido: float):
    """Razão do split de ``market_us.splits`` que explica um degrau visto em
    ``quando``: data do split até :data:`DIAS_ATE_O_BALANCO` antes do degrau,
    no mesmo sentido do que foi medido e com a razão mais próxima do salto.
    Dois splits na janela (2:1 e depois 3:1) casam cada um com o seu degrau;
    registro que não explica o salto medido não é usado."""
    if splits is None or len(splits) == 0:
        return None
    import pandas as pd
    s = splits[splits["symbol"] == simbolo]
    if s.empty or not medido or medido <= 0:
        return None
    datas = pd.to_datetime(s["split_date"])
    janela = (datas <= quando) & (datas >= quando - pd.Timedelta(days=DIAS_ATE_O_BALANCO))
    melhor, distancia = None, None
    for _, linha in s.loc[janela].iterrows():
        try:
            razao = float(linha["numerator"]) / float(linha["denominator"])
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if razao <= 0 or (razao > 1) != (medido > 1):
            continue
        d = abs(math.log(razao) - math.log(medido))
        if d <= math.log(TOLERANCIA_SALTO_REGISTRADO) and (distancia is None or d < distancia):
            melhor, distancia = razao, d
    return melhor


def _picos_isolados(acoes, max_meses: int = MESES_DO_BLOCO_ERRADO):
    """Máscara dos meses cuja contagem destoa dos vizinhos dos DOIS lados:
    contagem errada de um mês, ou de um bloco de até ``max_meses`` meses
    seguidos com a mesma contagem errada (dentro de
    :data:`COERENCIA_DO_BLOCO`), não degrau. O primeiro mês de um regime novo
    destoa só do lado de antes e fica; o bloco errado na véspera de um split
    destoa dos dois e sai -- com ele no meio, nenhum dos dois saltos parecia
    persistente, o degrau não era medido e o histórico ficava na escala
    errada (AAPL: abr--jun/2014 cerca de 1000x abaixo, antes do 7:1). Por
    isso o bloco sai ANTES de medir o degrau. Com ``max_meses=1`` é o pico
    isolado de um mês só."""
    import numpy as np
    acoes = np.asarray(acoes, dtype=float)
    n = len(acoes)
    pico = np.zeros(n, dtype=bool)
    limite = math.log(DESVIO_MES_ISOLADO)
    for tamanho in range(1, max_meses + 1):
        for i in range(1, n - tamanho):
            bloco = acoes[i:i + tamanho]
            if not (bloco > 0).all():
                continue
            if tamanho > 1 and math.log(bloco.max() / bloco.min()) > math.log(COERENCIA_DO_BLOCO):
                continue
            antes = acoes[max(0, i - 2):i]
            depois = acoes[i + tamanho:i + tamanho + 2]
            antes, depois = antes[antes > 0], depois[depois > 0]
            if not len(antes) or not len(depois):
                continue
            m_bloco = float(np.median(bloco))
            if (abs(math.log(m_bloco / float(np.median(antes)))) > limite
                    and abs(math.log(m_bloco / float(np.median(depois)))) > limite):
                pico[i:i + tamanho] = True
    return pico


def _degrau_de_escala(salto: float) -> bool:
    """Salto de 1000^k (k != 0): erro de escala do XBRL, não evento."""
    if not salto or salto <= 0:
        return False
    k = round(math.log10(salto) / 3)
    return k != 0 and abs(math.log10(salto) - 3 * k) <= math.log10(TOLERANCIA_DEGRAU_ESCALA)


def _retroajustar_simbolo(g, splits, contagem: dict):
    """Multiplica o valor de mercado anterior a cada degrau de contagem pela
    razão do split. ``g`` é o quadro de UM símbolo ordenado por data, com as
    colunas ``acoes`` e ``market_cap``; devolve ``market_cap`` ajustado.

    Degrau que não é split: o de escala (1000^k) e o que não leva as ações a
    menos de :data:`ACOES_CAEM_A_MENOS_DE` da contagem anterior (emissão,
    fusão, recompra grande) ficam como estão. O que leva -- grupamento
    misturado com emissão, sem razão de split -- deixa o passado sem escala
    conhecida: os meses anteriores voltam como NaN, para serem descartados."""
    import numpy as np
    acoes = g["acoes"].to_numpy(dtype=float)
    fatores = np.ones(len(g))
    for i in range(1, len(g)):
        if not (acoes[i] > 0 and acoes[i - 1] > 0):
            continue
        salto = acoes[i] / acoes[i - 1]
        if abs(math.log(salto)) <= math.log(SALTO_ACOES):
            continue
        # Degrau persistente: a contagem velha era estável e a nova fica. Pico
        # de um mês só é contagem errada daquele mês, tratado depois como mês
        # isolado -- e a volta do pico não pode passar por degrau para baixo.
        seguintes = acoes[i + 1:i + 4]
        seguintes = seguintes[seguintes > 0]
        if len(seguintes) and abs(math.log(float(np.median(seguintes)) / acoes[i])) > math.log(1.25):
            continue
        anteriores = acoes[max(0, i - 4):i - 1]
        anteriores = anteriores[anteriores > 0]
        if len(anteriores) and abs(math.log(float(np.median(anteriores)) / acoes[i - 1])) > math.log(1.25):
            continue
        registrado = _split_registrado(splits, g["symbol"].iloc[i], g["date"].iloc[i], salto)
        if len(seguintes) < 2 and registrado is None:
            # Salto no fim da série, sem meses que o confirmem nem split
            # registrado: ainda não é regime. Fica para o filtro de mês
            # isolado, que o descarta; reescrever o histórico inteiro por um
            # mês ainda não confirmado é o erro que não se desfaz.
            continue
        razao = registrado
        if razao is None and _parece_split(salto):
            razao = salto
        if razao is None:
            if salto < ACOES_CAEM_A_MENOS_DE and not _degrau_de_escala(salto):
                # Recompra não corta a contagem pela metade: é grupamento
                # sem razão de split. O passado não tem escala conhecida.
                contagem["degraus_cortados"] += 1
                fatores[:i] = np.nan
            else:
                contagem["degraus_mantidos"] += 1   # emissão, recompra, escala
            continue
        contagem["degraus_retroajustados"] += 1
        fatores[:i] *= razao
    cortados = np.isnan(fatores)
    contagem["meses_cortados"] += int(cortados.sum())
    contagem["meses_retroajustados"] += int((~cortados & (fatores != 1.0)).sum())
    return g["market_cap"].to_numpy(dtype=float) * fatores


def capitalizacao_limpa(capitalizacao, precos, splits=None) -> tuple:
    """Valor de mercado mês a mês na escala correta, com o que não dá para
    corrigir descartado e contado.

    ``market_cap_history`` multiplica o preço ajustado por split pelas ações
    do ponto-no-tempo, que NÃO são retroajustadas: todo mês anterior ao
    primeiro balanço pós-split sai dividido pela razão do split (AAPL até
    set/2020 inteira em 1/4). Até 08/11/2026 a saída era ficar só com o trecho
    depois do último degrau -- e 2.852 das 4.350 empresas ficaram sem um ano
    sequer de P/L, porque quase toda empresa com 15 anos de bolsa desdobrou
    alguma vez. Agora o degrau é retroajustado: o valor anterior é
    multiplicado pela razão do split (``market_us.splits`` quando registrado;
    senão a razão medida, se for uma que a bolsa pratica). Degrau que não é
    split (emissão, recompra grande) fica como está, porque é mudança real;
    o de escala do XBRL (1000^k) também fica. Degrau sem split que leva as
    ações a menos da metade é grupamento misturado com emissão: o passado
    dele sai (``degraus_cortados``, ``meses_cortados``). Antes de medir os
    degraus saem os blocos de até :data:`MESES_DO_BLOCO_ERRADO` meses com
    contagem errada.

    Depois disso, mês isolado cujas ações implícitas fogem mais de
    :data:`DESVIO_MES_ISOLADO` da mediana móvel de 13 meses é contagem errada
    daquele mês e é descartado. Mês sem preço não tem como ser conferido e
    também sai. ``precos``: symbol, mes, close. ``splits``: symbol,
    split_date, numerator, denominator (opcional).

    Devolve ``(quadro, contagens)``; ``contagens["capitalizacao_fora_do_preco"]``
    mantém o nome que o arquivo publicado sempre teve.
    """
    import numpy as np
    import pandas as pd
    contagem = {"capitalizacao_fora_do_preco": 0, "sem_preco_no_mes": 0,
                "degraus_retroajustados": 0, "degraus_mantidos": 0,
                "meses_retroajustados": 0, "degraus_cortados": 0,
                "meses_cortados": 0}
    c = capitalizacao.dropna(subset=["date", "market_cap"]).copy()
    c["date"] = pd.to_datetime(c["date"])
    c["mes"] = c["date"].dt.to_period("M")
    p = precos.dropna(subset=["close"]).copy()
    p["mes"] = pd.to_datetime(p["mes"]).dt.to_period("M")
    c = c.merge(p[["symbol", "mes", "close"]], on=["symbol", "mes"], how="left")
    sem_preco = c["close"].isna() | ~(c["close"] > 0)
    contagem["sem_preco_no_mes"] = int(sem_preco.sum())
    c = c.loc[~sem_preco].sort_values(["symbol", "date"]).reset_index(drop=True)
    if c.empty:
        return c[["symbol", "date", "market_cap"]], contagem
    c["acoes"] = c["market_cap"] / c["close"]
    # 1. Picos isolados saem antes de medir degraus: um mês errado colado a um
    #    split escondia o split e deixava o histórico na escala errada.
    pico = np.concatenate([_picos_isolados(g["acoes"].to_numpy())
                           for _, g in c.groupby("symbol", sort=False)])
    contagem["capitalizacao_fora_do_preco"] += int(pico.sum())
    c = c.loc[~pico].reset_index(drop=True)
    if c.empty:
        return c[["symbol", "date", "market_cap"]], contagem
    # 2. Degraus de contagem retroajustados pela razão do split.
    c["market_cap"] = np.concatenate([
        _retroajustar_simbolo(g, splits, contagem) for _, g in c.groupby("symbol", sort=False)])
    #    O passado de um degrau cortado (NaN) sai aqui; os cortes são sempre
    #    o começo da série, então a mediana móvel abaixo não muda por isso.
    c = c.loc[c["market_cap"].notna()].reset_index(drop=True)
    if c.empty:
        return c[["symbol", "date", "market_cap"]], contagem
    c["acoes"] = c["market_cap"] / c["close"]
    # 3. O que ainda destoa da vizinhança na série já corrigida (bordas, picos
    #    duplos) sai pela mediana móvel.
    c["ref"] = (c.groupby("symbol")["acoes"]
                .transform(lambda s: s.rolling(13, center=True, min_periods=5)
                           .median()))
    razao = (c["acoes"] / c["ref"]).astype(float)
    fora = razao.notna() & (np.abs(np.log(razao.where(razao > 0))) > math.log(DESVIO_MES_ISOLADO))
    contagem["capitalizacao_fora_do_preco"] += int(fora.sum())
    c = c.loc[~fora]
    return c[["symbol", "date", "market_cap"]].reset_index(drop=True), contagem


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
    # Leitura opcional em savepoint: tabela ausente abortaria a transação da
    # conexão única e derrubaria o que vem depois (coletar_mercado_b3).
    try:
        with conn.begin_nested():
            splits = _ler(conn, """
                SELECT symbol, split_date, numerator, denominator
                  FROM market_us.splits
                 WHERE symbol IN (SELECT symbol FROM market_us.company_snapshots)
            """)
    except Exception as exc:  # noqa: BLE001 - sem a tabela, vale a razão medida
        print(f"  splits não lidos ({type(exc).__name__}); degraus pela razão medida",
              file=sys.stderr)
        splits = None
    for col in ("net_income", "equity", "dividends_paid"):
        anuais[col] = anuais[col].astype(float)
    cap["market_cap"] = cap["market_cap"].astype(float)
    mensais["close"] = mensais["close"].astype(float)
    mensais["adjusted_close"] = mensais["adjusted_close"].astype(float)
    cap, contagem_cap = capitalizacao_limpa(cap, mensais, splits)
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
    return saida, {**contagem_cap, "momento": excl_mom}


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

    from core.proventos_b3 import eventos_do_armazem
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
        proventos = eventos_do_armazem(conn)
        # Uma leitura das fotos de valor para os dois caminhos que usam k.
        fotos_valor = coletar_fotos_de_valor(conn)
        b3, excl_b3, fita = coletar_b3(conn, precos, agora, proventos=proventos,
                                       fotos_valor=fotos_valor)
        fii, excl_fii = coletar_fii(conn, precos, agora, proventos=proventos)
        eua, excl_eua = coletar_eua(conn)
        mercado = coletar_mercado_b3(conn, acoes_por_titulo=fotos_valor[1])

    relatorio = {
        "b3": len(b3), "fii": len(fii), "eua": len(eua),
        "fita_b3_mais_recente": fita, "exclusoes_b3": excl_b3,
        "precos_b3_fii": ("Supabase" if precos is not None
                          else "armazém local (Supabase indisponível)"),
        "exclusoes_fii": excl_fii, "exclusoes_eua": excl_eua,
        "mercado_b3": {"meses": len(mercado["serie"]),
                       "ultimo": (mercado["serie"] or [None])[-1],
                       "fora": mercado["ultimo"],
                       "descartado": mercado["descartado"]},
    }
    motivo = motivo_para_recusar(fita, mercado, agora.date())
    if motivo:
        relatorio["erro"] = motivo
        print(json.dumps(relatorio, ensure_ascii=False, indent=2))
        return 1
    payload = {
        "versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
        "fita_b3": fita, "metodo": METODO,
        "precos_b3_fii": ("supabase" if precos is not None else "local"),
        "exclusoes": {"b3": excl_b3, "fii": excl_fii, "eua": excl_eua},
        "b3": b3, "fii": fii, "eua": eua, "mercado_b3": mercado,
    }
    if not args.dry_run:
        serializar(payload, args.saida)
        relatorio["arquivo"] = str(args.saida)
        relatorio["bytes"] = args.saida.stat().st_size
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

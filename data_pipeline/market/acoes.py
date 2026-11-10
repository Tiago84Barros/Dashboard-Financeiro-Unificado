"""Valor de mercado por título negociado: ações emitidas e ações por título.

Ponto único da conta, usado pelo normalizador (linha spot ``marketCap``), pelo
``reprocess_metrics`` (P/L, P/VP, EV/EBIT e P/FCO de hoje e do histórico) e pelo
``publish_valuation_historico`` (porte e P/L e P/VP anuais).

**A convenção dos sites.** O Fundamentus, o Status Invest e o Investidor10
dividem o preço do título pelo lucro *por título*. Para ação, o valor de
mercado é o preço da classe × as ações emitidas da companhia (todas as classes).
Para unit, o preço da unit × as ações emitidas ÷ as ações que cada unit
carrega (k). A conta é a mesma de P/L = preço / LPA do título.

**Por que não o ``marketCap`` da brapi.** Medido em 10/11/2026 numa amostra
de 34 empresas, contra os três sites:
- é um número só por companhia, igual em ENGI3 e ENGI4 (17,5 bi), e não bate
  com preço × ações em nenhuma das duas classes (razões de 0,65 e 0,79);
- em VALE3 a razão contra os sites era 0,905, e em PETR4, 1,058;
- unit não tem ``marketCap`` (BPAC11, SANB11). O reserva antigo, preço da unit
  × lucro / LPA por ação, inflava o valor k vezes (TAEE11 ×3, KLBN11 ×5).

Com preço × ações emitidas ÷ k, levado ao mesmo preço, a razão contra o
Fundamentus ficou em 1,000 em todos os tickers da amostra que tinham as duas
medidas, salvo IGTI3/IGTI11, onde os próprios sites divergem (abaixo).

**De onde sai k.** No próprio quote da unit: ``trailingEps`` é o LPA *por
unit*, e ``netIncomeToCommon`` / ``sharesOutstanding`` é o LPA por ação. A
razão deu inteiro exato nas dez units de companhia da B3 (3, 5, 2 e 1). O
valor patrimonial por unit não serve, porque o patrimônio da brapi inclui os
minoritários. Quando a razão não fecha num inteiro, k fica desconhecido: a
unit fica só com o ``marketCap`` da brapi, que é da companhia (a mesma
convenção dos sites), e nunca com o reserva lucro / LPA, que inflava k vezes.

Convenção divergente registrada: em IGTI3/IGTI11 o Fundamentus conta 2,08 bi
de ações e o Status Invest e o Investidor10 contam 1,21 bi, a mesma base da
brapi. Fica a da brapi, porque é a dos dois sites que concordam.
"""
from __future__ import annotations

# Folga para k ser inteiro: k vem de três campos arredondados da brapi.
TOL_ACOES_POR_TITULO = 0.05
# Nenhuma unit da B3 carrega mais que isso. Acima, a razão é lixo de unidade.
MAX_ACOES_POR_TITULO = 10


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _positivo(v) -> float | None:
    x = _f(v)
    return x if x is not None and x > 0 else None


def acoes_emitidas(quote: dict) -> float | None:
    """Ações emitidas da companhia, todas as classes (igual em TAEE3/4/11)."""
    quote = quote or {}
    dks = quote.get("defaultKeyStatistics") or {}
    return _positivo(dks.get("sharesOutstanding")) or _positivo(quote.get("sharesOutstanding"))


def acoes_por_titulo(quote: dict, *, unit: bool) -> int | None:
    """Quantas ações cada título carrega: 1 para ação; k para unit; None se
    a unit não deixa derivar k com segurança."""
    if not unit:
        return 1
    quote = quote or {}
    dks = quote.get("defaultKeyStatistics") or {}
    acoes = acoes_emitidas(quote)
    lpa_titulo = _f(dks.get("trailingEps"))
    lucro = _f(dks.get("netIncomeToCommon"))
    if acoes is None or lpa_titulo is None or lucro is None or abs(lucro) < 1.0:
        return None
    k = acoes * lpa_titulo / lucro
    inteiro = round(k)
    if 1 <= inteiro <= MAX_ACOES_POR_TITULO and abs(k - inteiro) <= TOL_ACOES_POR_TITULO:
        return int(inteiro)
    return None


# Campos de ``defaultKeyStatistics`` de que saem as ações emitidas e k.
COMPOSICAO = ("sharesOutstanding", "trailingEps", "netIncomeToCommon")


def tem_composicao(quote: dict) -> bool:
    dks = (quote or {}).get("defaultKeyStatistics") or {}
    return all(_f(dks.get(c)) is not None for c in COMPOSICAO)


def quote_com_composicao(quote: dict, composicao: dict | None) -> dict:
    """O quote com a composição (ações emitidas e os campos de k) do quote
    completo mais novo, quando ele próprio não a tem.

    O refresh diário (``ingest_ticker(full=False)``) pede o quote sem o módulo
    ``defaultKeyStatistics``. Sem isto, a unit cujo quote mais novo é o diário
    perdia k, e com ele o valor de mercado, o histórico anual e o lugar no P/L
    mediano. ``composicao`` é o ``defaultKeyStatistics`` desse quote completo;
    os três campos entram juntos, para a razão de k não misturar datas. O
    quote que tem a composição própria fica como está. Só serve à conta de
    ações: o LPA e os demais campos continuam os do próprio quote."""
    quote = quote or {}
    if tem_composicao(quote) or not composicao:
        return quote
    if not all(_f(composicao.get(c)) is not None for c in COMPOSICAO):
        return quote
    dks = dict(quote.get("defaultKeyStatistics") or {})
    dks.update({c: composicao[c] for c in COMPOSICAO})
    return {**quote, "defaultKeyStatistics": dks}


def valor_de_mercado(preco, acoes, por_titulo) -> float | None:
    """Preço do título × ações emitidas ÷ ações por título. None sem os três."""
    p, a, k = _positivo(preco), _positivo(acoes), _positivo(por_titulo)
    if p is None or a is None or k is None:
        return None
    return p * a / k


def valor_de_mercado_do_quote(quote: dict, *, unit: bool) -> float | None:
    """Valor de mercado do título no instante do quote.

    Sem ações emitidas, sem preço ou (na unit) sem k, cai no ``marketCap`` da
    brapi: é um número da companhia, na mesma convenção, só menos exato. A
    brapi não o dá para nenhuma unit de companhia medida, então na prática a
    unit sem k fica sem valor de mercado."""
    quote = quote or {}
    preco = _f(quote.get("regularMarketPrice"))
    mc = valor_de_mercado(preco, acoes_emitidas(quote), acoes_por_titulo(quote, unit=unit))
    if mc is not None:
        return mc
    return _positivo(quote.get("marketCap"))

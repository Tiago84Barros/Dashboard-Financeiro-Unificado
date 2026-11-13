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

**As ações emitidas são conferidas.** O ``sharesOutstanding`` da brapi às
vezes vem errado (MILS3 com 48.172 ações, BNBR3 com 12,3 mi, TFCO4 com 942
mi em 29/10/2026), e o valor de mercado herdava o erro: P/L ttm de 0,47 na
BNBR3 e de 66 na TFCO4, contra 3,8 e 10,4. ``acoes_emitidas`` confere a
contagem contra ``marketCap`` ÷ preço com a mesma regra do LPA do título
(``core.brapi.escolher_pela_referencia``, #727); k continua saindo da
contagem bruta (``_acoes_brutas``), porque é com ela que a brapi fez o
``trailingEps``.

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


def _acoes_brutas(quote: dict) -> float | None:
    """O ``sharesOutstanding`` como a brapi deu, sem conferência. É a base de
    k: o ``trailingEps`` da unit foi calculado com esta contagem."""
    quote = quote or {}
    dks = quote.get("defaultKeyStatistics") or {}
    return _positivo(dks.get("sharesOutstanding")) or _positivo(quote.get("sharesOutstanding"))


def _lucro(quote: dict):
    dks = (quote or {}).get("defaultKeyStatistics") or {}
    lucro = _f(dks.get("netIncomeToCommon"))
    if lucro is None:
        comp = (quote or {}).get(_COMPOSICAO_DO_COMPLETO)
        lucro = _f(comp.get("netIncomeToCommon")) if isinstance(comp, dict) else None
    return lucro


def acoes_emitidas(quote: dict) -> float | None:
    """Ações emitidas da companhia, todas as classes (igual em TAEE3/4/11),
    conferidas contra o ``marketCap`` da brapi.

    A contagem da brapi às vezes vem errada (29/10/2026: MILS3 com 48.172
    ações, BNBR3 com 12,3 mi, TFCO4 com 942 mi), e com ela o valor de
    mercado, e daí o P/L, o P/VP, o EV/EBIT e o P/FCO do ttm. A conferência é
    a mesma regra do LPA do título (``core.brapi.escolher_pela_referencia``,
    #727), do lado das ações, em títulos:

    - referência: ``marketCap`` ÷ preço;
    - próprio: ações brutas ÷ k (k da razão do próprio quote, 1 na ação);
    - alternativo: lucro ÷ ``earningsPerShare`` do topo (mesmo sinal).

    Escolhido o próprio, voltam as ações brutas; escolhido o alternativo,
    ele × k. Sem nenhum plausível, None: o valor de mercado cai no
    ``marketCap`` da brapi (``valor_de_mercado_do_quote``), que erra até
    ~35%, contra as contagens erradas, que erram de 2× a 5.000×. Sem
    ``marketCap`` (as units) não há referência e as ações ficam como vieram."""
    bruto = _acoes_brutas(quote)
    if bruto is None:
        return None
    quote = quote or {}
    preco, mcap = _positivo(quote.get("regularMarketPrice")), _positivo(quote.get("marketCap"))
    if preco is None or mcap is None:
        return bruto
    from core.brapi import escolher_pela_referencia

    k = acoes_por_titulo(quote, unit=True) or 1
    lucro, topo = _lucro(quote), _f(quote.get("earningsPerShare"))
    alternativo = (lucro / topo) if (lucro and topo and abs(lucro) >= 1.0) else None
    escolha = escolher_pela_referencia(bruto / k, alternativo, mcap / preco)
    if escolha is None:
        return None
    return bruto if escolha == bruto / k else escolha * k


# Tipos de ativo em que k não é 1 por suposição. ``other`` é o título de
# sufixo 11 que o normalizador não classificou (sem perfil e sem nome que o
# denuncie): pode ser unit, então k sai da razão ou fica desconhecido.
# Dado ausente permanece ausente, nunca vira 1.
TIPOS_COM_K = frozenset({"unit", "other"})

# Linhas spot que saem juntas da composição. O quote completo que não as
# emite (k não fecha) apaga as antigas: o upsert sozinho deixaria o k velho
# ao lado das ações novas.
METRICAS_DE_COMPOSICAO = ("marketCap", "Acoes_Emitidas", "Acoes_por_Titulo")


def exige_k(tipo, ticker: str = "") -> bool:
    """True quando o título não é 1 ação por suposição: unit, tipo
    desconhecido, ou, sem tipo gravado, sufixo 11."""
    if tipo is None or str(tipo) == "":
        return str(ticker or "").upper().endswith("11")
    return str(tipo) in TIPOS_COM_K


# Campos de ``defaultKeyStatistics`` de que saem as ações emitidas e k.
COMPOSICAO = ("sharesOutstanding", "trailingEps", "netIncomeToCommon")

# Chave reservada em que ``quote_com_composicao`` guarda a composição do
# quote completo mais novo. Fica fora de ``defaultKeyStatistics``: nada é
# fabricado nos campos do próprio quote.
_COMPOSICAO_DO_COMPLETO = "_composicao_do_completo"


def _composicao_valida(composicao) -> bool:
    return isinstance(composicao, dict) and all(
        _f(composicao.get(c)) is not None for c in COMPOSICAO)


def tem_composicao(quote: dict) -> bool:
    return _composicao_valida((quote or {}).get("defaultKeyStatistics") or {})


def composicao_conhecida(quote: dict, composicao: dict | None = None) -> bool:
    """True quando há os três campos de k juntos: os do próprio quote ou os
    do quote completo mais novo."""
    return tem_composicao(quote) or _composicao_valida(composicao)


def _k_da_razao(acoes, lpa_titulo, lucro) -> int | None:
    acoes, lpa_titulo, lucro = _positivo(acoes), _f(lpa_titulo), _f(lucro)
    if acoes is None or lpa_titulo is None or lucro is None or abs(lucro) < 1.0:
        return None
    k = acoes * lpa_titulo / lucro
    inteiro = round(k)
    if 1 <= inteiro <= MAX_ACOES_POR_TITULO and abs(k - inteiro) <= TOL_ACOES_POR_TITULO:
        return int(inteiro)
    return None


def acoes_por_titulo(quote: dict, *, unit: bool) -> int | None:
    """Quantas ações cada título carrega: 1 para ação; k para unit; None se
    a unit não deixa derivar k com segurança.

    k sai dos três campos de uma mesma fonte. Quando ``quote_com_composicao``
    anexou a composição do quote completo mais novo, k sai dela, inteira,
    mesmo que o quote traga ações emitidas ou LPA mais novos: as ações de uma
    data com o LPA de outra não dão a razão. O ``sharesOutstanding`` que a
    função empresta ao ``defaultKeyStatistics`` não conta como composição
    própria."""
    if not unit:
        return 1
    quote = quote or {}
    dks = quote.get("defaultKeyStatistics") or {}
    comp = quote.get(_COMPOSICAO_DO_COMPLETO)
    if _composicao_valida(comp):
        return _k_da_razao(comp["sharesOutstanding"], comp["trailingEps"],
                           comp["netIncomeToCommon"])
    return _k_da_razao(_acoes_brutas(quote), dks.get("trailingEps"),
                       dks.get("netIncomeToCommon"))


def quote_com_composicao(quote: dict, composicao: dict | None) -> dict:
    """O quote com a composição (ações emitidas e os campos de k) do quote
    completo mais novo, quando ele próprio não a tem.

    O refresh diário (``ingest_ticker(full=False)``) pede o quote sem o módulo
    ``defaultKeyStatistics``. Sem isto, a unit cujo quote mais novo é o diário
    perdia k, e com ele o valor de mercado, o histórico anual e o lugar no P/L
    mediano. ``composicao`` é o ``defaultKeyStatistics`` desse quote completo.

    - k sai dos três campos da composição, juntos, para a razão não misturar
      datas (``acoes_por_titulo``).
    - As ações emitidas do próprio quote, quando ele as traz (no
      ``defaultKeyStatistics`` ou no topo), são as mais novas e vencem: a
      composição só as empresta ao quote que não tem nenhuma.
    - O quote que tem a composição própria fica como está. O LPA e os demais
      campos continuam os do próprio quote."""
    quote = quote or {}
    if tem_composicao(quote) or not _composicao_valida(composicao):
        return quote
    comp = {c: composicao[c] for c in COMPOSICAO}
    out = {**quote, _COMPOSICAO_DO_COMPLETO: comp}
    if _acoes_brutas(quote) is None:
        dks = dict(quote.get("defaultKeyStatistics") or {})
        dks["sharesOutstanding"] = comp["sharesOutstanding"]
        out["defaultKeyStatistics"] = dks
    return out


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


# ── Ações e LPA do ano, para os múltiplos anuais ──────────────────────────────
# Teto dos títulos do ano (já × fator) contra os de hoje: acima dele, o LPA está
# na escala errada, pequeno demais (`normalize._divisores_lpa`). Só há teto:
# abaixo, a empresa emitiu depois (Oi, JFEN3, SAUD3), e o P/L da época é
# coerente sem as ações de hoje.
TETO_ACOES_DO_ANO = 5.0
# Faixa em que os títulos do ano, SEM o fator, coincidem com os de hoje: é o
# sinal de LPA reapresentado na base de hoje depois de um desdobramento.
FAIXA_BASE_DE_HOJE = (0.8, 1.25)
BASE_DA_EPOCA = "epoca"
BASE_DE_HOJE = "hoje"


def acoes_e_lpa_do_ano(*, lucro, lpa, por_titulo, acoes_atuais, fator_base=None
                       ) -> tuple[float, float, str] | None:
    """(títulos do ano, LPA por título, base), os dois na base DA ÉPOCA, ou None.

    Ponto único dos dois caminhos que fazem o múltiplo anual da B3:
    ``ingest.reprocess_metrics`` (``market.calculated_metrics``) e
    ``scripts/publish_valuation_historico.historico_b3`` (o histórico
    publicado). Quem chama multiplica os títulos pelo fechamento SEM ajuste do
    ano e divide esse fechamento pelo LPA devolvido.

    lucro ÷ LPA por ação dá as ações emitidas; ÷ k, títulos (a unit carrega k
    ações). ``fator_base`` = fechamento sem ajuste ÷ fechamento ajustado do
    mesmo ano: o fator acumulado dos desdobramentos e grupamentos posteriores.

    O LPA da base costuma vir na base do ano, mas nem sempre: alguns
    exercícios vêm reapresentados na base de hoje (CPLE3 2020, HETA3, SANB3).
    A regra (R3, medida em 12/11/2026 no universo inteiro):

    1. títulos × fator até ``TETO_ACOES_DO_ANO`` × os de hoje → base da
       época, como vieram (SBSP3 2024: 683 mi × 5 contra 3,52 bi de hoje).
       Não há piso: quem emitiu depois (Oi 2012, 0,005×) fica na época;
    2. acima do teto, o LPA reapresentado só é resgatado quando o fator é de
       desdobramento (> 1,25) e os títulos sem o fator caem em
       ``FAIXA_BASE_DE_HOJE`` dos de hoje: volta para a época (títulos ÷
       fator, LPA × fator). CPLE3 2020: 0,96× sem fator, 9,6× com ele;
    3. senão, None: o LPA está pequeno demais (LIGT3, MILS3, CTSA3, com
       100× a 10⁶× as ações de hoje) e o ano fica sem valuation.

    Sem títulos de hoje não há régua e vale a época. A R2 (faixa 0,2–5 nas
    duas bases) derrubava as empresas que emitiram depois e resgatava pela
    base de hoje, por coincidência, grupamentos com emissão (OIBR4 2012:
    P/L 7,6 virava 7 633). Limites conhecidos: quando a época passa no teto,
    vence a época, mesmo que o LPA estivesse na de hoje (LWSA3 2020, fator 4,
    fica com o P/VP 4× alto); LPA grande demais (P/L perto de zero) não é
    pego aqui. O fator de `historical_prices` carrega também o ajuste por
    provento (UGPA3 2015: 4,34 com desdobramento de 2), e por isso ele só
    resgata o ano que a época reprova.
    """
    k = _positivo(por_titulo)
    lucro, lpa = _f(lucro), _f(lpa)
    if k is None or not lucro or not lpa:
        return None
    titulos = lucro / lpa / k
    if titulos <= 0:
        return None
    lpa_titulo = lpa * k
    atuais, fator = _positivo(acoes_atuais), _positivo(fator_base)
    if atuais is None:
        return titulos, lpa_titulo, BASE_DA_EPOCA
    if titulos * (fator or 1.0) / atuais <= TETO_ACOES_DO_ANO:
        return titulos, lpa_titulo, BASE_DA_EPOCA
    lo, hi = FAIXA_BASE_DE_HOJE
    if (fator is not None and fator > hi
            and lo <= titulos / atuais <= hi):
        return titulos / fator, lpa_titulo * fator, BASE_DE_HOJE
    return None

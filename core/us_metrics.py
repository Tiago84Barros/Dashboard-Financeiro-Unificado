"""
core/us_metrics.py
Cálculo determinístico de métricas fundamentalistas dos EUA (puro, sem DB/rede).

Recebe séries anuais já normalizadas (colunas de market_us.*) e devolve um dict
de indicadores por empresa. Ausência NUNCA vira zero: divisão inválida → None
(rank neutro depois). Coberto por tests/test_us_metrics.py.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

_TAX_DEFAULT = 0.21  # alíquota corporativa federal EUA (aproximação p/ NOPAT)


def _f(v: Any) -> Optional[float]:
    """Coage para float, tolerando Decimal (NUMERIC do Postgres). None se inválido."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def safe_div(num: Any, den: Any) -> Optional[float]:
    """Divisão que preserva ausência: None se faltar dado ou denominador ~0.

    Coage os operandos a float — o warehouse devolve NUMERIC como Decimal, e
    float/Decimal levantaria TypeError.
    """
    n, d = _f(num), _f(den)
    if n is None or d is None or d == 0:
        return None
    return n / d


def div_if_den_positive(num: Any, den: Any) -> Optional[float]:
    """Como safe_div, mas exige denominador POSITIVO em vez de apenas != 0.

    Razão cujo denominador troca de sinal deixa de ser ordenável: ROE de lucro
    -50 sobre patrimônio -200 dá +25%, e passaria por rentabilidade boa; EV/EBIT
    com EBIT negativo dá um número negativo, que o ranqueador lê como o múltiplo
    mais barato do universo. Nesses casos o valor não é "ruim", é indefinido
    (n/m) — e ausência é o que o score já sabe tratar, reduzindo cobertura e
    confiança. Ver tests/test_score_sinal_de_denominador.py (achado A-101).

    O prejuízo em si não fica impune: margem líquida, ROA e earnings yield têm
    denominador sempre positivo (receita, ativo, valor de mercado) e continuam
    marcando o resultado negativo com o sinal certo.
    """
    n, d = _f(num), _f(den)
    if n is None or d is None or d <= 0:
        return None
    return n / d


def cagr(first: Optional[float], last: Optional[float], years: int) -> Optional[float]:
    """CAGR entre first e last em `years` períodos. None se inválido.

    Exige base positiva (crescimento composto não é definido com base <= 0).
    """
    if first is None or last is None or years <= 0:
        return None
    if first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def _latest(series: Sequence[dict], field: str) -> Optional[float]:
    """Ultimo ano com valor para o campo -- e NaN conta como ausencia.

    O `is not None` sozinho nao bastava, e a diferenca nao era teorica. O quadro
    de pontuacao chega por pandas (`load_scoring_frame` le em lote e faz
    `to_dict("records")`), onde NULL do Postgres vira `float('nan')`, nao
    `None`. O dossie chega por `dict(r._mapping)`, onde vira `None`. Com o
    guarda antigo, o mesmo CIK saia com `ebitda=nan` de um lado e derivado do
    outro -- e como `nan is None` e falso, TODA derivacao guardada por
    `is None` (EBITDA, FCL, divida liquida, capital investido, lucro bruto)
    ficava desligada no caminho que decide a nota.

    O efeito visivel: o portao de balanco quebrado (A-101) nunca disparava no
    snapshot. Medido em 30/08/2026, 21 empresas saiam `decision_grade` com
    `impairment_flags` gravado na propria linha -- o verificador e o escritor
    liam a mesma empresa e discordavam.
    """
    for row in reversed(series):
        v = _f(row.get(field))
        if v is not None:
            return v
    return None


def _series_values(series: Sequence[dict], field: str) -> list[tuple[int, float]]:
    out = []
    for row in series:
        v = _f(row.get(field))          # NaN e ausencia; ver _latest
        y = row.get("fiscal_year")
        if v is not None and y is not None and y == y:
            out.append((int(y), v))
    out.sort()
    return out


def symmetric_growth(first: Optional[float], last: Optional[float],
                     years: int) -> Optional[float]:
    """Crescimento anualizado definido também através do zero.

    ``(last - first) / (média dos módulos) / anos`` -- a taxa simétrica de
    Davis-Haltiwanger-Schuh, usada na literatura de dinâmica de firmas
    justamente porque atravessa a mudança de sinal. Fica limitada a
    ``±2/anos``, é monótona na melhora e não explode com base minúscula.

    Ela existe porque o CAGR APAGA a evidência em vez de pontuá-la. Lucro
    operacional, LPA e fluxo de caixa ficam negativos com frequência, e o
    CAGR não é definido com base ou ponta <= 0: a empresa que perdeu dinheiro
    quatro anos seguidos saía daqui como ``None``. Medido no armazém, isso
    atingia 1.159 das 1.976 empresas com par de anos para lucro operacional e
    1.289 das 2.271 para LPA -- a maioria, não a exceção. E ``None`` não é
    "cresceu pouco": é "não há dado", que derruba a COBERTURA e, por ela, a
    confiança. O prejuízo persistente é o dado mais eloquente que a empresa
    produziu, e era exatamente ele que sumia.
    """
    if first is None or last is None or years <= 0:
        return None
    escala = (abs(first) + abs(last)) / 2.0
    if escala == 0:
        return None
    return (last - first) / escala / years


_MIN_PONTOS_REGRESSAO = 3


def _ols(xs: Sequence[float], ys: Sequence[float]):
    """Inclinacao, intercepto e R2 de uma reta por minimos quadrados.

    Escrito a mao para nao arrastar scipy/sklearn para uma camada que e pura
    e roda dentro do publicador. None quando os x nao variam (reta vertical).
    """
    n = len(xs)
    if n < _MIN_PONTOS_REGRESSAO or n != len(ys):
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    syy = sum((y - my) ** 2 for y in ys)
    # Serie perfeitamente plana: a reta acerta tudo, mas nao ha variacao a
    # explicar. R2 = 1 aqui e convencao defensavel e evita 0/0.
    r2 = 1.0 if syy == 0 else 1.0 - sum(
        (y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys)) / syy
    return slope, intercept, r2


def _pontos_da_janela(series: Sequence[dict], field: str,
                      window: int) -> list[tuple[int, float]]:
    """TODOS os pontos anuais dentro da janela, nao so as duas pontas."""
    vals = _series_values(series, field)
    if not vals:
        return []
    limite = vals[-1][0] - window
    dentro = [(y, v) for y, v in vals if y >= limite]
    return dentro if len(dentro) >= _MIN_PONTOS_REGRESSAO else []


def tendencia_composta(pontos: Sequence[tuple[int, float]]):
    """Taxa composta anual pela INCLINACAO da regressao de ln(valor) no ano.

    Devolve (taxa, r2). A taxa e `exp(inclinacao) - 1`, entao le-se na mesma
    unidade de um CAGR e entra no mesmo limiar — o que muda e a estimativa.

    Por que trocar o CAGR ponta-a-ponta: ele usa DOIS pontos e descarta os
    demais, entao um ano atipico em qualquer das pontas define o numero
    sozinho. Uma empresa com receita 100, 180, 190, 200, 205 e outra com 100,
    102, 105, 110, 205 tem o MESMO CAGR e trajetorias que nada tem em comum;
    a inclinacao as separa. E o R2 diz se ha tendencia de fato: inclinacao
    alta com R2 baixo e ruido com cara de crescimento, e quem consome a
    metrica precisa poder ver isso.

    Exige todos os valores positivos (ln nao existe abaixo de zero) — o que e
    verdade para receita e nao e para lucro, dai `tendencia_simetrica`.
    """
    if len(pontos) < _MIN_PONTOS_REGRESSAO or any(v <= 0 for _, v in pontos):
        return None, None
    ajuste = _ols([float(y) for y, _ in pontos], [math.log(v) for _, v in pontos])
    if ajuste is None:
        return None, None
    slope, _intercepto, r2 = ajuste
    # Trava de sanidade: |slope| > 3 e crescimento de ~2.000% ao ano, que na
    # pratica so sai de erro de unidade na serie, nao de operacao real.
    if not (-3.0 <= slope <= 3.0):
        return None, None
    return math.exp(slope) - 1.0, r2


def tendencia_simetrica(pontos: Sequence[tuple[int, float]]):
    """Taxa anual pela inclinacao da regressao sobre os NIVEIS, normalizada
    pela media dos modulos. Devolve (taxa, r2).

    E a generalizacao de `symmetric_growth` para todos os pontos: mesma
    unidade (fracao da escala tipica por ano), mesma virtude de atravessar o
    zero — indispensavel para lucro operacional, LPA e FCL, que ficam
    negativos na maioria das empresas — e sem depender das duas pontas.
    """
    if len(pontos) < _MIN_PONTOS_REGRESSAO:
        return None, None
    escala = sum(abs(v) for _, v in pontos) / len(pontos)
    if escala == 0:
        return None, None
    ajuste = _ols([float(y) for y, _ in pontos], [v for _, v in pontos])
    if ajuste is None:
        return None, None
    slope, _intercepto, r2 = ajuste
    return slope / escala, r2


def _janela(series: Sequence[dict], field: str, window: int):
    """Par (base, ponta) e o vão em anos, ou None se a série não sustenta."""
    vals = _series_values(series, field)
    if len(vals) < 2:
        return None
    last_year, last_val = vals[-1]
    # procura o ponto ~window anos antes; senão usa o mais antigo disponível
    target_year = last_year - window
    base = None
    for y, v in vals:
        if y <= target_year:
            base = (y, v)
    if base is None:
        base = vals[0]
    span = last_year - base[0]
    if span <= 0:
        return None
    return base[1], last_val, span


def _growth(series: Sequence[dict], field: str, window: int) -> Optional[float]:
    janela = _janela(series, field, window)
    if janela is None:
        return None
    primeiro, ultimo, span = janela
    return cagr(primeiro, ultimo, span)


def _growth_simetrico(series: Sequence[dict], field: str,
                      window: int) -> Optional[float]:
    janela = _janela(series, field, window)
    if janela is None:
        return None
    primeiro, ultimo, span = janela
    return symmetric_growth(primeiro, ultimo, span)


def _tendencia(series: Sequence[dict], field: str, window: int, *,
               simetrica: bool = False):
    pontos = _pontos_da_janela(series, field, window)
    if not pontos:
        return None, None
    return (tendencia_simetrica(pontos) if simetrica
            else tendencia_composta(pontos))


DIVERGENCIA_MARKET_CAP = 10.0


ACOES_IMPLICITAS_MINIMAS = 100_000
RECEITA_SOBRE_MARKET_CAP_MAXIMA = 100.0


def market_cap_confiavel(publicado: Optional[float],
                         derivado: Optional[float],
                         preco: Optional[float] = None,
                         receita: Optional[float] = None) -> Optional[float]:
    """Valor de mercado quando as duas fontes concordam; None quando brigam.

    Ha duas fontes no armazem: `market_us.market_cap_history` (publicado) e
    o produto ultimo preco x acoes em circulacao (derivado). Em 08/09/2026
    elas concordam bem -- razao mediana 1,009 em 2.638 simbolos -- mas 54
    (2,1%) divergem por mais de uma ordem de grandeza, e a divergencia nao
    e ruido: a PSKY (Paramount Skydance) constava valendo 10.290 dolares, a
    SLXN 470 mil contra 1,1 bilhao derivado.

    Quando divergem assim, **nenhuma das duas se sustenta** -- nao ha como
    saber qual esta certa, e eleger o derivado seria trocar um numero
    inventado por outro. Entao devolve None e o valor de mercado vira
    lacuna declarada: `pe`, `pvp`, `p_s`, `dividend_yield` e
    `shareholder_yield` saem vazios em vez de saírem absurdos. O custo e
    2,1% de cobertura nesses campos; o beneficio e nao publicar dividend
    yield de 13.508% ao ano, que era o que a PSKY produzia.
    """
    if publicado is None or publicado <= 0:
        return derivado if derivado and derivado > 0 else None
    # Checagem que nao depende do derivado: o valor de mercado dividido pelo
    # preco da a contagem de acoes implicita, e ela tem piso conhecido. NYSE
    # e Nasdaq exigem mais de um milhao de acoes em circulacao publica, entao
    # cem mil ja e uma ordem de grandeza abaixo do minimo legal -- nao pega
    # listada legitima. Pega, sim, a CHWY com 101 acoes implicitas e a PSKY
    # com 947, que so o preco denunciava: essas duas nao tem
    # `shares_outstanding` no balanco, logo nao havia derivado para
    # confronta-las e a checagem de divergencia as deixava passar inteiras.
    if preco and preco > 0 and publicado / preco < ACOES_IMPLICITAS_MINIMAS:
        return None
    # Segunda checagem independente, pela receita. A contagem de acoes pega
    # o absurdo grosseiro, mas deixa passar quem tem acoes plausiveis e
    # valor de mercado em escala errada: a CHTR constava valendo 31 milhoes
    # com receita de 54,8 bilhoes, a ATHS 5 milhoes com receita de 25,7
    # bilhoes. Empresa nenhuma negocia por menos de 1% do que fatura num
    # ano -- nem em falencia, porque a receita e ativo negociavel. Um P/S
    # abaixo de 0,01 nao e acao barata, e unidade trocada na origem.
    if (receita and receita > 0
            and receita / publicado > RECEITA_SOBRE_MARKET_CAP_MAXIMA):
        return None
    if derivado is None or derivado <= 0:
        return publicado
    razao = publicado / derivado
    if razao > DIVERGENCIA_MARKET_CAP or razao < 1.0 / DIVERGENCIA_MARKET_CAP:
        return None
    return publicado


DIVERGENCIA_ACOES = 10.0


def acoes_em_circulacao(balance: Sequence[dict],
                        informado: Optional[float] = None) -> Optional[float]:
    """Ações do último exercício, ou None quando a série se contradiz.

    O exercício mais recente vem com a contagem em escala errada num punhado de
    empresas: a FLS publica 176.793 ações em 2025 contra 176.793.000 em cada um
    dos quatro anos anteriores — os mesmos dígitos, mil vezes menor — e a CMTV
    publica 15.924 contra 5.882.266. O número não é implausível isolado; só a
    série ao lado revela a escala trocada.

    Ele contamina tudo que é "por ação": sem esta guarda a FLS saía distribuindo
    620 dólares de dividendo por ação e a JOAN, 303.854.

    Um salto real de uma ordem de grandeza existe — grupamento de 1000:1 — e é
    raríssimo. Recusar os dois casos é a escolha conservadora: uma lacuna
    declarada é lida como lacuna, enquanto 620 dólares por ação é lido como
    fato. Com um único exercício não há série para contradizer, e o valor passa.

    A série coerente também erra: a GBL publica 26,70 em TODOS os exercícios —
    a contagem em milhões, sem contradição interna que a denuncie — e a HY
    chega aqui com 100. Contra isso vale o mesmo piso de
    ``ACOES_IMPLICITAS_MINIMAS`` usado no valor de mercado: as bolsas exigem um
    milhão de ações em circulação, e o piso está uma ordem de grandeza abaixo.
    """
    pontos = [(ano, v) for ano, v in _series_values(balance, "shares_outstanding")
              if v and v > 0]
    if len(pontos) >= 2:
        razao = pontos[-1][1] / pontos[-2][1]
        if razao > DIVERGENCIA_ACOES or razao < 1.0 / DIVERGENCIA_ACOES:
            return None
    valor = informado if informado is not None else (
        pontos[-1][1] if pontos else None)
    if valor is None or valor < ACOES_IMPLICITAS_MINIMAS:
        return None
    return valor


def crescimento_de_acoes(balance: Sequence[dict], window: int) -> Optional[float]:
    """Diluição pela contagem de ações, ou None quando a escala não fecha.

    Mesma série defeituosa de `acoes_em_circulacao`, e aqui o erro *premia*:
    a contagem mil vezes menor no último exercício vira -99,89% de crescimento,
    que o score lê como a maior recompra líquida do universo. O extremo oposto
    também aparece — o maior valor observado era +2.339.999%.

    Um fator de dez na janela não é diluição orgânica: é desdobramento (a série
    não é ajustada por split) ou escala trocada. Nos dois casos o número não
    responde a pergunta que o fator faz, e a resposta honesta é a lacuna.
    """
    janela = _janela(balance, "shares_outstanding", window)
    if janela is None:
        return None
    primeiro, ultimo, span = janela
    if not primeiro or not ultimo or primeiro <= 0 or ultimo <= 0:
        return None
    if min(primeiro, ultimo) < ACOES_IMPLICITAS_MINIMAS:
        return None
    razao = ultimo / primeiro
    if razao > DIVERGENCIA_ACOES or razao < 1.0 / DIVERGENCIA_ACOES:
        return None
    return cagr(primeiro, ultimo, span)


def _linha_do_ultimo_exercicio(series: Sequence[dict]) -> Optional[dict]:
    """A linha do maior `fiscal_year` da serie -- nao a ultima da lista.

    Existe para nao repetir a ordenacao implicita: `load_scoring_frame` traz
    `ORDER BY fiscal_year`, mas o dossie e os testes montam a serie a mao.
    """
    melhor: Optional[tuple[int, dict]] = None
    for row in series:
        y = row.get("fiscal_year")
        if y is None or y != y:                       # NaN nao e ano
            continue
        y = int(y)
        if melhor is None or y > melhor[0]:
            melhor = (y, row)
    return melhor[1] if melhor else None


def _dividendo_desembolsado(cashflow: Sequence[dict]) -> Optional[float]:
    """`dividends_paid` NO exercicio mais recente -- sem cair para anos antes.

    Deliberadamente NAO usa `_latest`, que percorre a serie de tras para
    frente ate achar valor. Para dividendo, cair de ano compara safras
    diferentes: o numerador vem de um exercicio antigo e o denominador
    (valor de mercado) e de hoje. Medido no armazem em 08/09/2026: 261
    simbolos tinham o `dividends_paid` mais recente defasado em 1 ano ou
    mais do ultimo exercicio publicado, e a QCOM -- que paga 3,62/acao --
    saia com 1,91 vindo de um exercicio antigo.
    """
    linha = _linha_do_ultimo_exercicio(cashflow)
    return None if linha is None else _f(linha.get("dividends_paid"))


def _dividend_yield(cashflow: Sequence[dict],
                    market_cap: Optional[float]) -> Optional[float]:
    """Dividendo do ultimo exercicio sobre o valor de mercado.

    Base: `dividends_paid` do fluxo de caixa (EDGAR) -- o que a empresa
    DESEMBOLSOU no exercicio, nao o que declarou por acao. Defasa a medida
    em ate um ano e ignora mudanca recente de politica; e o preco de usar so
    fonte propria, ja ingerida e sem restricao de licenca.

    **Ausencia devolve None, nunca 0.0.** A tentacao e obvia -- empresa que
    nao paga dividendo nao tem a linha, entao ausencia pareceria "nao paga"
    e a cobertura triplicaria. A hipotese foi testada contra
    `market_us.dividends` em 08/09/2026, restrita aos 1.383 simbolos que
    aquela tabela cobre, e REPROVOU: dos 303 com `dividends_paid` nulo no
    ultimo exercicio, 127 (42%) tinham pagado dividendo nos 12 meses. Nem o
    historico salva -- entre os que NUNCA tiveram a linha, 76 pagavam e 62
    nao. A ausencia aqui nao carrega informacao sobre pagamento; ela e
    lacuna de extracao, e grava-la como zero seria inventar a metade errada.

    Zero so sai quando o EDGAR publica zero explicito no exercicio.
    """
    if market_cap is None or market_cap <= 0:
        return None
    div = _dividendo_desembolsado(cashflow)
    return None if div is None else abs(div) / market_cap


def compute_company_metrics(
    income: Sequence[dict], balance: Sequence[dict], cashflow: Sequence[dict], *,
    price: Optional[float] = None, market_cap: Optional[float] = None,
    shares: Optional[float] = None,
) -> dict:
    """Deriva o snapshot de métricas de UMA empresa a partir das séries anuais.

    As séries vêm ordenadas por ano; usamos o último ano com dado para cada campo.
    market_cap pode ser dado direto ou derivado de price*shares.
    """
    # NUMERIC do Postgres chega como Decimal; coage os escalares externos a float
    # (há aritmética direta abaixo, não só safe_div).
    price, market_cap, shares = _f(price), _f(market_cap), _f(shares)
    revenue     = _latest(income, "revenue")
    gross       = _latest(income, "gross_profit")
    cogs        = _latest(income, "cost_of_revenue")
    gross_derived = False
    if gross is None and revenue is not None and cogs is not None:
        # Lucro bruto NAO e estimativa aqui: e receita menos custo, por
        # definicao. Quem tagueia os dois extremos e nao o subtotal deixava a
        # margem bruta ausente -- e ausencia nao e nota baixa, e queda de
        # COBERTURA da trilha de Qualidade, que barra a empresa por um numero
        # que os proprios demonstrativos dela ja continham. Medido no armazem:
        # 406 empresas cairam assim.
        gross = revenue - abs(cogs)
        gross_derived = True
    op_income   = _latest(income, "operating_income")
    ebit        = _latest(income, "ebit") or op_income
    ebitda      = _latest(income, "ebitda")
    depreciation = _latest(cashflow, "depreciation_and_amortization")
    ebitda_derived = False
    if ebitda is None and op_income is not None and depreciation is not None:
        ebitda = op_income + abs(depreciation)
        ebitda_derived = True
    net_income  = _latest(income, "net_income")
    interest    = _latest(income, "interest_expense")
    _latest(income, "eps")

    total_assets = _latest(balance, "total_assets")
    equity       = _latest(balance, "total_equity")
    total_debt   = _latest(balance, "total_debt")
    net_debt     = _latest(balance, "net_debt")
    cash         = _latest(balance, "cash_and_equivalents")
    cur_assets   = _latest(balance, "current_assets")
    cur_liab     = _latest(balance, "current_liabilities")
    invested_cap = _latest(balance, "invested_capital")
    # NÃO é `shares or _latest(...)`: a contagem informada vem da mesma
    # linha de balanço e carrega o mesmo defeito de escala — ver
    # `acoes_em_circulacao`, que confronta o último exercício com o anterior.
    shares_out   = acoes_em_circulacao(balance, shares)

    ocf   = _latest(cashflow, "operating_cash_flow")
    capex = _latest(cashflow, "capex")
    fcf   = _latest(cashflow, "free_cash_flow")
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf + capex  # capex vem negativo
    div_paid  = _latest(cashflow, "dividends_paid")
    _div_exercicio = _dividendo_desembolsado(cashflow)
    buyback   = _latest(cashflow, "stock_repurchase")
    issuance  = _latest(cashflow, "stock_issuance")
    sbc       = _latest(cashflow, "stock_based_compensation")

    # SBC é despesa real do acionista (paga em participação, não em caixa) que
    # o FCF GAAP devolve como se fosse ganho: sai do lucro e volta somada no
    # fluxo operacional. Sem esta linha, empresas que remuneram em ações
    # aparentam margem de caixa melhor do que a economia do negócio entrega.
    fcf_ex_sbc = None if fcf is None or sbc is None else fcf - abs(sbc)

    if market_cap is None and price is not None and shares_out is not None:
        market_cap = price * shares_out

    tend_rev5, r2_rev5 = _tendencia(income, "revenue", 5)
    tend_rev3, _r2_rev3 = _tendencia(income, "revenue", 3)
    tend_op, _r2_op = _tendencia(income, "operating_income", 3, simetrica=True)
    tend_eps, _r2_eps = _tendencia(income, "eps", 3, simetrica=True)
    tend_fcf, _r2_fcf = _tendencia(cashflow, "free_cash_flow", 3, simetrica=True)
    if net_debt is None and total_debt is not None and cash is not None:
        net_debt = total_debt - cash
    if invested_cap is None and equity is not None and total_debt is not None:
        invested_cap = equity + total_debt - (cash or 0.0)

    ev = None
    if market_cap is not None and total_debt is not None:
        ev = market_cap + total_debt - (cash or 0.0)

    nopat = None if ebit is None else ebit * (1 - _TAX_DEFAULT)

    m = {
        # Qualidade
        "gross_margin":     safe_div(gross, revenue),
        "operating_margin": safe_div(op_income, revenue),
        "net_margin":       safe_div(net_income, revenue),
        "fcf_margin":       safe_div(fcf, revenue),
        # Denominador precisa ser positivo: ver div_if_den_positive (A-101).
        "cash_conversion":  div_if_den_positive(fcf, net_income),
        "roe":              div_if_den_positive(net_income, equity),
        "roa":              safe_div(net_income, total_assets),
        "roic":             div_if_den_positive(nopat, invested_cap),
        # Crescimento. Receita continua em CAGR: ela não fica negativa, a taxa
        # composta é definida e é a leitura que o usuário reconhece. As três
        # abaixo ficam, e por isso mudaram de medida e de NOME -- ler "CAGR"
        # onde a conta é outra seria pior que a lacuna que isto corrige.
        "revenue_cagr_3y":  _growth(income, "revenue", 3),
        "revenue_cagr_5y":  _growth(income, "revenue", 5),
        "op_income_growth_3y": _growth_simetrico(income, "operating_income", 3),
        "eps_growth_3y":    _growth_simetrico(income, "eps", 3),
        "fcf_growth_3y":    _growth_simetrico(cashflow, "free_cash_flow", 3),
        # Crescimento por REGRESSAO: a inclinacao usa todos os anos da janela,
        # e nao so as duas pontas como o CAGR acima. Os `*_cagr_*` e
        # `*_growth_3y` continuam publicados — sao a serie que o historico e o
        # backtest ja gravaram, e apaga-los reescreveria o passado — mas quem
        # decide (us_score, roles) le os `*_trend_*`. O R2 acompanha porque
        # inclinacao sem aderencia e ruido com cara de tendencia.
        "revenue_trend_5y":    tend_rev5,
        "revenue_trend_r2_5y": r2_rev5,
        "revenue_trend_3y":    tend_rev3,
        "op_income_trend_3y":  tend_op,
        "eps_trend_3y":        tend_eps,
        "fcf_trend_3y":        tend_fcf,
        # Solidez
        "net_debt_ebitda":  div_if_den_positive(net_debt, ebitda),
        "interest_coverage": safe_div(ebit, abs(interest)) if interest else None,
        "current_ratio":    safe_div(cur_assets, cur_liab),
        "debt_to_equity":   div_if_den_positive(total_debt, equity),
        # Valuation
        "pe":            safe_div(market_cap, net_income),
        "earnings_yield": safe_div(net_income, market_cap),
        "ev_ebit":       safe_div(ev, ebit),
        "ev_ebitda":     safe_div(ev, ebitda),
        "p_fcf":         safe_div(market_cap, fcf),
        "fcf_yield":     safe_div(fcf, market_cap),
        "p_s":           safe_div(market_cap, revenue),
        # Qualidade dos lucros: peso da remuneração em ações e caixa livre
        # depois de absorvê-la (menor SBC/receita é melhor).
        "sbc_to_revenue":   safe_div(abs(sbc) if sbc is not None else None, revenue),
        "fcf_ex_sbc_margin": safe_div(fcf_ex_sbc, revenue),
        # Retorno ao acionista (buyback/dividendo vêm negativos no CF → sinal +)
        "shareholder_yield": _shareholder_yield(div_paid, buyback, issuance, market_cap),
        # Payout: distribuir acima do lucro não se sustenta. Em REIT é normal
        # (distribui FFO, e a depreciação deprime o lucro contábil) — quem
        # consome a métrica precisa tratar esse caso, ver us_advanced_lab.
        # Dividendo. Ver _dividend_yield: ausencia e None, nunca 0.0 — a
        # ausencia da linha no EDGAR nao distingue "nao paga" de "nao
        # extraido" (medido: 42% dos ausentes pagavam). Os dois campos usam
        # o exercicio MAIS RECENTE, nao o ultimo ano com valor, e por isso
        # divergem de payout_ratio/shareholder_yield, que seguem no `_latest`
        # historico para nao reescrever a serie ja publicada.
        "dividend_yield": _dividend_yield(cashflow, market_cap),
        "dividends_per_share": safe_div(
            abs(_div_exercicio) if _div_exercicio is not None else None,
            shares_out),
        "payout_ratio": (safe_div(abs(div_paid), net_income)
                         if div_paid is not None and net_income and net_income > 0
                         else None),
        # Diluição: recompra sem olhar a contagem de ações engana — a emissão
        # por SBC pode anular o buyback. Crescimento do share count: menor é
        # melhor (negativo = recompra líquida efetiva).
        "share_count_cagr_3y": crescimento_de_acoes(balance, 3),
        # Balanço/geração estruturalmente quebrados. Nasceu (A-101) como
        # portão: sem ele, as razões anuladas por div_if_den_positive chegavam
        # ao score como simples ausência, e ausência é puxada para o neutro.
        #
        # Desde 0.7.1 a indefinição sai do numerador E do denominador da
        # cobertura, então esse caminho não existe mais — e a marca deixou de
        # ser eliminatória em 0.8.0 (A-160). Ela mede estrutura de capital, não
        # defeito de dado: Lowe's, Altria e Cardinal Health têm patrimônio
        # negativo por recompra acumulada, cobertura 100% e confiança 100%, e
        # ficavam sem opinião por isso. O que a marca continua fazendo — e é a
        # parte que sempre foi verdadeira — é DIVULGAR que múltiplo sobre base
        # negativa não significa nada. Ver core/portfolio_report_us.py.
        "impairment_flags": tuple(
            nome for nome, quebrado in (
                ("patrimonio_liquido_negativo", equity is not None and equity <= 0),
                ("ebitda_nao_positivo", ebitda is not None and ebitda <= 0),
                ("capital_investido_negativo",
                 invested_cap is not None and invested_cap <= 0),
            ) if quebrado
        ),
        # Razoes que NAO EXISTEM, em vez de faltar. `div_if_den_positive`
        # anula a razao quando o denominador MEDIDO e <= 0 -- e isso esta
        # certo, porque razao cujo denominador troca de sinal deixa de ser
        # ordenavel. O problema era o que acontecia depois: o resultado chegava
        # ao score como ausencia, e ausencia derruba COBERTURA, que e o numero
        # que barra decision_grade. A empresa deficitaria era punida duas vezes
        # pelo mesmo prejuizo -- uma no rank, puxada ao neutro, e outra na
        # cobertura, por um dado que ela ENTREGOU.
        #
        # E a mesma correcao que a trilha de crescimento recebeu em 0.6.0
        # (prejuizo persistente entrava como falta de dado). So ficou seguro
        # fazer aqui depois de 0.7.0: o portao de balanco quebrado (A-101)
        # voltou a disparar, e ele trava exatamente a empresa que produz estas
        # indefinicoes. Com o portao morto, isentar a cobertura teria aberto
        # caminho para a pior empresa sair com selo de decisao.
        "nm_metrics": tuple(
            nome for nome, indefinida in (
                ("cash_conversion",
                 fcf is not None and net_income is not None and net_income <= 0),
                ("roe",
                 net_income is not None and equity is not None and equity <= 0),
                ("roic",
                 nopat is not None and invested_cap is not None
                 and invested_cap <= 0),
                ("net_debt_ebitda",
                 net_debt is not None and ebitda is not None and ebitda <= 0),
                ("debt_to_equity",
                 total_debt is not None and equity is not None and equity <= 0),
            ) if indefinida
        ),
        # contexto (não entram no score, ajudam classificação/dossiê)
        "_revenue": revenue, "_net_income": net_income, "_fcf": fcf,
        "_equity": equity, "_net_debt": net_debt, "_market_cap": market_cap,
        "_ebit": ebit, "_ebitda": ebitda, "_ebitda_derived": ebitda_derived,
        "_gross_derived": gross_derived,
        "_years": len(_series_values(income, "revenue")),
    }
    return m


def _shareholder_yield(div_paid, buyback, issuance, market_cap) -> Optional[float]:
    if market_cap is None or market_cap == 0:
        return None
    parts = [abs(x) for x in (div_paid, buyback) if x is not None]
    if not parts:
        return None
    returned = sum(parts) - (abs(issuance) if issuance is not None else 0.0)
    return returned / market_cap


# métricas em que MENOR é melhor (para o ranqueamento no score)
LOWER_IS_BETTER = frozenset({
    "net_debt_ebitda", "debt_to_equity", "pe", "ev_ebit", "ev_ebitda", "p_fcf", "p_s",
    # SBC pesada corrói o acionista; share count crescente é diluição.
    "sbc_to_revenue", "share_count_cagr_3y",
})

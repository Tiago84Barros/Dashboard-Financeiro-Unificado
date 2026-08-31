"""
core/us_metrics.py
Cálculo determinístico de métricas fundamentalistas dos EUA (puro, sem DB/rede).

Recebe séries anuais já normalizadas (colunas de market_us.*) e devolve um dict
de indicadores por empresa. Ausência NUNCA vira zero: divisão inválida → None
(rank neutro depois). Coberto por tests/test_us_metrics.py.
"""
from __future__ import annotations

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
    shares_out   = shares or _latest(balance, "shares_outstanding")

    ocf   = _latest(cashflow, "operating_cash_flow")
    capex = _latest(cashflow, "capex")
    fcf   = _latest(cashflow, "free_cash_flow")
    if fcf is None and ocf is not None and capex is not None:
        fcf = ocf + capex  # capex vem negativo
    div_paid  = _latest(cashflow, "dividends_paid")
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
        "payout_ratio": (safe_div(abs(div_paid), net_income)
                         if div_paid is not None and net_income and net_income > 0
                         else None),
        # Diluição: recompra sem olhar a contagem de ações engana — a emissão
        # por SBC pode anular o buyback. Crescimento do share count: menor é
        # melhor (negativo = recompra líquida efetiva).
        "share_count_cagr_3y": _growth(balance, "shares_outstanding", 3),
        # Balanço/geração estruturalmente quebrados. Sem isto, as razões
        # anuladas por div_if_den_positive chegariam ao score como simples
        # ausência — e ausência é puxada para o neutro, o que premiaria a
        # empresa em pior situação. Ver us_score.score_cross_section (A-101).
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

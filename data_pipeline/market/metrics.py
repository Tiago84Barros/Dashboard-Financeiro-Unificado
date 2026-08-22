"""
data_pipeline/market/metrics.py
Cálculo de indicadores derivados a partir das demonstrações + preço + dividendos
de market.* (sem rede). Núcleo PURO e testável.

Snapshot (último ano disponível) → calculated_metrics (period='ttm', year=0):
  Margem_Liquida, Margem_Operacional, ROE, ROA, ROIC, Endividamento_Total,
  P/L, P/VP, EV_EBIT, P_FCO, DY, Payout.

Cada valor passa pela faixa coerente de core.data_quality (fora da faixa → omitido,
nunca grava lixo). Usa as mesmas convenções do ETL legado (safe_div, escala decimal).

Além dos indicadores, emite SINAIS (0/1) para condições que a faixa coerente
descartava e que não são ausência de dado, e sim veredito: Patrimonio_Negativo e
Endividamento_Fora_De_Faixa. Ver core.data_quality.SIGNAL_RANGES para o porquê.
"""
from __future__ import annotations

import core.data_quality as _dq


def _safe_div(a, b):
    a = _dq.to_float(a)
    b = _dq.to_float(b)
    if a is None or b is None or abs(b) < 1e-12:
        return None
    return a / b


# fórmula/insumos de cada indicador (para auditoria e clareza)
def compute_snapshot(f: dict) -> dict[str, tuple[float, str]]:
    """
    f: insumos do ÚLTIMO ano + mercado:
       net_income, revenue, ebit, ebitda, total_assets, equity, cash,
       gross_debt, net_debt, fco (op. cash flow), market_cap;
       div_ttm (dividendos POR AÇÃO, 12m), price (último preço), eps (LPA) —
       DY e Payout usam base por ação (os dividendos da BRAPI são por ação,
       enquanto market_cap/net_income são absolutos: misturar daria ~0).
    Retorna {indicador: (valor, metodo)} apenas com valores válidos na faixa coerente.
    """
    ni  = f.get("net_income")
    rev = f.get("revenue")
    ebit = f.get("ebit")
    ta  = f.get("total_assets")
    eq = f.get("equity")
    cash = f.get("cash")
    gd  = f.get("gross_debt")
    nd = f.get("net_debt")
    fco = f.get("fco")
    mc  = f.get("market_cap")
    div_ps = f.get("div_ttm")
    price = f.get("price")
    eps = f.get("eps")
    ca  = f.get("current_assets")
    cl = f.get("current_liabilities")

    inv_capital = None
    if all(_dq.to_float(x) is not None for x in (eq, gd)):
        inv_capital = _dq.to_float(eq) + _dq.to_float(gd) - (_dq.to_float(cash) or 0.0)
    # Denominador NEGATIVO inverte o sinal do quociente e transforma desastre em
    # destaque. Medido em 30/07/2026: das 45 empresas com patrimônio negativo,
    # 32 exibiam ROE POSITIVO — prejuízo dividido por patrimônio negativo dá
    # retorno positivo, e a faixa de ROE (-3, 5) aceita numericamente. RAIZ4
    # (Raízen) tinha ROE de +3,28 com prejuízo de R$ 27 bi e patrimônio de
    # R$ -8,3 bi: para o ranking, retorno de 328% sobre o capital próprio.
    # Aqui não há valor a salvar — a razão é indefinida, e o veredito quem dá é
    # o sinal Patrimonio_Negativo emitido abaixo.
    if inv_capital is not None and inv_capital <= 0:
        inv_capital = None
    eq_para_razao = eq if (_dq.to_float(eq) or 0.0) > 0 else None
    ev = None
    if mc is not None and nd is not None:
        ev = _dq.to_float(mc) + _dq.to_float(nd)

    # SINAIS de balanço estruturalmente rompido. Vêm ANTES da faixa coerente
    # porque é justamente ela que apagava a informação: dívida/PL com patrimônio
    # negativo dá razão negativa, cai fora de [0, 20] e o indicador some. Quem
    # consome não distinguia "insolvente" de "sem balanço". Medido em 30/07/2026:
    # 37 tickers com patrimônio negativo e 4 com razão fora de faixa estavam
    # invisíveis assim. Sinal é 1.0 = a condição existe; ausente = não existe ou
    # não há balanço para dizer.
    eqf, gdf, fcof = _dq.to_float(eq), _dq.to_float(gd), _dq.to_float(fco)
    signals: dict[str, tuple[float, str]] = {}

    # FCO ≤ 0: a operação queima caixa. P_FCO = market_cap/fco fica negativo,
    # a faixa (0.01, 200) rejeita, e o resultado era NULL. Como P_FCO é insumo
    # CRÍTICO da rota de valor, essas empresas caíam em "sem evidência" — o
    # estado reservado a quem não tem dado, não a quem tem o pior dado
    # possível. Efeito colateral grave, medido em 30/07/2026: a regra
    # "FCO negativo: p_fco < 0" de core/b3_value_route.py NUNCA disparou em
    # nenhuma empresa, porque valor negativo jamais chega ao banco (0 de 3.066
    # linhas de P_FCO são negativas, mínimo 0,0135). Eram 71 empresas invisíveis.
    #
    # CONFIRMAÇÃO POR PREJUÍZO é obrigatória, e isso não é cautela genérica: FCO
    # negativo com lucro é rotina contábil em setores inteiros. Banco tem saída
    # operacional por originação de crédito; transmissora de energia sob IFRIC 12
    # reconhece a contraprestação da concessão em INVESTIMENTO, não em operação.
    # Medido em 30/07/2026: das 84 empresas com FCO ≤ 0, 31 (37%) tinham EBIT E
    # lucro positivos — e o setor mais representado era Financeiro, com 21.
    # ISAE4 (transmissora, EBIT de R$ 4,1 bi e lucro de R$ 2,5 bi) seria
    # reprovada e a carteira perderia justamente o contrapeso defensivo.
    # A confirmação olha o LUCRO LÍQUIDO, não o EBIT. Exigir os dois (a versão
    # anterior pedia lucro > 0 E ebit > 0) fazia a métrica inválida vetar a
    # válida: banco não tem EBIT no sentido industrial, e a brapi devolve o
    # campo negativo mesmo assim. Medido em 01/08/2026, ABCB4 (Banco ABC Brasil)
    # fechou 2025 com lucro de R$ 1,0 bi e FCO POSITIVO de R$ 150 mi, e ainda
    # assim carregava o sinal — porque o EBIT reportado era -R$ 456 mi.
    #
    # Lucro positivo com FCO negativo é ambíguo, não conclusivo: pode ser
    # originação de crédito, IFRIC 12 ou capital de giro. Ambíguo vira ATENÇÃO
    # pelos outros caminhos, nunca CRÍTICO por este sinal. EBIT só decide quando
    # não há lucro líquido para consultar.
    nif, ebitf = _dq.to_float(ni), _dq.to_float(ebit)
    lucrativa = (nif > 0) if nif is not None else (ebitf is not None and ebitf > 0)
    if fcof is not None and fcof <= 0 and not lucrativa:
        signals["FCO_Negativo"] = (
            1.0, "operating_cash_flow <= 0 confirmado por prejuízo (lucro líquido)")

    if eqf is not None and eqf < 0:
        signals["Patrimonio_Negativo"] = (1.0, "equity < 0")
    elif eqf is not None and gdf is not None and eqf > 0:
        razao = gdf / eqf
        lo, hi = _dq.CANONICAL_RANGES["Endividamento_Total"]
        if (lo is not None and razao < lo) or (hi is not None and razao > hi):
            signals["Endividamento_Fora_De_Faixa"] = (
                1.0, f"gross_debt/equity={razao:.1f} fora de [{lo:g}, {hi:g}]")

    candidates = {
        "Margem_Liquida":      (_safe_div(ni, rev),  "net_income/revenue"),
        "Margem_Operacional":  (_safe_div(ebit, rev), "ebit/revenue"),
        "ROE":                 (_safe_div(ni, eq_para_razao), "net_income/equity"),
        "ROA":                 (_safe_div(ni, ta),   "net_income/total_assets"),
        "ROIC":                (_safe_div(ebit, inv_capital), "ebit/(equity+gross_debt-cash)"),
        "Endividamento_Total": (_safe_div(gd, eq),   "gross_debt/equity"),
        "Liquidez_Corrente":   (_safe_div(ca, cl),   "current_assets/current_liabilities"),
        "P/L":                 (_safe_div(mc, ni),   "market_cap/net_income"),
        "P/VP":                (_safe_div(mc, eq),   "market_cap/equity"),
        "EV_EBIT":             (_safe_div(ev, ebit), "(market_cap+net_debt)/ebit"),
        "P_FCO":               (_safe_div(mc, fco),  "market_cap/operating_cash_flow"),
        "DY":                  (_safe_div(div_ps, price), "dividendos_ps_12m/preco"),
        "Payout":              (_safe_div(div_ps, eps),   "dividendos_ps_12m/LPA"),
    }
    out: dict[str, tuple[float, str]] = dict(signals)
    for name, (val, method) in candidates.items():
        if val is None:
            continue
        if _dq.is_valid_value(name, val):   # respeita faixa coerente; descarta absurdos
            out[name] = (round(float(val), 8), method)
    return out


# Múltiplos de valuation por ANO derivados de ações em circulação ATUAIS
# (não temos ações históricas) — aproximação válida em janelas curtas; recebem
# confiança menor. Fundamentais (margens/ROE/ROA/ROIC/Endiv/Liquidez) e DY são
# exatos das demonstrações/preço e mantêm confiança cheia.
ANNUAL_APPROX = frozenset({"P/L", "P/VP", "EV_EBIT", "P_FCO", "Payout"})


def to_metric_rows(ticker: str, snapshot: dict[str, tuple[float, str]],
                   confidence: float = 85.0, *, period: str = "ttm", year: int = 0,
                   low_conf: frozenset[str] | set[str] | None = None,
                   low_conf_value: float = 60.0) -> list[dict]:
    """
    Converte o snapshot em linhas para market.calculated_metrics.
    low_conf: métricas que recebem confiança reduzida (ex.: valuation anual via
    ações atuais aproximadas). O método ganha o sufixo '~aprox' p/ rastreio.
    """
    low_conf = low_conf or frozenset()
    rows = []
    for name, (val, method) in snapshot.items():
        approx = name in low_conf
        rows.append({
            "ticker": ticker, "period": period, "year": year, "quarter": 0,
            "metric_name": name, "metric_value": val,
            "calculation_method": (method + " ~aprox" if approx else method),
            "source": "market.compute",
            "confidence_score": low_conf_value if approx else confidence,
        })
    return rows

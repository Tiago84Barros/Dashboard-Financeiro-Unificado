"""
data_pipeline/market/normalize.py
Normalizadores PUROS: convertem o payload da BRAPI (results[0]) nas linhas das
tabelas market.* — sem rede e sem banco (100% testável).

Defensivo por design: a forma exata dos módulos do plano Pro pode variar, então
cada campo é extraído tentando MÚLTIPLOS nomes candidatos e cai para None quando
ausente (nunca quebra, nunca inventa).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def _f(v):
    """float seguro (None em falha/NaN/inf)."""
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _first(d: dict, names: tuple[str, ...]):
    """Primeiro valor numérico presente entre os nomes candidatos."""
    if not isinstance(d, dict):
        return None
    for n in names:
        if n in d and d[n] is not None:
            x = _f(d[n])
            if x is not None:
                return x
    return None


def _as_date(v):
    """Converte unix/ISO/'YYYY-MM-DD' em date (ou None)."""
    if v is None:
        return None
    # unix timestamp
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return datetime.fromtimestamp(int(v), tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        pass
    s = str(v)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:len("2026-08-20T03:00:00.000Z")], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _ticker(quote: dict) -> str:
    return str((quote or {}).get("symbol") or "").strip().upper().replace(".SA", "")


def _end_year_quarter(end, annual: bool):
    """(period_end_date, year, quarter) a partir do endDate; None se inválido."""
    d = _as_date(end)
    if d is None:
        return None
    if annual:
        return d, d.year, 0
    return d, d.year, (d.month - 1) // 3 + 1


# ── Cadastro ──────────────────────────────────────────────────────────────────

def company_row(quote: dict) -> dict | None:
    tk = _ticker(quote)
    if not tk:
        return None
    prof = (quote or {}).get("summaryProfile") or {}
    name = (quote.get("longName") or quote.get("shortName") or prof.get("longName") or tk)
    return {
        "codigo_cvm": None,  # preenchido depois via cvm_to_ticker (CVM)
        "name": str(name)[:300],
        "cnpj": None,
        "sector": prof.get("sector"),
        "subsector": prof.get("industry") or prof.get("industryDisp"),
        "segment": prof.get("industryKey") or prof.get("sectorKey"),
        "website": prof.get("website"),
        "description": prof.get("longBusinessSummary"),
        "logo_url": quote.get("logourl") or quote.get("logoUrl"),
    }


def _infer_asset_type(tk: str, quote: dict) -> str:
    """Classifica o tipo de ativo a partir do payload + sufixo do ticker.

    Fix auditoria 2026-07: antes era gravado 'stock' fixo para todo payload
    (inclusive units e BDRs), sobrescrevendo classificações corretas no
    upsert. Ordem: perfil do payload (FII/ETF) → sufixo numérico do ticker.
    """
    sector = str(((quote or {}).get("summaryProfile") or {}).get("sector") or "")
    if sector.strip().lower() == "fundos imobiliários":
        return "fii"
    name = str((quote or {}).get("longName") or (quote or {}).get("shortName") or "")
    # Sufixo numérico FINAL (a classe), não todos os dígitos do ticker: "B3SA3"
    # tem o "3" da raiz (B3), e "".join(dígitos) daria "33" -> caía em BDR.
    digits = ""
    for ch in reversed(tk):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    if digits in ("31", "32", "33", "34", "35", "36", "39"):
        return "bdr"
    if digits == "11":
        low = name.lower()
        if (
            "fii" in low or "fdo inv imob" in low
            or "fundo investimento imobili" in low
            or "fundo de investimento imobili" in low
        ):
            return "fii"
        if (
            "etf" in low or "índice" in low or "indice" in low
            or "ishares" in low or "it now" in low or low.startswith("investo ")
            or "bovespa" in low or "ibovespa" in low
        ):
            return "etf"
        if (
            " unit " in f" {low} " or "ctf de deposito de acoes" in low
            or "cons of" in low
        ):
            return "unit"
        # O sufixo 11 sozinho não comprova unit. Sem evidência de companhia,
        # mantém como other e o vínculo CVM/taxonomia decide a elegibilidade.
        if not (quote or {}).get("summaryProfile"):
            return "other"
        return "unit"
    return "stock"


def asset_row(quote: dict) -> dict | None:
    tk = _ticker(quote)
    if not tk:
        return None
    return {
        "ticker": tk,
        "asset_type": _infer_asset_type(tk, quote),
        "exchange": "B3",
        "currency": str(quote.get("currency") or "BRL"),
        # is_active continua sem fonte confiável no payload da brapi (ativo
        # delistado simplesmente some da API); mantido True por design —
        # a exclusão de deslistados é feita via core/survivorship.py.
        "is_active": True,
    }


# ── Preços ────────────────────────────────────────────────────────────────────

def price_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    for it in (quote or {}).get("historicalDataPrice") or []:
        d = _as_date(it.get("date"))
        if d is None:
            continue
        close = _f(it.get("close"))
        # Candle vazio (mês sem negociação de ativo ilíquido): a brapi devolve
        # a data sem OHLC. Sem close não há informação de preço — não gravar
        # placeholder (auditoria 2026-07: 1.526 linhas nulas em FSRF11 etc.).
        if close is None or close <= 0:
            continue
        out.append({
            "ticker": tk, "date": d,
            "open": _f(it.get("open")), "high": _f(it.get("high")),
            "low": _f(it.get("low")), "close": close,
            "adjusted_close": _f(it.get("adjustedClose")),
            "volume": int(_f(it.get("volume")) or 0) if it.get("volume") is not None else None,
        })
    return out


# ── Demonstrações ─────────────────────────────────────────────────────────────

def _statement_items(quote: dict, annual_key: str, quarterly_key: str) -> list[tuple[dict, bool]]:
    items: list[tuple[dict, bool]] = []
    for it in (quote.get(annual_key) or []):
        if isinstance(it, dict):
            items.append((it, True))
    for it in (quote.get(quarterly_key) or []):
        if isinstance(it, dict):
            items.append((it, False))
    return items


# Lucro líquido: alguns bancos (ITUB4, BPAC11) vêm sem netIncome e com o lucro
# só nos campos de operações continuadas / atribuível.
_LUCRO = ("netIncome", "netIncomeCommonStockholders",
          "netIncomeFromContinuingOps", "netIncomeApplicableToCommonShares")
# Lucro usado para o LPA implícito (lucro / ações): o atribuível aos
# controladores é o numerador do LPA da DFP.
_LUCRO_LPA = ("netIncomeApplicableToCommonShares", "netIncome",
              "netIncomeFromContinuingOps", "netIncomeCommonStockholders")
# Campos de LPA que a brapi copia da DFP multiplicados pela ESCALA_MOEDA.
_LPA_ON = ("basicEarningsPerCommonShare", "dilutedEarningsPerCommonShare")
_LPA_PN = ("basicEarningsPerPreferredShare", "dilutedEarningsPerPreferredShare")
# Campos já em reais por ação.
_LPA_REAIS = ("earningsPerShare", "basicEarningsPerShare", "dilutedEarningsPerShare")
# O LPA bruto de cada exercício sai em 1000**k vezes o da DFP: k=0 em UNIDADE
# (VIVA3), 1 em MIL (PETR4 8540), 2 em MIL e por lote de mil ações (ITUB4 2019
# 2.780.000) e -1 em BRSR3/6 de 2010 a 2014 (0,0024). lucro / LPA bruto é a
# quantidade de ações implícita vezes 1000**-k, e a quantidade de ações de um
# exercício para o vizinho só muda por desdobramento, grupamento ou emissão —
# 1:3 da SBSP3 em 2013, 1:8 da MGLU3 em 2017, 10:1 dela em 2024 —, nunca por
# 1000. Daí o k de cada exercício: o que deixa as ações implícitas mais perto
# das do exercício seguinte, em log10.
_ESCALAS = (-1, 0, 1, 2)
_TOL_ESCALA = 0.5      # âncora: o exercício recente fecha com lucro / ações de hoje
# Troca de k só quando a escala do vizinho deixaria as ações mudarem mais de
# ~56x (log10 1,75) num ano: grupamento de 15:1 junto com a passagem de MIL a
# UNIDADE (HAPV3 2024, razão 1,89) troca; base 40x menor (CEED3 2016, 1,6),
# não. Nenhum k abaixo disso: LPA lixo (AMAR3 2021, -2,7e16), que não vira
# referência para os exercícios anteriores.
_TOL_TROCA = 1.75
# k=-1 e k=2 são raros e só valem com encaixe justo (_TOL_ESCALA): BRSR3 2014,
# CBEE3 2012 e ITUB4 2019 fecham com o vizinho a menos de 0,1; a ENEV3 2015
# (ações 93x as de 2016, que teve grupamento de 25:1) caía em k=-1 com 1,03 e
# a PETZ3 2024 (31.350, lixo) em k=2 com 0,95.
_ESCALAS_RARAS = (-1, 2)
_TOL_VIZINHOS = 0.15   # os dois vizinhos concordam entre si (log10)...
_TOL_PICO = 0.3        # ... e o exercício foge dos dois por mais de 2x: lixo


def _nao_zero(d: dict, names: tuple[str, ...]):
    """Como _first, mas 0 conta como ausente (a brapi grava 0 no lugar de nulo)."""
    for n in names:
        x = _f((d or {}).get(n))
        if x:
            return x
    return None


def _vazio(it: dict) -> bool:
    """Demonstração devolvida com todos os números em 0 (TIMS3 2024/2025): é
    ausência do provedor, não resultado zero."""
    nums = [v for k, v in it.items()
            if k not in ("endDate", "date") and isinstance(v, (int, float))
            and not isinstance(v, bool)]
    return bool(nums) and not any(nums)


def _classe_pn(tk: str) -> bool:
    return tk[-1:] in ("4", "5", "6", "7", "8")


def _lpa_bruto(it: dict, pn: bool):
    """(valor, escalado). Classe do ticker primeiro, a outra como reserva (VALE3
    tem 0 no ordinário e o LPA no preferencial); depois os campos em reais."""
    v = _nao_zero(it, (_LPA_PN + _LPA_ON) if pn else (_LPA_ON + _LPA_PN))
    if v is not None:
        return v, True
    return _nao_zero(it, _LPA_REAIS), False


def _acoes(quote: dict):
    dks = (quote or {}).get("defaultKeyStatistics") or {}
    return _nao_zero(dks, ("sharesOutstanding",)) or _nao_zero(quote, ("sharesOutstanding",))


def _divisores_lpa(items: list[tuple[dict, bool]], pn: bool, acoes) -> dict | None:
    """{id(demonstração): divisor do LPA bruto, ou None se o LPA é lixo}.

    None (a função toda) quando não há base de ações confiável: sem ações ou
    quando o exercício mais recente não fecha com lucro / ações de hoje —
    emissão maciça (AZUL3) quebra o implícito, e aí vale a regra antiga (campo
    escalado ÷ 1000, a maioria está em MIL).

    Âncora: o exercício anual mais recente com LPA escalado e lucro, ou um
    trimestre posterior a ele — a SBSP3 desdobrou 1:5 em abr/2026 e a DFP de
    2025 saiu na base antiga (razão 5); o ITR de 2026 já fecha. A referência
    começa nas ações de hoje, e dali para trás cada demonstração fica com o k
    (ver _ESCALAS) do exercício anual seguinte enquanto as ações implícitas não
    fugirem mais de _TOL_TROCA da referência; só então vale o k que deixa as
    ações mais perto. Assim desdobramento vira mudança de base e não de escala
    — razão 4,5 da MGLU3 em 2015-16 é MIL com 1:8 e 1:4 depois, não lote de
    mil —, e k=-1 ou k=2 só entra com encaixe justo (_ESCALAS_RARAS). Nenhum k
    serve: LPA nulo, e a referência não se move. Campo em reais só admite k=0.
    Trimestres não movem a referência (lucro pequeno, razão ruidosa). Fica nulo
    o LPA de sinal oposto ao do lucro e o do exercício cujas ações implícitas
    fogem das dos dois vizinhos, que concordam entre si: é pico isolado, não
    mudança de base (BRSR3 2020, 310.665).

    Limite: grupamento ou emissão de ~56x ou mais no mesmo ano em que a escala
    muda é indistinguível de troca de k (ENEV3 2015) e vira nulo.
    """
    if not acoes:
        return None
    ordem = sorted(((it, a) for it, a in items if not _vazio(it)),
                   key=lambda p: (str(p[0].get("endDate") or p[0].get("date")), p[1]),
                   reverse=True)

    def razao(it):
        bruto, escalado = _lpa_bruto(it, pn)
        lucro = _nao_zero(it, _LUCRO_LPA)
        if bruto is None or lucro is None:
            return bruto, escalado, None
        return bruto, escalado, (math.log10(abs(lucro / bruto)) if bruto * lucro > 0 else False)

    hoje = math.log10(acoes)
    ref = k_atual = None
    for it, anual in ordem:
        _, escalado, n = razao(it)
        if not escalado or n is None or n is False:
            continue
        k = min(_ESCALAS[1:], key=lambda k: abs(n + 3 * k - hoje))
        if abs(n + 3 * k - hoje) < _TOL_ESCALA:
            # a referência é a das ações de hoje, não a do trimestre: o LPA do
            # ITR pode vir acumulado no ano (AVLL3 2T26, -7,08 = 1S26 / ações)
            ref, k_atual = hoje, k
            break
        if anual:
            break
    if ref is None:
        return None

    div: dict = {}
    anuais: list[tuple[int, float, int]] = []
    for it, anual in ordem:
        bruto, escalado, n = razao(it)
        if bruto is None:
            continue
        if n is False:
            div[id(it)] = None
            continue
        if n is None:
            div[id(it)] = 1000.0 ** k_atual if escalado else 1.0
            continue
        ks = ((k_atual, *sorted(_ESCALAS, key=lambda k: abs(n + 3 * k - ref)))
              if escalado else (0,))
        k = next((k for k in ks if abs(n + 3 * k - ref)
                  <= (_TOL_ESCALA if k in _ESCALAS_RARAS else _TOL_TROCA)), None)
        if k is None:
            div[id(it)] = None
            continue
        div[id(it)] = 1000.0 ** k
        if anual:
            ref = n + 3 * k
            k_atual = k if escalado else k_atual
            anuais.append((id(it), ref, k))
    for (_, a, ka), (i, b, kb), (_, c, kc) in zip(anuais, anuais[1:], anuais[2:]):
        if (ka == kc != kb and abs(a - c) <= _TOL_VIZINHOS
                and min(abs(b - a), abs(b - c)) > _TOL_PICO):
            div[i] = None
    return div


def income_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    pn = _classe_pn(tk)
    items = _statement_items(quote, "incomeStatementHistory", "incomeStatementHistoryQuarterly")
    divisores = _divisores_lpa(items, pn, _acoes(quote))
    out: list[dict] = []
    for it, annual in items:
        peq = _end_year_quarter(it.get("endDate") or it.get("date"), annual)
        if not peq:
            continue
        period_end, year, quarter = peq
        eps = _eps(it, pn, divisores)
        if _vazio(it):
            # Linha com tudo nulo (não pular): o upsert sobrescreve os zeros já
            # gravados em vez de deixá-los como fóssil.
            it = {}
        out.append({
            "ticker": tk, "period": "annual" if annual else "quarterly",
            "year": year, "quarter": quarter,
            # point-in-time (migração 019): fim do exercício + proveniência
            # (raw_payload_id preenchido pelo ingest ao salvar o payload)
            "period_end_date": period_end, "raw_payload_id": None,
            "revenue": _first(it, ("totalRevenue", "revenue", "operatingRevenue")),
            "gross_profit": _first(it, ("grossProfit",)),
            "ebit": _first(it, ("ebit", "operatingIncome")),
            "ebitda": _first(it, ("ebitda", "normalizedEBITDA")),
            "net_income": _first(it, _LUCRO),
            "eps": eps,
        })
    return out


def _eps(it: dict, pn: bool = False, divisores: dict | None = None):
    """
    LPA do exercício, em reais por ação e na base de ações da DFP (desdobramento
    posterior não entra: o preço da fita na data também não o tem). Divisor da
    demonstração em ``divisores`` (ver _divisores_lpa); sem base confiável,
    campo escalado ÷ 1000 e campo em reais como está. 0/None => None.
    """
    bruto, escalado = _lpa_bruto(it, pn)
    if bruto is None:
        return None
    if divisores is None:
        value = bruto / 1000.0 if escalado else bruto
    else:
        d = divisores.get(id(it), 1.0)
        if d is None:
            return None
        value = bruto / d
    # Alguns payloads históricos da BRAPI trazem sentinelas/erros de escala
    # (ex.: 2,7e16 por ação). Ausência é preferível a contaminar a linha
    # inteira ou exceder NUMERIC(20,6). O limite é deliberadamente amplo.
    if abs(value) > 1_000_000:
        return None
    return value


def balance_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    for it, annual in _statement_items(quote, "balanceSheetHistory", "balanceSheetHistoryQuarterly"):
        peq = _end_year_quarter(it.get("endDate") or it.get("date"), annual)
        if not peq:
            continue
        period_end, year, quarter = peq
        cash = _first(it, ("cash", "cashAndCashEquivalents",
                           "cashCashEquivalentsAndShortTermInvestments"))
        # dívida bruta: campos diretos quando existem; senão soma dos componentes
        # de empréstimos/debêntures/arrendamento (padrão CVM da brapi p/ B3).
        gross_debt = _first(it, ("totalDebt", "shortLongTermDebt", "shortLongTermDebtTotal"))
        if gross_debt is None:
            parts = [_first(it, (k,)) for k in (
                "loansAndFinancing", "longTermLoansAndFinancing",
                "debentures", "longTermDebentures",
                "leaseFinancing", "longTermLeaseFinancing")]
            parts = [p for p in parts if p is not None]
            gross_debt = sum(parts) if parts else None
        net_debt = _first(it, ("netDebt",))
        if net_debt is None and gross_debt is not None and cash is not None:
            net_debt = gross_debt - cash
        out.append({
            "ticker": tk, "period": "annual" if annual else "quarterly",
            "year": year, "quarter": quarter,
            # point-in-time (migração 019): fim do exercício + proveniência
            # (raw_payload_id preenchido pelo ingest ao salvar o payload)
            "period_end_date": period_end, "raw_payload_id": None,
            "total_assets": _first(it, ("totalAssets",)),
            "total_liabilities": _first(it, ("totalLiab", "totalLiabilities",
                                             "totalLiabilitiesNetMinorityInterest")),
            "equity": _first(it, ("totalStockholderEquity", "shareholdersEquity",
                                  "stockholdersEquity", "controllerShareholdersEquity",
                                  "totalEquityGrossMinorityInterest")),
            "cash": cash, "gross_debt": gross_debt, "net_debt": net_debt,
            # ativo/passivo circulante p/ Liquidez_Corrente (PC: total* costuma vir
            # nulo na B3 → fallback p/ currentLiabilities).
            "current_assets": _first(it, ("totalCurrentAssets", "currentAssets")),
            "current_liabilities": _first(it, ("totalCurrentLiabilities", "currentLiabilities")),
        })
    return out


def cashflow_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    for it, annual in _statement_items(quote, "cashflowHistory", "cashflowHistoryQuarterly"):
        peq = _end_year_quarter(it.get("endDate") or it.get("date"), annual)
        if not peq:
            continue
        period_end, year, quarter = peq
        fco = _first(it, ("totalCashFromOperatingActivities", "operatingCashFlow"))
        capex = _first(it, ("capitalExpenditures", "capitalExpenditure"))
        fcf = _first(it, ("freeCashFlow",))
        if fcf is None and fco is not None and capex is not None:
            fcf = fco + capex  # capex já vem negativo no padrão Yahoo/brapi
        out.append({
            "ticker": tk, "period": "annual" if annual else "quarterly",
            "year": year, "quarter": quarter,
            # point-in-time (migração 019): fim do exercício + proveniência
            # (raw_payload_id preenchido pelo ingest ao salvar o payload)
            "period_end_date": period_end, "raw_payload_id": None,
            "operating_cash_flow": fco,
            "investing_cash_flow": _first(it, ("totalCashflowsFromInvestingActivities", "investingCashFlow")),
            "financing_cash_flow": _first(it, ("totalCashFromFinancingActivities", "financingCashFlow")),
            "capex": capex, "free_cash_flow": fcf,
        })
    return out


# ── Dividendos ────────────────────────────────────────────────────────────────

def _sane_payment_date(pay, ex):
    """
    Anula payment_date claramente impossível para o event_date gerado
    (COALESCE(payment_date, ex_date)) não herdar lixo da brapi: ano além do
    próximo ano (placeholder 9999 ou futuros 2028 quando estamos em 2026).
    Datas de pagamento atrasadas no passado (JCP a pagar) são legítimas e ficam
    intactas; o ex_date assume o event_date quando o payment é anulado.
    """
    if pay is None:
        return None
    try:
        if pay.year > datetime.now(timezone.utc).year + 1:
            return None
    except Exception:
        return None
    return pay


# Uma unidade da última casa de market.dividends.amount, numeric(18,6). Abaixo
# disto o valor depende da regra de arredondamento do banco para sobreviver ao
# CHECK de positividade, e nenhum provento real vale menos de um milionésimo de
# real — o piso é uma unidade cheia, não meia, para não depender disso.
AMOUNT_MINIMO = 0.000001


def dividend_rows(quote: dict) -> list[dict]:
    tk = _ticker(quote)
    out: list[dict] = []
    # Dedup por classe/fonte: em multi-classe (CEBR5/6, BRSR5/6...) a brapi
    # mescla no feed de cada classe as linhas de TODAS as classes (fonte CSV
    # secundária, remarks não-vazio) — sem o dedup a soma 12m dobra e o DY
    # infla ~2x (ver core.brapi.dedup_cash_dividends).
    from core.brapi import dedup_cash_dividends
    items = dedup_cash_dividends(
        ((quote or {}).get("dividendsData") or {}).get("cashDividends") or [])
    for it in items:
        amount = _f(it.get("rate"))
        # O piso é a precisão da COLUNA, não a do Python. market.dividends.amount
        # é numeric(18,6): a brapi devolve resíduos como rate=1e-10, que passam
        # em `> 0` aqui, chegam ao banco arredondados para 0.000000 e violam
        # chk_dividends_amount_positive. Como ingest_ticker grava o ticker
        # inteiro numa transação só, a violação derrubava também o PREÇO —
        # PETR4 não atualizava por causa de um provento de 2006 valendo 1e-10.
        if amount is None or amount < AMOUNT_MINIMO:
            continue
        ex = _as_date(it.get("lastDatePrior") or it.get("approvedOn"))
        out.append({
            "ticker": tk,
            "payment_date": _sane_payment_date(_as_date(it.get("paymentDate")), ex),
            "ex_date": ex,
            "amount": amount,
            "type": str(it.get("label") or "DIVIDENDO")[:40],
            "source": "brapi.dev",
        })
    return out


# ── Indicadores (calculated_metrics) ──────────────────────────────────────────

def metric_rows(quote: dict) -> list[dict]:
    """Indicadores 'spot' fornecidos/derivados pela brapi (P/L, P/VP, EV/EBITDA, marketCap)."""
    tk = _ticker(quote)
    if not tk:
        return []
    dks = (quote or {}).get("defaultKeyStatistics") or {}
    fin = (quote or {}).get("financialData") or {}
    # DY trailing da própria brapi (consenso) — evita over-count da janela
    # de dividendos somada (ex.: SAPR3 cluster de JCP em junho). Em multi-
    # classe (CEBR5/6...), porém, o consenso da brapi soma TODAS as classes
    # e infla ~2x: quando excede 1,5x a soma própria 12m (pós-dedup por
    # classe), descarta — nunca grava lixo em calculated_metrics/vintages.
    dy_spot = _first(dks, ("dividendYield", "yield"))
    if dy_spot is not None:
        from core.brapi import trailing_dy
        dy_proprio = trailing_dy(quote)
        if dy_proprio and dy_spot > dy_proprio * 1.5:
            dy_spot = None
    cand = {
        "P/L": _first(quote, ("priceEarnings",)) or _first(dks, ("trailingPE", "forwardPE")),
        "P/VP": _first(dks, ("priceToBook",)),
        "EV/EBITDA": _first(dks, ("enterpriseToEbitda", "enterpriseValueEbitda")),
        "marketCap": _first(quote, ("marketCap",)),
        "LPA": _first(quote, ("earningsPerShare",)) or _first(dks, ("trailingEps",)),
        "ROE": _first(fin, ("returnOnEquity",)),
        "ROA": _first(fin, ("returnOnAssets",)),
        "Margem_Liquida": _first(fin, ("profitMargins",)),
        "Margem_Operacional": _first(fin, ("operatingMargins",)),
        "DY": dy_spot,
    }
    rows: list[dict] = []
    for name, val in cand.items():
        if val is None:
            continue
        rows.append({
            "ticker": tk, "period": "spot", "year": 0, "quarter": 0,
            "metric_name": name, "metric_value": val,
            "calculation_method": "brapi_field",
            "source": "brapi.dev", "confidence_score": 80.0,
        })
    return rows


def normalize_all(quote: dict) -> dict[str, list[dict]]:
    """Converte um payload completo em todas as linhas market.* (dicionário por tabela)."""
    comp = company_row(quote)
    ast = asset_row(quote)
    return {
        "companies": [comp] if comp else [],
        "assets": [ast] if ast else [],
        "historical_prices": price_rows(quote),
        "income_statements": income_rows(quote),
        "balance_sheets": balance_rows(quote),
        "cash_flow_statements": cashflow_rows(quote),
        "dividends": dividend_rows(quote),
        "calculated_metrics": metric_rows(quote),
    }

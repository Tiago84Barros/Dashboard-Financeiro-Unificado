"""
data_pipeline/us/edgar_facts.py
Tradução XBRL (SEC EDGAR companyfacts) → linhas do schema market_us.*.

Por que EDGAR: dados públicos, oficiais e de domínio público — sem licença
restritiva, sem cláusula de deleção e sem limite de exibição (a FMP proíbe cópia/
armazenamento e exige apagar tudo ao cancelar; ver docs/empresas_americanas.md).

Três diferenças importantes em relação à FMP, tratadas aqui:

  1. CONCEITOS: o mesmo número aparece sob tags diferentes conforme a empresa
     (ex.: receita como Revenues ou RevenueFromContractWithCustomer...). Resolvido
     por lista de aliases em ordem de prioridade.
  2. SINAIS: no XBRL, saídas de caixa são POSITIVAS (capex = pagamento). O resto
     do projeto segue a convenção capex negativo (fcf = cfo + capex). As tags de
     pagamento são negadas aqui, uma única vez, na fronteira.
  3. PIT: cada fato traz `filed` — a data em que o filing ficou público. É a
     melhor `available_at` possível (mais rigorosa que o acceptedDate da FMP).

Puro (sem rede/DB). Coberto por tests/test_us_edgar_facts.py.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from data_pipeline.us.normalize import content_hash, parse_date, to_float

logger = logging.getLogger(__name__)

_USD = "USD"
_SHARES = "shares"
_USD_PER_SHARE = "USD/shares"

# Faixa plausível de exercício fiscal. Alguns filers (ex.: PRTH, TNET) gravam
# o `fy` do XBRL como serial de data estilo Excel (43465 = 2018-12-31); a SEC
# repassa o valor cru no companyfacts.
_FY_MIN, _FY_MAX = 1990, 2100
_EXCEL_SERIAL_MIN, _EXCEL_SERIAL_MAX = 20000, 80000
_EXCEL_EPOCH = date(1899, 12, 30)

# Duração aceita como "anual" (10-K com exercícios de ~12 meses).
_ANNUAL_MIN_DAYS, _ANNUAL_MAX_DAYS = 350, 380
_QUARTERLY_MIN_DAYS, _QUARTERLY_MAX_DAYS = 70, 110
# v8: PL do controlador (A6), mais aliases de divida e dividendo (A7/A8) e
# contagem de acoes sem `CommonStockSharesIssued` cru, com a capa `dei` (A2/A4).
PARSER_VERSION = "companyfacts-parser-v8"

# ── Mapas de conceitos (ordem = prioridade) ───────────────────────────────────
INCOME_CONCEPTS: dict[str, list[str]] = {
    # `SalesRevenueGoodsNet`/`ServicesNet` sao pre-ASC 606 e fecham a lacuna que
    # sobrou do A-148: a Compass Minerals publica `Revenues` = 0 de 2011 a 2013 e
    # nenhum dos quatro aliases seguintes -- o valor real (US$ 1.105,7 mi em 2011)
    # so existe sob `SalesRevenueGoodsNet`. Com a regra do zero que cede, entram
    # apenas onde os anteriores faltam ou vem zerados. O corte de duracao anual
    # descarta o Q4 que compartilha o mesmo `end` do exercicio.
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "SalesRevenueGoodsNet", "SalesRevenueServicesNet"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "gross_profit": ["GrossProfit"],
    "rnd_expenses": ["ResearchAndDevelopmentExpense"],
    "sga_expenses": ["SellingGeneralAndAdministrativeExpense",
                     "GeneralAndAdministrativeExpense"],
    "operating_income": ["OperatingIncomeLoss"],
    "interest_expense": ["InterestExpense", "InterestExpenseDebt"],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
}
# ── Receita de instituicao financeira ────────────────────────────────────────
# `RevenueFromContractWithCustomerExcludingAssessedTax` encabeca a lista acima e,
# para um banco, captura APENAS as tarifas sob ASC 606 -- nao a receita de juros,
# que e a maior parte. O resultado nao era ausencia, era numero errado: 48 das 83
# financeiras com receita gravada tinham margem liquida acima de 100% (AUBN com
# receita de US$ 619 mil e lucro de US$ 7,2 milhoes). Isso alimentava p_s e as
# margens da trilha de qualidade com um denominador minusculo.
#
# A convencao do setor -- e a que os proprios grandes bancos publicam como "total
# revenue, net of interest expense" -- e juros LIQUIDOS mais receita nao-juros.
# Verificado contra a SEC: RBB 6,18x -> 0,24x, MRBK 3,46x -> 0,19x, TCBK 2,27x ->
# 0,29x, e no Citigroup, onde a receita gravada ja estava correta, a formula
# chega ao mesmo numero (0,18x contra 0,17x).
#
# A deteccao e estrutural, pelo proprio XBRL, e nao pelo cadastro de setor: quem
# reporta juros liquidos ou receita nao-juros e intermediario financeiro. Uma
# processadora de pagamentos (USIO) nao reporta, e mantem a receita generica.
BANK_REVENUE_CONCEPTS: dict[str, list[str]] = {
    "_revenues_net_of_interest": ["RevenuesNetOfInterestExpense"],
    "_net_interest_income": ["InterestIncomeExpenseNet",
                             "InterestIncomeExpenseAfterProvisionForLoanLoss"],
    "_noninterest_income": ["NoninterestIncome"],
}
_BANK_AUX_FIELDS = tuple(BANK_REVENUE_CONCEPTS)

# A-142: tags que SO um intermediario financeiro publica. O A-138 qualificava o
# filer por `InterestIncomeExpenseNet`, e essa tag nao discrimina nada: e a linha
# de resultado financeiro liquido, publicada por qualquer empresa com caixa ou
# divida. Medido contra a SEC em 20 companhias, ela aparece em 10 das 12
# NAO-financeiras (AMD, ADSK, MDT, CNP, YUMC, GME, MANH, JKHY, CASY, SIG); as
# tres tags abaixo aparecem em 8 de 8 bancos e em 0 das 12 nao-financeiras.
#
# O estrago era do tamanho do erro de qualificacao: a AMD ficou com receita de
# US$ 215 milhoes (a real e ~US$ 25,8 bilhoes -- o numero gravado era o juro do
# caixa) e a Autodesk com receita NEGATIVA de US$ 82,4 milhoes, que e despesa
# financeira liquida. Nao e receita subestimada; e outra grandeza no lugar dela.
_BANK_MARKER_TAGS = (
    "RevenuesNetOfInterestExpense",
    "NoninterestIncome",
    "InterestIncomeExpenseAfterProvisionForLoanLoss",
    "InterestAndDividendIncomeOperating",
)


def _e_intermediario_financeiro(cf: dict) -> bool:
    """Qualifica o filer pelo XBRL, no nivel da COMPANHIA, nao da linha.

    Por companhia e nao por exercicio de proposito: um banco que num ano nao
    reporta `NoninterestIncome` continua banco, e alternar a definicao de receita
    ao longo da serie produziria CAGR e margens comparando grandezas diferentes.
    """
    return any(_entries(cf, tag, _USD) for tag in _BANK_MARKER_TAGS)


def _bank_revenue(row: dict[str, Any]) -> Optional[float]:
    """Receita da intermediacao financeira, ou None se a linha nao a sustenta.

    So e chamada para filer ja qualificado por `_e_intermediario_financeiro`.

    Substitui a receita generica em vez de so preencher lacuna: metade das
    financeiras JA tinha um numero, e era ele o defeito. Preencher o vazio e
    deixar o errado produziria duas definicoes de receita dentro do mesmo grupo
    de ranking -- o percentil de p_s e calculado intra-industria.
    """
    explicita = row.get("_revenues_net_of_interest")
    if explicita is not None:
        return explicita
    juros = row.get("_net_interest_income")
    tarifas = row.get("_noninterest_income")
    if juros is None and tarifas is None:
        return None
    return (juros or 0.0) + (tarifas or 0.0)


def _aplicar_receita_bancaria(rows: list[dict], cf: dict, symbol: str | None) -> None:
    """Substitui a receita nas linhas, so quando o filer e um banco."""
    banco = _e_intermediario_financeiro(cf)
    for r in rows:
        substituta = _bank_revenue(r) if banco else None
        if substituta is not None and substituta < 0:
            # Receita negativa nao existe. Se a formula chega la, o insumo nao e
            # o que se supos -- manter a generica e menos errado que publicar
            # um denominador negativo em p_s e nas margens.
            logger.warning("edgar_facts: %s receita bancaria negativa (%s); "
                           "mantendo a receita generica", symbol or "?", substituta)
            substituta = None
        if substituta is not None:
            r["revenue"] = substituta
        for aux in _BANK_AUX_FIELDS:
            r.pop(aux, None)


INCOME_SHARE_CONCEPTS = {
    "eps": (["EarningsPerShareBasic"], _USD_PER_SHARE),
    "eps_diluted": (["EarningsPerShareDiluted"], _USD_PER_SHARE),
    "weighted_shares": (["WeightedAverageNumberOfSharesOutstandingBasic",
                         "WeightedAverageNumberOfSharesOutstanding"], _SHARES),
    "weighted_shares_diluted": (["WeightedAverageNumberOfDilutedSharesOutstanding"], _SHARES),
}

BALANCE_CONCEPTS: dict[str, list[str]] = {
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue",
                             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "short_term_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent"],
    "current_assets": ["AssetsCurrent"],
    "total_assets": ["Assets"],
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    # A7: a ORCL tagueia so `LongTermNotesPayable`/`NotesPayableCurrent`; sem estes
    # aliases a divida saia nula e a empresa parecia ter caixa liquido. Cada campo
    # usa o PRIMEIRO alias com dado no periodo (nao soma aliases), entao acrescentar
    # no fim nao dobra a contagem dentro do campo. `CommercialPaper` fica por ultimo
    # porque `ShortTermBorrowings` costuma ja inclui-lo.
    "short_term_debt": ["LongTermDebtCurrent", "ShortTermBorrowings", "DebtCurrent",
                        "NotesPayableCurrent",
                        "LongTermDebtAndCapitalLeaseObligationsCurrent",
                        "CommercialPaper"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt",
                       "LongTermNotesPayable",
                       "LongTermDebtAndCapitalLeaseObligations"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "total_liabilities": ["Liabilities"],
    # A6: o lucro (`NetIncomeLoss`) e o do CONTROLADOR; o PL tem de ser o mesmo
    # perimetro, ou ROE e P/VPA ficam errados (KKR: 30,9 B contra 78,9 B com NCI).
    # O PL com minoritarios so entra na falta do outro (sem NCI os dois coincidem).
    # Nao ha coluna para guardar o PL com NCI sem migration: ver o PR.
    "total_equity": ["StockholdersEquity",
                     "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    # PL consolidado (com minoritarios), guardado a parte SO para a identidade
    # ativo = passivo + PL e para o capital investido do ROIC. ROE e P/VPA leem
    # `total_equity` (controlador). Coluna: migration 087.
    "total_equity_incl_nci": [
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
}
# Total combinado de divida (curto + longo). Nao e campo do schema: so vale
# quando NENHUMA das partes existe, senao somaria em dobro. Auxiliar descartado
# em `_aplicar_divida_combinada`, no mesmo molde dos auxiliares bancarios.
BALANCE_AUX_CONCEPTS: dict[str, list[str]] = {
    "_debt_combined": ["DebtLongtermAndShorttermCombinedAmount"],
}

# A2/A4: contagem de acoes. Ordem de prioridade (ver `_acoes_em_circulacao`):
#   1. dei:EntityCommonStockSharesOutstanding (capa do filing; soma as classes);
#   2. us-gaap:CommonStockSharesOutstanding (data do balanco);
#   3. CommonStockSharesIssued menos acoes em tesouraria do MESMO periodo.
# `Issued` cru inclui a tesouraria (JPM: 4,10 bi contra ~2,7 bi em circulacao).
_SHARES_OUTSTANDING_TAGS = ["CommonStockSharesOutstanding"]
_SHARES_ISSUED_TAGS = ["CommonStockSharesIssued"]
_SHARES_TREASURY_TAGS = ["TreasuryStockShares", "TreasuryStockCommonShares"]
_DEI_SHARES_TAG = "EntityCommonStockSharesOutstanding"
# Fatos instantaneos do balanco que ancoram "qual e o fim do balanco deste filing".
_BALANCE_ANCHOR_TAGS = ("Assets", "LiabilitiesAndStockholdersEquity",
                        "StockholdersEquity", "Liabilities")

CASHFLOW_CONCEPTS: dict[str, list[str]] = {
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired"],
    "investments": ["PaymentsToAcquireInvestments"],
    "stock_issuance": ["ProceedsFromIssuanceOfCommonStock"],
    "stock_repurchase": ["PaymentsForRepurchaseOfCommonStock"],
    "debt_issuance": ["ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromNotesPayable"],
    "debt_repayment": ["RepaymentsOfLongTermDebt", "RepaymentsOfDebt"],
    # A8: a BAC tagueia so `DividendsCommonStockCash`. Esse e o dividendo
    # DECLARADO/baixado do PL (positivo), nao o pagamento do fluxo de caixa:
    # entra por ultimo, e a negacao de NEGATE_FIELDS vale igual para ele.
    "dividends_paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
                       "DividendsCommonStockCash", "DividendsCommonStock"],
    "stock_based_compensation": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "depreciation_and_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipmentAndIntangibleAssets",
        "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        "Depreciation",
    ],
}

# Campos cujo XBRL é "pagamento" (positivo) e que o projeto guarda NEGATIVO,
# para casar com a convenção usada no resto do código (fcf = cfo + capex).
NEGATE_FIELDS = frozenset({"capex", "acquisitions", "investments", "stock_repurchase",
                           "debt_repayment", "dividends_paid"})


def _is_annual_duration(start: Optional[str], end: str) -> bool:
    """True para fatos instantâneos (sem start) ou durações de ~12 meses."""
    if not start:
        return True
    d0, d1 = parse_date(start), parse_date(end)
    if d0 is None or d1 is None:
        return False
    return _ANNUAL_MIN_DAYS <= (d1 - d0).days <= _ANNUAL_MAX_DAYS


def _is_quarterly_duration(start: Optional[str], end: str) -> bool:
    """Aceita fatos instantâneos ou duração de aproximadamente três meses."""
    if not start:
        return True
    d0, d1 = parse_date(start), parse_date(end)
    if d0 is None or d1 is None:
        return False
    return _QUARTERLY_MIN_DAYS <= (d1 - d0).days <= _QUARTERLY_MAX_DAYS


def _valid_filing_timing(end: Any, filed: Any) -> bool:
    """Rejeita fatos cujo período termina depois da própria divulgação."""
    period_end, filing_date = parse_date(end), parse_date(filed)
    return bool(period_end and filing_date and filing_date >= period_end)


def _entries(cf: dict, tag: str, unit: str) -> list[dict]:
    facts = (cf or {}).get("facts", {})
    for taxonomy in ("us-gaap", "ifrs-full", "dei"):
        node = facts.get(taxonomy, {}).get(tag)
        if node:
            return node.get("units", {}).get(unit, []) or []
    return []


def _annual_points(entries: list[dict]) -> dict[str, dict]:
    """{period_end: {val, filed}} de 10-K, escolhendo o filing MAIS ANTIGO.

    O 10-K de 2023 traz 2022 como comparativo; o original de 2022 foi arquivado
    antes. Pegar o `filed` mais antigo por período dá a data em que o número
    ficou conhecível pela primeira vez — que é o que o PIT exige.
    """
    by_end: dict[str, dict] = {}
    for e in entries:
        if not str(e.get("form") or "").startswith("10-K"):
            continue
        end, filed = e.get("end"), e.get("filed")
        if not end or not filed or not _valid_filing_timing(end, filed):
            continue
        if not _is_annual_duration(e.get("start"), end):
            continue
        cur = by_end.get(end)
        if cur is None or str(filed) < str(cur["filed"]):
            by_end[end] = {"val": e.get("val"), "filed": filed}
    return by_end


def _sane_fiscal_year(fy: Any, end: Any) -> int:
    """Ano fiscal validado; serial-Excel é convertido; resto cai no ano do `end`."""
    from datetime import timedelta
    try:
        year = int(fy)
    except (TypeError, ValueError):
        year = 0
    if _FY_MIN <= year <= _FY_MAX:
        return year
    if _EXCEL_SERIAL_MIN <= year <= _EXCEL_SERIAL_MAX:
        return (_EXCEL_EPOCH + timedelta(days=year)).year
    d = parse_date(end)
    return d.year if d else 0


def _quarterly_points(entries: list[dict], *, instant: bool = False) -> dict[tuple[int, int], dict]:
    """Fatos 10-Q trimestrais por (fiscal_year, fiscal_quarter), sempre PIT.

    Fluxos acumulados de seis/nove meses são rejeitados pela duração. Isso reduz
    cobertura, mas evita tratar YTD como trimestre isolado.
    """
    out: dict[tuple[int, int], dict] = {}
    for e in entries:
        if not str(e.get("form") or "").startswith("10-Q"):
            continue
        fp = str(e.get("fp") or "").upper()
        if fp not in {"Q1", "Q2", "Q3", "Q4"}:
            continue
        end, filed = e.get("end"), e.get("filed")
        if not end or not filed or not _valid_filing_timing(end, filed):
            continue
        # Balanços são fatos instantâneos e normalmente não têm `start`.
        # Fluxos/resultados precisam ter duração de um trimestre isolado.
        if not instant and not _is_quarterly_duration(e.get("start"), end):
            continue
        fy = _sane_fiscal_year(e.get("fy"), end)
        if not fy:
            continue
        key = (fy, int(fp[1]))
        cur = out.get(key)
        # Um 10-Q também carrega comparativos de períodos anteriores com o
        # mesmo FY/FP do filing. Primeiro escolhemos o maior `end` (período
        # corrente); entre revisões desse mesmo período, preservamos o filing
        # mais antigo para manter o point-in-time.
        if (cur is None or str(end) > str(cur["end"])
                or (str(end) == str(cur["end"]) and str(filed) < str(cur["filed"]))):
            out[key] = {"val": e.get("val"), "filed": filed, "end": end}
    return out


# ── A-148: zero na tag preferida bloqueava a tag que tinha o valor ───────────
#
# A prioridade entre aliases era "primeiro que TIVER dado", e um `val: 0` conta
# como ter dado. A Eaton publica `Revenues` = 0 nos exercicios de 2014 a 2016 --
# uma linha de rollup vazia, ao lado de `SalesRevenueNet` = US$ 20,9 bilhoes, o
# numero real. O parser parava no zero: cinco anos de receita zero para uma
# industria de US$ 24 bilhoes, e o mesmo em Flowserve, Compass Minerals e
# Assured Guaranty (que publica a tag de ASC 606 zerada porque sua receita e de
# premios de seguro, nao de contrato com cliente). Sao 573 linhas anuais.
#
# A regra abaixo e deliberadamente estreita: o zero so cede quando um alias
# POSTERIOR traz valor diferente de zero para o mesmo periodo. A biotech que
# realmente nao fatura nao tem alias nenhum com valor, e continua zerada -- a
# correcao nao inventa receita, so deixa de preferir o vazio ao cheio.
_CAMPOS_ONDE_ZERO_CEDE = frozenset({"revenue"})


def _mesclar_alias(merged: dict, end, point, *, zero_cede: bool) -> None:
    atual = merged.get(end)
    if atual is None:
        merged[end] = point
        return
    # Trimestral: a chave e (fy, fp), nao a data. Alias de maior prioridade com
    # so o comparativo antigo nao pode vencer um alias com o periodo corrente.
    if point.get("end") and atual.get("end") and str(point["end"]) > str(atual["end"]):
        merged[end] = point
        return
    if zero_cede and not atual.get("val") and point.get("val"):
        merged[end] = point


def _collect(cf: dict, concepts: dict[str, list[str]], unit: str = _USD) -> dict[str, dict]:
    """{field: {end: {val, filed}}} — primeiro alias que tiver dado para o período."""
    out: dict[str, dict] = {}
    for field, tags in concepts.items():
        merged: dict[str, dict] = {}
        zero_cede = field in _CAMPOS_ONDE_ZERO_CEDE
        for tag in tags:
            for end, point in _annual_points(_entries(cf, tag, unit)).items():
                _mesclar_alias(merged, end, point, zero_cede=zero_cede)
        out[field] = merged
    return out


def _collect_units(cf: dict, concepts: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for field, (tags, unit) in concepts.items():
        merged: dict[str, dict] = {}
        for tag in tags:
            for end, point in _annual_points(_entries(cf, tag, unit)).items():
                merged.setdefault(end, point)
        out[field] = merged
    return out


def _collect_quarterly(cf: dict, concepts: dict[str, list[str]], unit: str = _USD,
                       *, instant: bool = False) -> dict:
    out: dict[str, dict] = {}
    for field, tags in concepts.items():
        merged: dict[tuple[int, int], dict] = {}
        zero_cede = field in _CAMPOS_ONDE_ZERO_CEDE
        for tag in tags:
            for key, point in _quarterly_points(
                    _entries(cf, tag, unit), instant=instant).items():
                _mesclar_alias(merged, key, point, zero_cede=zero_cede)
        out[field] = merged
    return out


def _collect_quarterly_units(cf: dict, concepts: dict, *, instant: bool = False) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for field, (tags, unit) in concepts.items():
        merged: dict[tuple[int, int], dict] = {}
        for tag in tags:
            for key, point in _quarterly_points(
                    _entries(cf, tag, unit), instant=instant).items():
                _mesclar_alias(merged, key, point, zero_cede=False)
        out[field] = merged
    return out


def _anchor_end_por_accn(cf: dict, forms: str) -> dict[str, str]:
    """{accn: fim do balanco corrente} a partir de fatos instantaneos do filing.

    O fato `dei` de capa tem `end` = data da capa (semanas depois do balanco) e
    nao pode ser casado com o balanco por data. O que os liga e o `accn` (numero
    do filing): o `end` mais recente entre os fatos de balanco do mesmo `accn` e o
    balanco corrente (os demais sao comparativos do exercicio anterior).
    """
    out: dict[str, str] = {}
    for tag in _BALANCE_ANCHOR_TAGS:
        for e in _entries(cf, tag, _USD):
            accn, end, filed = e.get("accn"), e.get("end"), e.get("filed")
            if (not accn or not end or not filed or e.get("start")
                    or not str(e.get("form") or "").startswith(forms)
                    or not _valid_filing_timing(end, filed)):
                continue
            if accn not in out or str(end) > out[accn]:
                out[accn] = str(end)
    return out


def _dei_shares_points(cf: dict, *, quarterly: bool) -> dict:
    """Capa do filing (`dei`), classes somadas, na chave do periodo do balanco.

    A capa informa as acoes em circulacao numa data POSTERIOR ao balanco (do
    10-K, tipicamente 1-2 meses). Documentado e aceito: o fato nasce no mesmo
    filing, entao `filed` (e o PIT) coincide com o do balanco, e para valor de
    mercado a contagem recente e a mais proxima do preco usado.

    Empresas com varias classes publicam uma entrada por classe, com o mesmo
    `accn` e `end` (o companyfacts nao traz a dimensao): somam-se. Limite: classes
    que valem fracoes distintas da mesma acao (BRK: A = 1.500 B) somam unidades
    nao comparaveis; nao ha como detectar isso sem a dimensao, e fica como lacuna
    declarada, sem caso especial por ticker.
    """
    forms = "10-Q" if quarterly else "10-K"
    anchor = _anchor_end_por_accn(cf, forms)
    grupos: dict[tuple, dict] = {}
    for e in _entries(cf, _DEI_SHARES_TAG, _SHARES):
        accn, end, filed = e.get("accn"), e.get("end"), e.get("filed")
        if (not accn or accn not in anchor or not end or not filed
                or not str(e.get("form") or "").startswith(forms)):
            continue
        val = to_float(e.get("val"))
        if val is None:
            continue
        g = grupos.setdefault((accn, str(end)), {"val": 0.0, "filed": filed, "e": e})
        g["val"] += val
    # por filing, so o grupo de `end` mais recente da capa
    por_accn: dict[str, tuple] = {}
    for (accn, end), g in grupos.items():
        if accn not in por_accn or end > por_accn[accn][0]:
            por_accn[accn] = (end, g)
    out: dict = {}
    for accn, (_cover_end, g) in por_accn.items():
        period_end, filed = anchor[accn], g["filed"]
        if not _valid_filing_timing(period_end, filed) or g["val"] <= 0:
            continue
        if quarterly:
            fp = str(g["e"].get("fp") or "").upper()
            if fp not in {"Q1", "Q2", "Q3", "Q4"}:
                continue
            fy = _sane_fiscal_year(g["e"].get("fy"), period_end)
            if not fy:
                continue
            key = (fy, int(fp[1]))
        else:
            key = period_end
        point = {"val": g["val"], "filed": filed}
        if quarterly:
            point["end"] = period_end
        cur = out.get(key)
        # mesma regra de `_quarterly_points`: balanco mais recente; entre
        # revisoes do mesmo periodo, o filing mais antigo (PIT)
        if cur is None:
            out[key] = point
        elif quarterly and period_end > cur["end"]:
            out[key] = point
        elif (not quarterly or period_end == cur["end"]) and str(filed) < str(cur["filed"]):
            out[key] = point
    return out


def _points(cf: dict, tags: list[str], *, quarterly: bool) -> dict:
    merged: dict = {}
    for tag in tags:
        entries = _entries(cf, tag, _SHARES)
        pts = (_quarterly_points(entries, instant=True) if quarterly
               else _annual_points(entries))
        for key, point in pts.items():
            merged.setdefault(key, point)
    return merged


def _issued_menos_tesouraria(cf: dict, *, quarterly: bool) -> dict:
    """Issued - Treasury do mesmo periodo; sem tesouraria (ou <= 0) nao ha valor."""
    issued = _points(cf, _SHARES_ISSUED_TAGS, quarterly=quarterly)
    treasury = _points(cf, _SHARES_TREASURY_TAGS, quarterly=quarterly)
    out: dict = {}
    for key, pt in issued.items():
        tr = treasury.get(key)
        if tr is None:
            continue
        # a chave trimestral e (fy, fp), nao a data: um 10-Q traz comparativos.
        # Emitidas e tesouraria so se subtraem na MESMA data de balanco.
        if quarterly and pt.get("end") != tr.get("end"):
            continue
        iss, trs = to_float(pt["val"]), to_float(tr["val"])
        if iss is None or trs is None or iss - trs <= 0:
            continue
        point = {"val": iss - trs, "filed": max(str(pt["filed"]), str(tr["filed"]))}
        if quarterly:
            point["end"] = pt["end"]
        out[key] = point
    return out


def _acoes_em_circulacao(cf: dict, *, quarterly: bool) -> dict:
    """{periodo: {val, filed}} de `shares_outstanding`, na ordem de prioridade."""
    merged: dict = {}
    for fonte in (
        lambda: _dei_shares_points(cf, quarterly=quarterly),
        lambda: _points(cf, _SHARES_OUTSTANDING_TAGS, quarterly=quarterly),
        lambda: _issued_menos_tesouraria(cf, quarterly=quarterly),
    ):
        for key, point in fonte().items():
            merged.setdefault(key, point)
    return merged


def _resolver_divida_combinada(collected: dict) -> None:
    """Resolve o total combinado ANTES de montar as linhas.

    So vale onde nao ha curto nem longo no periodo; vira `long_term_debt` com a
    propria procedencia (`filed`). Resolver aqui, e nao depois da linha pronta,
    evita que um combinado descartado entre no `available_at` e no `filed_at`.
    """
    combinado = collected.pop("_debt_combined", {})
    curto = collected.get("short_term_debt", {})
    longo = collected.setdefault("long_term_debt", {})
    for chave, ponto in combinado.items():
        if chave not in curto and chave not in longo:
            longo[chave] = ponto


def _exercicio_publicavel(ano: int, symbol: str | None, referencia: Any) -> bool:
    """Descarta o periodo cujo ano fiscal esta fora do dominio das tabelas.

    A-141: `_FY_MIN.._FY_MAX` ja replicava aqui o CHECK das tres tabelas de
    demonstracoes, mas nenhum dos dois construtores aplicava a faixa a linha que
    emitia -- so `_sane_fiscal_year` a usava, e a partir do campo `fy`, nao da
    data do periodo. Um fato XBRL com `end` em 1980 (SRPT tem um) gerava linha
    valida para o parser e recusada pelo banco, e como o lote e gravado numa
    transacao a empresa INTEIRA ficava sem fundamentos. Nao e caso de corrigir o
    valor: um exercicio de 1980 no companyfacts e ruido de tagueamento, e as
    linhas afetadas vinham sem nenhum valor financeiro.

    O descarte e registrado em WARNING de proposito. Filtro silencioso aqui
    viraria "a empresa nao tem esse ano" na leitura, que e afirmacao diferente.
    """
    if _FY_MIN <= ano <= _FY_MAX:
        return True
    logger.warning(
        "edgar_facts: %s descartando periodo fora da faixa publicavel "
        "(ano=%s, referencia=%s, faixa=%s..%s)",
        symbol or "?", ano, referencia, _FY_MIN, _FY_MAX,
    )
    return False


def _build_rows(collected: dict[str, dict], symbol: str | None = None) -> list[dict]:
    """Monta uma linha por período, com available_at conservador (filing mais tardio)."""
    ends: set[str] = set()
    for per_field in collected.values():
        ends.update(per_field.keys())

    rows = []
    for end in sorted(ends):
        d = parse_date(end)
        if d is None:
            continue
        if not _exercicio_publicavel(d.year, symbol, end):
            continue
        row: dict[str, Any] = {}
        filings: list[str] = []
        filed_por_campo: dict[str, str] = {}
        for field, per_field in collected.items():
            point = per_field.get(end)
            if point is None:
                row[field] = None            # ausente ≠ zero
                continue
            val = to_float(point["val"])
            if val is not None and field in NEGATE_FIELDS:
                val = -val                    # pagamento (XBRL +) → saída (projeto −)
            row[field] = val
            filings.append(str(point["filed"]))
            filed_por_campo[field] = str(point["filed"])
        if not filings:
            continue
        # conservador: só é conhecível quando o ÚLTIMO insumo da linha foi arquivado
        available = max(filings)
        row.update({
            "period": "annual", "fiscal_year": d.year, "fiscal_quarter": 0,
            "reference_date": d, "published_date": parse_date(available),
            "available_at": parse_date(available),
            "currency": "USD", "unit": "absolute", "source": "sec_edgar",
            "source_version": PARSER_VERSION, "quality_status": "raw",
        })
        if symbol:
            row["symbol"] = symbol
        row["content_hash"] = content_hash(row)
        # Depois do hash de proposito: o hash identifica os INSUMOS
        # financeiros da linha, e contaminá-lo com metadado de procedencia
        # faria toda a base parecer alterada na proxima ingestao.
        row["filed_at"] = dict(filed_por_campo)
        rows.append(row)
    return rows


def _build_quarterly_rows(collected: dict[str, dict], symbol: str | None = None) -> list[dict]:
    periods: set[tuple[int, int]] = set()
    for per_field in collected.values():
        periods.update(per_field.keys())
    rows = []
    for fy, fq in sorted(periods):
        if not _exercicio_publicavel(fy, symbol, f"{fy}Q{fq}"):
            continue
        row: dict[str, Any] = {}
        filings: list[str] = []
        ends: list[str] = []
        for field, per_field in collected.items():
            point = per_field.get((fy, fq))
            if point is None:
                row[field] = None
                continue
            val = to_float(point["val"])
            if val is not None and field in NEGATE_FIELDS:
                val = -val
            row[field] = val
            filings.append(str(point["filed"]))
            ends.append(str(point["end"]))
        if not filings or not ends:
            continue
        available = max(filings)
        reference = max(ends)
        row.update({
            "period": "quarterly", "fiscal_year": fy, "fiscal_quarter": fq,
            "reference_date": parse_date(reference),
            "published_date": parse_date(available), "available_at": parse_date(available),
            "currency": "USD", "unit": "absolute", "source": "sec_edgar",
            "source_version": PARSER_VERSION, "quality_status": "raw",
        })
        if symbol:
            row["symbol"] = symbol
        row["content_hash"] = content_hash(row)
        rows.append(row)
    return rows


# ── API pública ───────────────────────────────────────────────────────────────
# Estes campos nao existem no XBRL: saem de outros campos da MESMA linha. Ficam
# em funcoes proprias porque ha DOIS momentos em que precisam ser calculados: na
# montagem da linha e depois de a regra point-in-time esconder um insumo que
# ainda nao havia sido arquivado (`core.us_pit`). Enquanto a segunda copia vivia
# num script a parte, ela derivava MENOS campos que esta -- `invested_capital` e
# `free_cash_flow` sobreviviam a mascara com o valor calculado sobre o insumo
# invisivel, que e look-ahead dentro da propria correcao de look-ahead.
def derivar_income(r: dict) -> None:
    # EBIT nao e tag XBRL: usa o resultado operacional (mesma definicao do projeto)
    r["ebit"] = r.get("operating_income")
    r["ebitda"] = None        # exigiria D&A do fluxo; ausente > inventado


def derivar_balance(r: dict) -> None:
    std, ltd = r.get("short_term_debt"), r.get("long_term_debt")
    r["total_debt"] = None if std is None and ltd is None else (std or 0) + (ltd or 0)
    cash = r.get("cash_and_equivalents")
    r["net_debt"] = None if r["total_debt"] is None or cash is None         else r["total_debt"] - cash
    # capital investido e do perimetro consolidado (o EBIT inclui minoritarios)
    eq = r.get("total_equity_incl_nci")
    if eq is None:
        eq = r.get("total_equity")
    r["invested_capital"] = None if eq is None or r["total_debt"] is None         else eq + r["total_debt"] - (cash or 0.0)


def derivar_cashflow(r: dict) -> None:
    ocf, capex = r.get("operating_cash_flow"), r.get("capex")
    # FCF nao existe no XBRL: derivado (capex ja esta negativo aqui)
    r["free_cash_flow"] = None if ocf is None or capex is None else ocf + capex


DERIVADORES = {"income_statements": derivar_income,
               "balance_sheets": derivar_balance,
               "cash_flow_statements": derivar_cashflow}


def build_income_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect(cf, INCOME_CONCEPTS, _USD)
    collected.update(_collect_units(cf, INCOME_SHARE_CONCEPTS))
    # Os auxiliares entram ANTES de _build_rows para que o content_hash reflita
    # os insumos da receita: se o filer passa a reportar juros liquidos, o hash
    # muda e a re-ingestao detecta. Sao removidos depois -- nao existem no schema.
    collected.update(_collect(cf, BANK_REVENUE_CONCEPTS, _USD))
    rows = _build_rows(collected, symbol)
    for r in rows:
        derivar_income(r)
    _aplicar_receita_bancaria(rows, cf, symbol)
    return rows


def build_balance_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect(cf, BALANCE_CONCEPTS, _USD)
    collected.update(_collect(cf, BALANCE_AUX_CONCEPTS, _USD))
    collected["shares_outstanding"] = _acoes_em_circulacao(cf, quarterly=False)
    _resolver_divida_combinada(collected)
    rows = _build_rows(collected, symbol)
    for r in rows:
        derivar_balance(r)
    return rows


def build_cashflow_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    rows = _build_rows(_collect(cf, CASHFLOW_CONCEPTS, _USD), symbol)
    for r in rows:
        derivar_cashflow(r)
    return rows


def build_income_quarterly_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect_quarterly(cf, INCOME_CONCEPTS, _USD)
    collected.update(_collect_quarterly_units(cf, INCOME_SHARE_CONCEPTS))
    collected.update(_collect_quarterly(cf, BANK_REVENUE_CONCEPTS, _USD))
    rows = _build_quarterly_rows(collected, symbol)
    for r in rows:
        r["ebit"] = r.get("operating_income")
        r["ebitda"] = None
    # mesma regra do anual, sob pena de a serie trimestral contradizer o
    # exercicio fechado
    _aplicar_receita_bancaria(rows, cf, symbol)
    return rows


def build_balance_quarterly_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    collected = _collect_quarterly(cf, BALANCE_CONCEPTS, _USD, instant=True)
    collected.update(_collect_quarterly(cf, BALANCE_AUX_CONCEPTS, _USD, instant=True))
    collected["shares_outstanding"] = _acoes_em_circulacao(cf, quarterly=True)
    _resolver_divida_combinada(collected)
    rows = _build_quarterly_rows(collected, symbol)
    for r in rows:
        std, ltd = r.get("short_term_debt"), r.get("long_term_debt")
        r["total_debt"] = None if std is None and ltd is None else (std or 0) + (ltd or 0)
        cash = r.get("cash_and_equivalents")
        r["net_debt"] = None if r["total_debt"] is None or cash is None \
            else r["total_debt"] - cash
        eq = r.get("total_equity_incl_nci")
        if eq is None:
            eq = r.get("total_equity")
        r["invested_capital"] = None if eq is None or r["total_debt"] is None \
            else eq + r["total_debt"] - (cash or 0.0)
    return rows


def build_cashflow_quarterly_rows(cf: dict, symbol: str | None = None) -> list[dict]:
    rows = _build_quarterly_rows(_collect_quarterly(cf, CASHFLOW_CONCEPTS, _USD), symbol)
    for r in rows:
        ocf, capex = r.get("operating_cash_flow"), r.get("capex")
        r["free_cash_flow"] = None if ocf is None or capex is None else ocf + capex
    return rows


def cik_from_facts(cf: dict) -> Optional[str]:
    cik = (cf or {}).get("cik")
    return None if cik is None else str(cik).zfill(10)

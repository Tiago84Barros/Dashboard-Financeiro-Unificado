"""Testes da tradução XBRL (EDGAR companyfacts) → schema market_us."""
from datetime import date

import pytest

import data_pipeline.us.edgar_facts as ef


def _fact(tag, entries, unit="USD"):
    return {tag: {"units": {unit: entries}}}


def _cf(facts_gaap: dict, cik=320193):
    return {"cik": cik, "facts": {"us-gaap": facts_gaap}}


def _e(end, val, filed, start=None, form="10-K"):
    e = {"end": end, "val": val, "filed": filed, "form": form}
    if start:
        e["start"] = start
    return e


def test_income_basico_com_pit():
    facts = {}
    facts.update(_fact("Revenues", [
        _e("2022-12-31", 1000, "2023-02-15", start="2022-01-01"),
        _e("2023-12-31", 1200, "2024-02-14", start="2023-01-01"),
    ]))
    facts.update(_fact("NetIncomeLoss", [
        _e("2022-12-31", 100, "2023-02-15", start="2022-01-01"),
        _e("2023-12-31", 150, "2024-02-14", start="2023-01-01"),
    ]))
    rows = ef.build_income_rows(_cf(facts), "AAPL")
    assert len(rows) == 2
    r23 = rows[-1]
    assert r23["fiscal_year"] == 2023 and r23["revenue"] == 1200
    assert r23["net_income"] == 150
    assert r23["available_at"] == date(2024, 2, 14)   # filing date = PIT
    assert r23["source"] == "sec_edgar" and r23["period"] == "annual"
    assert r23["source_version"] == ef.PARSER_VERSION
    assert r23["gross_profit"] is None                # ausente ≠ zero


def test_alias_de_receita_por_prioridade():
    # empresa que reporta só RevenueFromContractWithCustomer... (sem Revenues)
    facts = _fact("RevenueFromContractWithCustomerExcludingAssessedTax",
                  [_e("2023-12-31", 500, "2024-02-01", start="2023-01-01")])
    rows = ef.build_income_rows(_cf(facts))
    assert rows[0]["revenue"] == 500


def test_filed_mais_antigo_por_periodo():
    """O 10-K seguinte re-reporta o ano anterior como comparativo; o PIT deve
    usar o filing ORIGINAL (mais antigo), não o re-reporte."""
    facts = _fact("Revenues", [
        _e("2022-12-31", 1000, "2023-02-15", start="2022-01-01"),  # original
        _e("2022-12-31", 1000, "2024-02-14", start="2022-01-01"),  # comparativo no 10-K/2023
    ])
    rows = ef.build_income_rows(_cf(facts))
    assert rows[0]["available_at"] == date(2023, 2, 15)


def test_ignora_trimestres_e_formularios_nao_10k():
    facts = _fact("Revenues", [
        _e("2023-03-31", 300, "2023-05-01", start="2023-01-01", form="10-Q"),  # 10-Q
        _e("2023-06-30", 310, "2023-08-01", start="2023-04-01"),               # duração 3m
        _e("2023-12-31", 1200, "2024-02-14", start="2023-01-01"),              # anual
    ])
    rows = ef.build_income_rows(_cf(facts))
    assert len(rows) == 1 and rows[0]["fiscal_year"] == 2023
    assert rows[0]["revenue"] == 1200


def test_sinais_de_caixa_invertidos():
    """XBRL: pagamentos são positivos. Projeto: saídas são negativas
    (fcf = cfo + capex)."""
    facts = {}
    facts.update(_fact("NetCashProvidedByUsedInOperatingActivities",
                       [_e("2023-12-31", 260, "2024-02-14", start="2023-01-01")]))
    facts.update(_fact("PaymentsToAcquirePropertyPlantAndEquipment",
                       [_e("2023-12-31", 60, "2024-02-14", start="2023-01-01")]))
    facts.update(_fact("PaymentsOfDividendsCommonStock",
                       [_e("2023-12-31", 30, "2024-02-14", start="2023-01-01")]))
    rows = ef.build_cashflow_rows(_cf(facts))
    r = rows[0]
    assert r["operating_cash_flow"] == 260
    assert r["capex"] == -60                       # negado
    assert r["dividends_paid"] == -30              # negado
    assert r["free_cash_flow"] == pytest.approx(200)   # 260 + (-60)


def test_balance_derivados():
    facts = {}
    facts.update(_fact("Assets", [_e("2023-12-31", 2000, "2024-02-14")]))
    facts.update(_fact("StockholdersEquity", [_e("2023-12-31", 1000, "2024-02-14")]))
    facts.update(_fact("LongTermDebtNoncurrent", [_e("2023-12-31", 400, "2024-02-14")]))
    facts.update(_fact("LongTermDebtCurrent", [_e("2023-12-31", 100, "2024-02-14")]))
    facts.update(_fact("CashAndCashEquivalentsAtCarryingValue",
                       [_e("2023-12-31", 200, "2024-02-14")]))
    facts.update(_fact("RetainedEarningsAccumulatedDeficit",
                       [_e("2023-12-31", 700, "2024-02-14")]))
    rows = ef.build_balance_rows(_cf(facts))
    r = rows[0]
    assert r["total_debt"] == 500                  # 400 LP + 100 CP
    assert r["net_debt"] == 300                    # 500 − 200 caixa
    assert r["invested_capital"] == 1300           # 1000 + 500 − 200
    assert r["retained_earnings"] == 700           # termo X2 do Altman


def test_equity_inclusivo_tem_prioridade_sobre_equity_da_controladora():
    facts = {}
    facts.update(_fact("Assets", [_e("2024-12-31", 1000, "2025-02-15")]))
    facts.update(_fact("Liabilities", [_e("2024-12-31", 600, "2025-02-15")]))
    facts.update(_fact("StockholdersEquity", [
        _e("2024-12-31", 250, "2025-02-15")]))
    facts.update(_fact(
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        [_e("2024-12-31", 400, "2025-02-15")]))
    row = ef.build_balance_rows(_cf(facts))[0]
    assert row["total_equity"] == 400
    assert row["total_assets"] == row["total_liabilities"] + row["total_equity"]


def test_available_at_conservador_no_maximo_dos_filings():
    """Se campos da mesma linha vêm de filings diferentes, available_at = o mais
    tardio (a linha completa só era conhecível quando o último saiu)."""
    facts = {}
    facts.update(_fact("Revenues",
                       [_e("2023-12-31", 1200, "2024-02-14", start="2023-01-01")]))
    facts.update(_fact("NetIncomeLoss",
                       [_e("2023-12-31", 150, "2024-03-01", start="2023-01-01")]))
    rows = ef.build_income_rows(_cf(facts))
    assert rows[0]["available_at"] == date(2024, 3, 1)


def test_rejeita_fato_arquivado_antes_do_fim_do_periodo():
    invalid = _e("2025-12-31", 999, "2025-11-01",
                 start="2025-01-01", form="10-K")
    valid = _e("2024-12-31", 800, "2025-02-15",
               start="2024-01-01", form="10-K")
    rows = ef.build_income_rows(_cf(_fact("Revenues", [invalid, valid])))
    assert len(rows) == 1
    assert rows[0]["reference_date"] == date(2024, 12, 31)
    assert rows[0]["revenue"] == 800


def test_cik_from_facts():
    assert ef.cik_from_facts({"cik": 320193}) == "0000320193"
    assert ef.cik_from_facts({}) is None


def test_quarterly_usa_10q_curto_e_rejeita_ytd():
    q1 = _e("2024-03-31", 300, "2024-05-01", start="2024-01-01", form="10-Q")
    q1.update({"fy": 2024, "fp": "Q1"})
    ytd = _e("2024-06-30", 650, "2024-08-01", start="2024-01-01", form="10-Q")
    ytd.update({"fy": 2024, "fp": "Q2"})
    facts = _fact("Revenues", [q1, ytd])
    rows = ef.build_income_quarterly_rows(_cf(facts), "TEST")
    assert len(rows) == 1
    assert rows[0]["period"] == "quarterly"
    assert rows[0]["fiscal_year"] == 2024 and rows[0]["fiscal_quarter"] == 1
    assert rows[0]["revenue"] == 300


def test_quarterly_rejeita_periodo_posterior_ao_filing():
    invalid = _e("2027-03-31", 300, "2021-05-07", form="10-Q")
    invalid.update({"fy": 2021, "fp": "Q1"})
    rows = ef.build_balance_quarterly_rows(
        _cf(_fact("Assets", [invalid])))
    assert rows == []


def test_quarterly_prefere_fim_corrente_ao_comparativo():
    comparative = _e("2023-03-31", 250, "2024-05-01",
                     start="2023-01-01", form="10-Q")
    comparative.update({"fy": 2024, "fp": "Q1"})
    current = _e("2024-03-31", 300, "2024-05-01",
                 start="2024-01-01", form="10-Q")
    current.update({"fy": 2024, "fp": "Q1"})
    rows = ef.build_income_quarterly_rows(
        _cf(_fact("Revenues", [comparative, current])))
    assert len(rows) == 1
    assert rows[0]["reference_date"] == date(2024, 3, 31)
    assert rows[0]["revenue"] == 300


def test_balance_quarterly_aceita_fatos_instantaneos_e_alinha_fim():
    facts = {}
    for tag, value in (("Assets", 2000), ("Liabilities", 1200),
                       ("StockholdersEquity", 800)):
        old = _e("2023-03-31", value - 100, "2024-05-01", form="10-Q")
        old.update({"fy": 2024, "fp": "Q1"})
        current = _e("2024-03-31", value, "2024-05-01", form="10-Q")
        current.update({"fy": 2024, "fp": "Q1"})
        facts.update(_fact(tag, [old, current]))
    rows = ef.build_balance_quarterly_rows(_cf(facts))
    assert len(rows) == 1
    assert rows[0]["reference_date"] == date(2024, 3, 31)
    assert rows[0]["total_assets"] == 2000
    assert rows[0]["total_liabilities"] + rows[0]["total_equity"] == 2000


def test_depreciacao_disponivel_para_ebitda_derivado():
    facts = _fact("DepreciationDepletionAndAmortization", [
        _e("2023-12-31", 40, "2024-02-14", start="2023-01-01")])
    rows = ef.build_cashflow_rows(_cf(facts))
    assert rows[0]["depreciation_and_amortization"] == 40

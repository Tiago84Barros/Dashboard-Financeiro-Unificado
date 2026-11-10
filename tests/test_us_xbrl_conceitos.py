"""Conceitos XBRL: PL do controlador, dívida, dividendo e contagem de ações.

Dados 100% sintéticos (EMPRESA_X, valores inventados). Cada teste reproduz um
defeito da auditoria da extração SEC.
"""
from datetime import date

import pandas as pd

import data_pipeline.us.edgar_facts as ef
from core.us_company_analysis import derive_metric_history


def _fact(tag, entries, unit="USD"):
    return {tag: {"units": {unit: entries}}}


def _e(end, val, filed, start=None, form="10-K", **extra):
    e = {"end": end, "val": val, "filed": filed, "form": form}
    if start:
        e["start"] = start
    e.update(extra)
    return e


def _cf(gaap=None, dei=None):
    facts = {"us-gaap": gaap or {}}
    if dei:
        facts["dei"] = dei
    return {"cik": 1, "facts": facts}


END, FILED = "2023-12-31", "2024-02-20"
NCI = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"


# ── A6: PL do controlador ────────────────────────────────────────────────────
def test_pl_do_controlador_tem_prioridade_sobre_o_com_minoritarios():
    gaap = {}
    gaap.update(_fact("StockholdersEquity", [_e(END, 30, FILED)]))
    gaap.update(_fact(NCI, [_e(END, 79, FILED)]))
    assert ef.build_balance_rows(_cf(gaap))[0]["total_equity"] == 30


def test_pl_com_minoritarios_so_entra_na_falta_do_controlador():
    gaap = _fact(NCI, [_e(END, 79, FILED)])
    assert ef.build_balance_rows(_cf(gaap))[0]["total_equity"] == 79


def test_roe_usa_pl_do_controlador_e_exige_denominador_positivo():
    gaap = {}
    gaap.update(_fact("StockholdersEquity", [_e(END, 30, FILED)]))
    gaap.update(_fact(NCI, [_e(END, 79, FILED)]))
    bal = ef.build_balance_rows(_cf(gaap))[0]
    f = pd.DataFrame([{"fiscal_year": 2023, "net_income": 3.0,
                       "total_equity": bal["total_equity"]}])
    assert abs(derive_metric_history(f)["roe"].iloc[0] - 0.10) < 1e-9

    neg = pd.DataFrame([{"fiscal_year": 2023, "net_income": -3.0,
                         "total_equity": -30.0, "total_debt": 5.0,
                         "total_assets": 10.0}])
    h = derive_metric_history(neg)
    for col in ("roe", "debt_to_equity", "financial_leverage"):
        assert pd.isna(h[col].iloc[0]), col


def test_p_b_ignora_pl_nao_positivo():
    f = pd.DataFrame([{"fiscal_year": 2023, "net_income": 1.0,
                       "total_equity": -10.0, "shares_outstanding": 5.0}])
    prices = pd.DataFrame([{"date": "2023-12-29", "price": 10.0}])
    assert pd.isna(derive_metric_history(f, prices)["p_b"].iloc[0])


# ── A7: dívida ───────────────────────────────────────────────────────────────
def _assets():
    return _fact("Assets", [_e(END, 500, FILED)])


def test_divida_com_notes_payable_nao_zera():
    gaap = _assets()
    gaap.update(_fact("LongTermNotesPayable", [_e(END, 100, FILED)]))
    gaap.update(_fact("NotesPayableCurrent", [_e(END, 7, FILED)]))
    row = ef.build_balance_rows(_cf(gaap))[0]
    assert row["long_term_debt"] == 100 and row["short_term_debt"] == 7
    assert row["total_debt"] == 107


def test_so_long_term_notes_payable_da_divida_100():
    gaap = _assets()
    gaap.update(_fact("LongTermNotesPayable", [_e(END, 100, FILED)]))
    assert ef.build_balance_rows(_cf(gaap))[0]["total_debt"] == 100


def test_nenhum_tag_de_divida_vira_nulo_nao_zero():
    row = ef.build_balance_rows(_cf(_assets()))[0]
    assert row["total_debt"] is None and row["net_debt"] is None
    assert row["invested_capital"] is None


def test_divida_combinada_nao_soma_em_dobro_com_as_partes():
    gaap = _assets()
    gaap.update(_fact("LongTermDebtCurrent", [_e(END, 10, FILED)]))
    gaap.update(_fact("DebtLongtermAndShorttermCombinedAmount", [_e(END, 110, FILED)]))
    row = ef.build_balance_rows(_cf(gaap))[0]
    assert row["total_debt"] == 10      # a parte existe: o combinado fica de fora


def test_divida_combinada_sozinha_vale_como_total():
    gaap = _assets()
    gaap.update(_fact("DebtLongtermAndShorttermCombinedAmount", [_e(END, 110, FILED)]))
    assert ef.build_balance_rows(_cf(gaap))[0]["total_debt"] == 110


def test_divida_com_arrendamento_como_alias():
    gaap = _assets()
    gaap.update(_fact("LongTermDebtAndCapitalLeaseObligations", [_e(END, 80, FILED)]))
    gaap.update(_fact("LongTermDebtAndCapitalLeaseObligationsCurrent", [_e(END, 5, FILED)]))
    assert ef.build_balance_rows(_cf(gaap))[0]["total_debt"] == 85


def test_divida_trimestral_com_notes_payable():
    gaap = {}
    gaap.update(_fact("Assets", [_e("2024-03-31", 600, "2024-05-02", form="10-Q",
                                    fy=2024, fp="Q1")]))
    gaap.update(_fact("LongTermNotesPayable", [_e("2024-03-31", 100, "2024-05-02",
                                                  form="10-Q", fy=2024, fp="Q1")]))
    row = ef.build_balance_quarterly_rows(_cf(gaap))[0]
    assert row["total_debt"] == 100


# ── A8: dividendos ───────────────────────────────────────────────────────────
def test_dividendo_so_com_dividends_common_stock_cash_segue_a_convencao_de_sinal():
    gaap = _fact("DividendsCommonStockCash", [_e(END, 40, FILED, start="2023-01-01")])
    assert ef.build_cashflow_rows(_cf(gaap))[0]["dividends_paid"] == -40


def test_dividendo_pago_tem_prioridade_sobre_declarado():
    gaap = {}
    gaap.update(_fact("PaymentsOfDividendsCommonStock",
                      [_e(END, 38, FILED, start="2023-01-01")]))
    gaap.update(_fact("DividendsCommonStockCash",
                      [_e(END, 40, FILED, start="2023-01-01")]))
    assert ef.build_cashflow_rows(_cf(gaap))[0]["dividends_paid"] == -38


# ── A2: ações em circulação ──────────────────────────────────────────────────
def _bal(gaap, dei=None):
    gaap = dict(gaap)
    gaap.update(_fact("Assets", [_e(END, 500, FILED, fy=2023, fp="FY", accn="A1")]))
    return ef.build_balance_rows(_cf(gaap, dei))[0]


def test_issued_menos_tesouraria_quando_nao_ha_outstanding():
    gaap = {}
    gaap.update(_fact("CommonStockSharesIssued", [_e(END, 100, FILED)], "shares"))
    gaap.update(_fact("TreasuryStockShares", [_e(END, 30, FILED)], "shares"))
    assert _bal(gaap)["shares_outstanding"] == 70


def test_issued_sem_tesouraria_e_nulo():
    gaap = _fact("CommonStockSharesIssued", [_e(END, 100, FILED)], "shares")
    assert _bal(gaap)["shares_outstanding"] is None


def test_outstanding_tem_prioridade_sobre_issued():
    gaap = {}
    gaap.update(_fact("CommonStockSharesIssued", [_e(END, 100, FILED)], "shares"))
    gaap.update(_fact("CommonStockSharesOutstanding", [_e(END, 65, FILED)], "shares"))
    assert _bal(gaap)["shares_outstanding"] == 65


def test_issued_menos_tesouraria_nao_positivo_e_nulo():
    gaap = {}
    gaap.update(_fact("CommonStockSharesIssued", [_e(END, 30, FILED)], "shares"))
    gaap.update(_fact("TreasuryStockShares", [_e(END, 30, FILED)], "shares"))
    assert _bal(gaap)["shares_outstanding"] is None


# ── A4 / A2: dei ─────────────────────────────────────────────────────────────
def _dei(*vals, end="2024-02-10", accn="A1", form="10-K"):
    return _fact("EntityCommonStockSharesOutstanding",
                 [_e(end, v, FILED, form=form, fy=2023, fp="FY", accn=accn)
                  for v in vals], "shares")


def test_dei_soma_as_classes_do_mesmo_filing():
    assert _bal({}, _dei(10, 90))["shares_outstanding"] == 100


def test_dei_sem_us_gaap_preenche_a_contagem():
    row = _bal({}, _dei(55))
    assert row["shares_outstanding"] == 55
    assert row["available_at"] == date(2024, 2, 20)


def test_dei_tem_prioridade_sobre_issued():
    gaap = _fact("CommonStockSharesIssued", [_e(END, 400, FILED)], "shares")
    assert _bal(gaap, _dei(270))["shares_outstanding"] == 270


def test_dei_de_outro_filing_nao_contamina_o_periodo():
    assert _bal({}, _dei(77, accn="B9"))["shares_outstanding"] is None


def test_dei_trimestral_alinha_ao_fim_do_balanco_e_soma_classes():
    gaap = _fact("Assets", [_e("2024-03-31", 600, "2024-05-02", form="10-Q",
                               fy=2024, fp="Q1", accn="Q1A")])
    dei = _fact("EntityCommonStockSharesOutstanding",
                [_e("2024-04-25", v, "2024-05-02", form="10-Q", fy=2024,
                    fp="Q1", accn="Q1A") for v in (10, 90)], "shares")
    row = ef.build_balance_quarterly_rows(_cf(gaap, dei))[0]
    assert row["shares_outstanding"] == 100
    assert row["reference_date"] == date(2024, 3, 31)   # não a data da capa


def test_issued_menos_tesouraria_trimestral():
    base = {"form": "10-Q", "fy": 2024, "fp": "Q1"}
    gaap = {}
    gaap.update(_fact("Assets", [_e("2024-03-31", 600, "2024-05-02", **base)]))
    gaap.update(_fact("CommonStockSharesIssued",
                      [_e("2024-03-31", 4100, "2024-05-02", **base)], "shares"))
    row = ef.build_balance_quarterly_rows(_cf(gaap))[0]
    assert row["shares_outstanding"] is None            # sem tesouraria
    gaap.update(_fact("TreasuryStockCommonShares",
                      [_e("2024-03-31", 1400, "2024-05-02", **base)], "shares"))
    row = ef.build_balance_quarterly_rows(_cf(gaap))[0]
    assert row["shares_outstanding"] == 2700


def test_parser_version_subiu():
    assert ef.PARSER_VERSION != "companyfacts-parser-v7"


# ── Codex #706: provenance, datas e identidade ───────────────────────────────
def test_divida_combinada_promovida_leva_a_procedencia_e_o_pit_a_mascara():
    from core.us_pit import REGRA_CAMPO, visiveis
    gaap = {}
    gaap.update(_fact("Assets", [_e("2020-12-31", 500, "2021-02-15")]))
    gaap.update(_fact("DebtLongtermAndShorttermCombinedAmount",
                      [_e("2020-12-31", 110, "2023-02-15")]))
    row = ef.build_balance_rows(_cf(gaap))[0]
    assert row["long_term_debt"] == 110
    assert "_debt_combined" not in row["filed_at"]
    assert row["filed_at"]["long_term_debt"] == "2023-02-15"
    vis = visiveis([row], date(2021, 3, 31), regra=REGRA_CAMPO,
                  derivar=ef.derivar_balance)
    assert vis and vis[0]["long_term_debt"] is None
    assert vis[0]["total_debt"] is None


def test_emitidas_e_tesouraria_exigem_a_mesma_data_trimestral():
    base = {"form": "10-Q", "fy": 2024, "fp": "Q1"}
    gaap = {}
    gaap.update(_fact("Assets", [_e("2024-03-31", 600, "2024-05-02", **base)]))
    gaap.update(_fact("CommonStockSharesIssued",
                      [_e("2024-03-31", 100, "2024-05-02", **base)], "shares"))
    # tesouraria so no comparativo de dezembro, mesmo (fy, fp) do filing
    gaap.update(_fact("TreasuryStockShares",
                      [_e("2023-12-31", 30, "2024-05-02", **base)], "shares"))
    row = ef.build_balance_quarterly_rows(_cf(gaap))[0]
    assert row["shares_outstanding"] is None


def test_pl_consolidado_fica_a_parte_e_alimenta_o_capital_investido():
    gaap = {}
    gaap.update(_fact("Assets", [_e(END, 1000, FILED)]))
    gaap.update(_fact("Liabilities", [_e(END, 600, FILED)]))
    gaap.update(_fact("StockholdersEquity", [_e(END, 250, FILED)]))
    gaap.update(_fact(NCI, [_e(END, 400, FILED)]))
    gaap.update(_fact("LongTermDebtNoncurrent", [_e(END, 100, FILED)]))
    row = ef.build_balance_rows(_cf(gaap))[0]
    assert row["total_equity"] == 250 and row["total_equity_incl_nci"] == 400
    assert row["invested_capital"] == 500        # consolidado + divida


def test_identidade_usa_o_consolidado_so_quando_a_coluna_existe():
    from data_pipeline.us.repository import equity_identity_expr
    assert "total_equity_incl_nci" in equity_identity_expr({"total_equity_incl_nci"})
    assert equity_identity_expr({"total_equity"}) == "total_equity"
    # ativo 1000, passivo 600, controlador 250, consolidado 400
    eq = {"total_equity_incl_nci": 400, "total_equity": 250}
    total = 600 + (eq["total_equity_incl_nci"] or eq["total_equity"])
    assert abs(1000 - total) / 1000 <= 0.02


def test_upsert_descarta_a_coluna_nova_sem_a_migration():
    from data_pipeline.us import repository as repo

    class Conn:
        def __init__(self):
            self.calls = []

        def execute(self, sql, payload=None):
            self.calls.append((str(sql), payload))
            return self

        def fetchall(self):
            return [("total_equity",)]

    c = Conn()
    repo.upsert_statements(c, "balance_sheets", 1, "X",
                           [{"fiscal_year": 2023, "total_equity": 1,
                             "total_equity_incl_nci": 2, "fiscal_quarter": 0,
                             "period": "annual"}])
    sql, payload = c.calls[-1]
    assert "total_equity_incl_nci" not in sql

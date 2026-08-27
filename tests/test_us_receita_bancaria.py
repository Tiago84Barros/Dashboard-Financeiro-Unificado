# -*- coding: utf-8 -*-
"""A receita de banco nao estava ausente: estava errada.

`RevenueFromContractWithCustomerExcludingAssessedTax` encabeca a lista de
aliases e, num banco, captura so as tarifas de ASC 606. Medido no armazem:
48 das 83 financeiras com receita gravada tinham margem liquida acima de 100%
-- impossivel -- contra 48 em 2.323 no resto do universo.
"""
from __future__ import annotations

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


def _ano(**tags):
    """Um exercicio de 2023 com as tags pedidas."""
    facts = {}
    for tag, val in tags.items():
        facts.update(_fact(tag, [_e("2023-12-31", val, "2024-02-14",
                                    start="2023-01-01")]))
    return _cf(facts)


def test_receita_de_banco_substitui_a_tarifa_de_asc606():
    """Numeros de MRBK: a tarifa era 6,3 MM contra 112,3 MM de receita real."""
    rows = ef.build_income_rows(_ano(
        RevenueFromContractWithCustomerExcludingAssessedTax=6_316_000,
        InterestIncomeExpenseNet=95_000_000,
        NoninterestIncome=17_335_000,
        NetIncomeLoss=21_800_000,
    ), "MRBK")
    assert len(rows) == 1
    # substituicao, nao preenchimento de lacuna: o valor generico existia
    assert rows[0]["revenue"] == 112_335_000
    assert rows[0]["net_income"] / rows[0]["revenue"] < 1.0


def test_tag_explicita_tem_precedencia_sobre_a_soma():
    """Quem publica RevenuesNetOfInterestExpense ja fez a conta; nao refazemos."""
    rows = ef.build_income_rows(_ano(
        RevenuesNetOfInterestExpense=78_066_000_000,
        InterestIncomeExpenseNet=40_000_000_000,
        NoninterestIncome=30_000_000_000,
    ), "C")
    assert rows[0]["revenue"] == 78_066_000_000


def test_quem_nao_e_banco_mantem_a_receita_generica():
    """USIO reporta receita de servico e nenhuma tag de intermediacao."""
    rows = ef.build_income_rows(_ano(
        RevenueFromContractWithCustomerExcludingAssessedTax=85_393_626,
        NetIncomeLoss=-2_500_000,
    ), "USIO")
    assert rows[0]["revenue"] == 85_393_626


def test_so_uma_das_duas_pernas_ainda_produz_receita():
    """Banco sem receita nao-juros nao pode voltar a valer a tarifa de ASC 606."""
    rows = ef.build_income_rows(_ano(InterestIncomeExpenseNet=10_793_928), "HYNE")
    assert rows[0]["revenue"] == 10_793_928


def test_campos_auxiliares_nao_vazam_para_o_schema():
    rows = ef.build_income_rows(_ano(
        InterestIncomeExpenseNet=95_000_000, NoninterestIncome=17_335_000))
    assert not [k for k in rows[0] if k.startswith("_")]


def test_trimestral_segue_a_mesma_regra():
    """Serie trimestral com outra definicao contradiria o exercicio fechado."""
    def _q(val):
        e = _e("2023-03-31", val, "2023-05-01", start="2023-01-01", form="10-Q")
        e.update({"fy": 2023, "fp": "Q1"})
        return [e]

    facts = {}
    facts.update(_fact("RevenueFromContractWithCustomerExcludingAssessedTax",
                       _q(1_500_000)))
    facts.update(_fact("InterestIncomeExpenseNet", _q(23_000_000)))
    facts.update(_fact("NoninterestIncome", _q(4_000_000)))
    rows = ef.build_income_quarterly_rows(_cf(facts), "MRBK")
    assert rows and rows[0]["revenue"] == 27_000_000


def test_versao_do_parser_subiu():
    """A mudanca reescreve receita ja ingerida: sem bump, a re-ingestao nao sabe."""
    assert ef.PARSER_VERSION == "companyfacts-parser-v5"

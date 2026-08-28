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
    """Banco sem receita nao-juros nao pode voltar a valer a tarifa de ASC 606.

    A-142: a versao original deste teste passava so `InterestIncomeExpenseNet`,
    e com isso fixava o defeito -- essa tag sozinha nao identifica um banco
    (10 de 12 nao-financeiras medidas na SEC a publicam). O que o teste quer
    afirmar continua valendo, mas o filer precisa estar qualificado: aqui pelo
    `InterestAndDividendIncomeOperating`, que so intermediario financeiro usa.
    """
    rows = ef.build_income_rows(_ano(
        InterestAndDividendIncomeOperating=14_000_000,
        InterestIncomeExpenseNet=10_793_928,
    ), "HYNE")
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
    """A mudanca reescreve receita ja ingerida: sem bump, a re-ingestao nao sabe.

    Prende o piso, nao a versao exata: cravar "v6" fazia o teste quebrar em
    todo bump seguinte e ser consertado editando o literal -- o que nao checa
    nada. O A-138 exigiu chegar a v6; quem subir depois passa por aqui.
    """
    assert ef.PARSER_VERSION.startswith("companyfacts-parser-v")
    assert int(ef.PARSER_VERSION.rsplit("v", 1)[1]) >= 6


# ── A-142: a qualificacao do filer ───────────────────────────────────────────
# O A-138 tratava como banco quem reportasse `InterestIncomeExpenseNet`. Medido
# contra a SEC, essa tag aparece em 10 de 12 companhias NAO-financeiras -- ela e
# a linha de resultado financeiro liquido, nao um marcador de intermediacao. A
# AMD ficou com receita de US$ 215 milhoes (juro do caixa) e a Autodesk com
# receita negativa de US$ 82,4 milhoes (despesa financeira liquida).
def test_empresa_de_tecnologia_com_juros_de_caixa_mantem_a_receita_propria():
    """O caso AMD: receita real preservada, juro do caixa fora do denominador."""
    from data_pipeline.us.edgar_facts import build_income_rows
    linhas = build_income_rows(_ano(**{
        "RevenueFromContractWithCustomerExcludingAssessedTax": 25_785_000_000.0,
        "InterestIncomeExpenseNet": 215_000_000.0,
        "NetIncomeLoss": 1_641_000_000.0,
    }), "AMD")
    assert linhas and linhas[0]["revenue"] == 25_785_000_000.0
    assert linhas[0]["revenue"] > linhas[0]["net_income"], "margem >100% e impossivel"


def test_despesa_financeira_liquida_nunca_vira_receita_negativa():
    """O caso ADSK: `InterestIncomeExpenseNet` negativo nao e receita."""
    from data_pipeline.us.edgar_facts import build_income_rows
    linhas = build_income_rows(_ano(**{
        "RevenueFromContractWithCustomerExcludingAssessedTax": 5_497_000_000.0,
        "InterestIncomeExpenseNet": -82_400_000.0,
        "NetIncomeLoss": 906_000_000.0,
    }), "ADSK")
    assert linhas and linhas[0]["revenue"] == 5_497_000_000.0
    assert linhas[0]["revenue"] > 0


def test_banco_continua_com_a_receita_de_intermediacao():
    """A correcao do A-138 nao pode ser desfeita pela do A-142."""
    from data_pipeline.us.edgar_facts import build_income_rows
    linhas = build_income_rows(_ano(**{
        "RevenueFromContractWithCustomerExcludingAssessedTax": 619_000.0,
        "InterestIncomeExpenseNet": 30_000_000.0,
        "NoninterestIncome": 4_000_000.0,
        "NetIncomeLoss": 7_200_000.0,
    }), "AUBN")
    assert linhas and linhas[0]["revenue"] == 34_000_000.0
    assert linhas[0]["revenue"] > linhas[0]["net_income"]


def test_marcador_de_banco_qualifica_a_serie_inteira():
    """Um ano sem `NoninterestIncome` nao muda a definicao de receita do banco."""
    from data_pipeline.us.edgar_facts import _e_intermediario_financeiro
    assert _e_intermediario_financeiro(_ano(**{"NoninterestIncome": 1.0}))
    assert _e_intermediario_financeiro(_ano(**{"InterestAndDividendIncomeOperating": 1.0}))
    assert not _e_intermediario_financeiro(_ano(**{"InterestIncomeExpenseNet": 1.0}))


def test_receita_bancaria_negativa_nao_substitui(caplog):
    import logging

    from data_pipeline.us import edgar_facts as ef
    cf = _ano(**{"RevenueFromContractWithCustomerExcludingAssessedTax": 1_000_000.0,
              "NoninterestIncome": -5_000_000.0, "NetIncomeLoss": 100_000.0})
    with caplog.at_level(logging.WARNING, logger=ef.__name__):
        linhas = ef.build_income_rows(cf, "X")
    assert linhas[0]["revenue"] == 1_000_000.0
    assert "negativa" in caplog.text

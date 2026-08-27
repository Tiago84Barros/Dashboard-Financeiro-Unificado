# -*- coding: utf-8 -*-
"""A-148: um zero na tag preferida bloqueava a tag que tinha o valor.

A prioridade entre aliases era "primeiro que TIVER dado", e `val: 0` conta
como ter dado. A Eaton publica `Revenues` = 0 nos exercicios de 2014 a 2016,
uma linha de rollup vazia, ao lado de `SalesRevenueNet` = US$ 20,9 bilhoes --
o numero real. O parser parava no zero e gravava cinco anos de receita zero
para uma industria de US$ 24 bilhoes. Mesmo padrao em Flowserve, Compass
Minerals e Assured Guaranty. Sao 573 linhas anuais com receita zero.
"""
from __future__ import annotations

from data_pipeline.us import edgar_facts as ef


def _cf(**por_tag):
    """companyfacts minimo: {tag: [(inicio, fim, valor)]}."""
    return {"facts": {"us-gaap": {
        tag: {"units": {"USD": [
            {"start": i, "end": f, "val": v, "form": "10-K", "fy": int(f[:4]),
             "fp": "FY", "filed": f"{int(f[:4]) + 1}-02-15"}
            for i, f, v in pontos]}}
        for tag, pontos in por_tag.items()}}}


def _receita(cf, ano="2015-12-31"):
    return ef._collect(cf, ef.INCOME_CONCEPTS)["revenue"].get(ano, {}).get("val")


def test_o_caso_eaton_o_zero_cede_ao_valor_real():
    cf = _cf(Revenues=[("2015-01-01", "2015-12-31", 0)],
             SalesRevenueNet=[("2015-01-01", "2015-12-31", 20_855_000_000)])
    assert _receita(cf) == 20_855_000_000


def test_sem_a_correcao_a_ordem_e_preservada_quando_a_preferida_tem_valor():
    """A prioridade continua valendo: quem tem valor primeiro vence."""
    cf = _cf(Revenues=[("2015-01-01", "2015-12-31", 500_000_000)],
             SalesRevenueNet=[("2015-01-01", "2015-12-31", 20_855_000_000)])
    assert _receita(cf) == 500_000_000


def test_empresa_pre_receita_continua_com_receita_zero():
    """A biotech que nao fatura nao tem alias com valor; nada e inventado."""
    cf = _cf(RevenueFromContractWithCustomerExcludingAssessedTax=[
                 ("2015-01-01", "2015-12-31", 0)],
             Revenues=[("2015-01-01", "2015-12-31", 0)])
    assert _receita(cf) == 0


def test_zero_isolado_permanece_zero():
    cf = _cf(Revenues=[("2015-01-01", "2015-12-31", 0)])
    assert _receita(cf) == 0


def test_o_zero_nao_cede_em_outro_campo():
    """Lucro zero e um fato contabil comum; a regra vale so para receita."""
    cf = _cf(NetIncomeLoss=[("2015-01-01", "2015-12-31", 0)],
             ProfitLoss=[("2015-01-01", "2015-12-31", 123_000_000)])
    assert ef._collect(cf, ef.INCOME_CONCEPTS)["net_income"][
        "2015-12-31"]["val"] == 0


def test_a_cessao_e_por_periodo_e_nao_contamina_os_vizinhos():
    cf = _cf(Revenues=[("2014-01-01", "2014-12-31", 0),
                       ("2015-01-01", "2015-12-31", 900_000_000)],
             SalesRevenueNet=[("2014-01-01", "2014-12-31", 700_000_000),
                              ("2015-01-01", "2015-12-31", 20_000_000_000)])
    assert _receita(cf, "2014-12-31") == 700_000_000
    assert _receita(cf, "2015-12-31") == 900_000_000


def test_receita_negativa_na_preferida_nao_e_tratada_como_vazio():
    """Receita negativa e outro defeito (A-142/A-143) e tem gate proprio;
    esta regra nao deve mascara-la trocando por outro alias em silencio."""
    cf = _cf(Revenues=[("2015-01-01", "2015-12-31", -82_400_000)],
             SalesRevenueNet=[("2015-01-01", "2015-12-31", 20_000_000_000)])
    assert _receita(cf) == -82_400_000

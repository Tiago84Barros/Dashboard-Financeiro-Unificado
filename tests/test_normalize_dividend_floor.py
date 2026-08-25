# -*- coding: utf-8 -*-
"""Piso de valor de provento: a régua é a coluna, não o Python.

Regressão real: a brapi devolve resíduos como rate=1e-10. O guard antigo era
`amount <= 0`, que os aprova; numeric(18,6) os arredonda para 0.000000 e
chk_dividends_amount_positive rejeita a linha. Como ingest_ticker grava o
ticker inteiro numa transação só, isso derrubava também o PREÇO — PETR4 ficou
sem atualizar por causa de um provento de 2006 valendo 1e-10.
"""
from data_pipeline.market.normalize import AMOUNT_MINIMO, dividend_rows


def _quote(*rates):
    return {
        "symbol": "PETR4",
        "dividendsData": {"cashDividends": [
            {"rate": r, "paymentDate": "2026-01-10", "lastDatePrior": "2026-01-02",
             "label": "DIVIDENDO", "remarks": ""} for r in rates]},
    }


def test_residuo_que_arredonda_para_zero_e_descartado():
    assert dividend_rows(_quote(1e-10)) == []
    assert dividend_rows(_quote(4e-7)) == []


def test_valor_no_piso_sobrevive():
    """O piso é uma unidade cheia da última casa, então não depende de a
    linguagem arredondar meio-para-par e o banco meio-para-longe-do-zero."""
    linhas = dividend_rows(_quote(AMOUNT_MINIMO))
    assert len(linhas) == 1
    assert round(linhas[0]["amount"], 6) > 0


def test_piso_nao_derruba_provento_de_centavo():
    """Guard apertado demais silenciaria provento pequeno porém real."""
    linhas = dividend_rows(_quote(0.01))
    assert [l["amount"] for l in linhas] == [0.01]


def test_negativo_e_zero_continuam_fora():
    assert dividend_rows(_quote(0.0, -1.5)) == []

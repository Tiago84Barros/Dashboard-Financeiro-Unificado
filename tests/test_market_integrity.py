"""Detector de ecos de dividendos (data_pipeline.market.integrity).

Cobre a derivação PURA das chaves a apagar/detectar (sem banco): quais linhas
do cashDividends o normalizador atual descarta e a forma EXATA em que ficaram
gravadas (event_date = COALESCE(payment saneado, ex); amount half-up 6 casas).
"""
from data_pipeline.market import integrity


def test_drop_keys_caso_ceb():
    # payload da CEBR5: linha confirmada (PNA) + eco da PNB (fonte CSV).
    items = [
        {"lastDatePrior": "2025-09-09T03:00:00.000Z",
         "paymentDate": "2025-09-17T03:00:00.000Z",
         "rate": 1.6657445, "label": "DIVIDENDO", "remarks": ""},
        {"lastDatePrior": "2025-09-09T03:00:00.000Z",
         "paymentDate": "2025-09-09T03:00:00.000Z",
         "rate": 1.832319, "label": "DIVIDENDO", "remarks": "csv:payment_date_estimated"},
    ]
    keys = integrity.dividend_drop_keys(items, {"CEBR5"})
    # só o eco da PNB é chave a apagar; event_date = paymentDate saneado
    assert keys == {("CEBR5", "2025-09-09", "DIVIDENDO", "1.832319")}


def test_drop_keys_half_up_bate_com_numeric():
    # 1.6657445 -> banker's do Python daria 1.665744; NUMERIC(18,6) é half-up
    # (1.665745). A chave PRECISA ser half-up p/ casar com o valor no banco.
    items = [
        {"lastDatePrior": "2025-01-02", "rate": 2.0, "label": "DIVIDENDO", "remarks": ""},
        {"lastDatePrior": "2025-01-02", "rate": 1.6657445, "label": "DIVIDENDO",
         "remarks": "csv:payment_date_estimated"},
    ]
    keys = integrity.dividend_drop_keys(items, {"XPTO3"})
    assert ("XPTO3", "2025-01-02", "DIVIDENDO", "1.665745") in keys


def test_drop_keys_orfa_csv_permanece():
    # eco sem par confirmado na mesma (data-ex, label) NÃO é chave a apagar.
    items = [
        {"lastDatePrior": "2020-05-05", "rate": 0.5, "label": "JCP",
         "remarks": "csv:payment_date_estimated"},
    ]
    assert integrity.dividend_drop_keys(items, {"XPTO3"}) == set()


def test_drop_keys_multi_ticker():
    # renormalizações antigas gravaram sob símbolo divergente: cobre os dois.
    items = [
        {"lastDatePrior": "2025-03-03", "rate": 1.0, "label": "DIVIDENDO", "remarks": ""},
        {"lastDatePrior": "2025-03-03", "rate": 1.1, "label": "DIVIDENDO",
         "remarks": "unconfirmed-by-third-party"},
    ]
    keys = integrity.dividend_drop_keys(items, {"AXIA3", "ELET3"})
    assert keys == {("AXIA3", "2025-03-03", "DIVIDENDO", "1.100000"),
                    ("ELET3", "2025-03-03", "DIVIDENDO", "1.100000")}

"""LPA (eps) e lucro de market.income_statements a partir do payload da brapi.

A brapi copia o LPA da DFP multiplicado pela ESCALA_MOEDA da companhia: quem
reporta em MIL sai x1000 (PETR4 8540 = R$ 8,54); quem reporta em UNIDADE sai
em reais (VIVA3 2,63529). Dividir tudo por 1000 deixava VIVA3/MTSA4 ~1000x
menores. A escala é decidida pela própria empresa — lucro / ações em
circulação do exercício mais recente — e um exercício isolado reportado por
lote de mil ações (ITUB4 2019) é corrigido pelo mesmo implícito.
Valores dos fixtures: payloads reais do armazém local (08/10/2026).
"""
import data_pipeline.market.normalize as nz


def _q(symbol, anuais, shares=None, trimestrais=()):
    q = {"symbol": symbol, "incomeStatementHistory": list(anuais),
         "incomeStatementHistoryQuarterly": list(trimestrais)}
    if shares is not None:
        q["defaultKeyStatistics"] = {"sharesOutstanding": shares}
    return q


def _eps(rows, ano, period="annual"):
    return next(r["eps"] for r in rows if r["year"] == ano and r["period"] == period)


def test_empresa_em_mil_divide_por_mil():
    q = _q("PETR4", [{"endDate": "2025-12-31", "netIncome": 110_605_000_000,
                      "netIncomeApplicableToCommonShares": 110_129_000_000,
                      "basicEarningsPerCommonShare": 8540,
                      "basicEarningsPerPreferredShare": 8540}],
           shares=12_888_733_000)
    assert abs(_eps(nz.income_rows(q), 2025) - 8.54) < 1e-9


def test_empresa_em_unidade_nao_divide():
    # VIVA3: 619,5 mi / 236,2 mi ações = 2,62 — a brapi já dá 2,63529 em reais
    q = _q("VIVA3", [
        {"endDate": "2025-12-31", "netIncome": 619_502_300,
         "basicEarningsPerCommonShare": 2.63529},
        {"endDate": "2024-12-31", "netIncome": 656_000_000,
         "basicEarningsPerCommonShare": 2.77940}], shares=236_197_780)
    rows = nz.income_rows(q)
    assert abs(_eps(rows, 2025) - 2.63529) < 1e-9
    assert abs(_eps(rows, 2024) - 2.7794) < 1e-9


def test_classe_pn_usa_lpa_preferencial():
    item = {"endDate": "2025-12-31", "netIncome": 40_684_240,
            "basicEarningsPerCommonShare": 4.53424,
            "basicEarningsPerPreferredShare": 4.98766}
    assert abs(_eps(nz.income_rows(_q("MTSA4", [item], 8_789_730)), 2025) - 4.98766) < 1e-9
    assert abs(_eps(nz.income_rows(_q("MTSA3", [item], 8_789_730)), 2025) - 4.53424) < 1e-9


def test_lpa_ordinario_zerado_cai_para_o_preferencial():
    # VALE3: a brapi grava 0 no campo ordinário e o LPA real no preferencial
    q = _q("VALE3", [{"endDate": "2025-12-31", "netIncome": 13_800_000_000,
                      "basicEarningsPerCommonShare": 0,
                      "basicEarningsPerPreferredShare": 3240}],
           shares=4_268_000_000)
    assert abs(_eps(nz.income_rows(q), 2025) - 3.24) < 1e-9


def test_exercicio_por_lote_de_mil_acoes():
    # ITUB4 2019: DFP antiga com LPA por lote de mil -> brapi 2.780.000
    anuais = [
        {"endDate": "2025-12-31", "netIncomeFromContinuingOps": 45_849_000_000,
         "basicEarningsPerPreferredShare": 4050},
        {"endDate": "2019-12-31", "netIncomeFromContinuingOps": 27_813_000_000,
         "basicEarningsPerPreferredShare": 2_780_000},
    ]
    rows = nz.income_rows(_q("ITUB4", anuais, shares=11_311_000_000))
    assert abs(_eps(rows, 2025) - 4.05) < 1e-9
    assert abs(_eps(rows, 2019) - 2.78) < 1e-9


def test_sem_acoes_mantem_a_regra_antiga():
    # Sem sharesOutstanding não há como decidir: mantém /1000 (maioria em MIL)
    q = _q("PETR4", [{"endDate": "2025-12-31", "netIncome": 100.0,
                      "basicEarningsPerCommonShare": 8540}])
    assert abs(_eps(nz.income_rows(q), 2025) - 8.54) < 1e-9


def test_base_de_acoes_nao_confiavel_nao_corrige_lote():
    # AZUL3: emissão maciça — ações de hoje não servem para o passado. Se o
    # exercício mais recente não fecha com lucro/ações, nada é "corrigido".
    anuais = [
        {"endDate": "2025-12-31", "netIncome": 1_000_000_000,
         "basicEarningsPerCommonShare": 160},
        {"endDate": "2020-12-31", "netIncome": -10_800_000_000,
         "basicEarningsPerCommonShare": -420},
    ]
    # Com estas ações, 2020 pareceria "lote de mil" (razão ~1000) — não é.
    rows = nz.income_rows(_q("AZUL3", anuais, shares=25_700_000_000_000))
    assert abs(_eps(rows, 2020) - (-0.42)) < 1e-9


def test_lucro_cai_para_operacoes_continuadas():
    # ITUB4/BPAC11: netIncome ausente; o lucro vem em netIncomeFromContinuingOps
    q = _q("ITUB4", [{"endDate": "2025-12-31",
                      "netIncomeFromContinuingOps": 45_849_000_000,
                      "netIncomeApplicableToCommonShares": 44_857_000_000}])
    assert nz.income_rows(q)[0]["net_income"] == 45_849_000_000


def test_demonstracao_toda_zerada_vira_ausencia():
    # TIMS3 2024/2025: a brapi devolve o exercício com todos os campos em 0
    zerado = {"endDate": "2025-12-31", "totalRevenue": 0, "netIncome": 0,
              "ebit": 0, "grossProfit": 0, "basicEarningsPerCommonShare": 0,
              "earningsPerShare": None}
    row = nz.income_rows(_q("TIMS3", [zerado]))[0]
    assert row["year"] == 2025
    assert row["revenue"] is None and row["net_income"] is None
    assert row["ebit"] is None and row["eps"] is None

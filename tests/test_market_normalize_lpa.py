"""LPA (eps) e lucro de market.income_statements a partir do payload da brapi.

A brapi copia o LPA da DFP multiplicado pela ESCALA_MOEDA da companhia: quem
reporta em MIL sai x1000 (PETR4 8540 = R$ 8,54); quem reporta em UNIDADE sai
em reais (VIVA3 2,63529). Dividir tudo por 1000 deixava VIVA3/MTSA4 ~1000x
menores. A escala é ancorada no exercício recente que fecha com lucro / ações
em circulação de hoje e encadeada para trás pelas ações implícitas (lucro /
LPA bruto) de cada exercício: elas só mudam de um ano para o outro por
desdobramento, grupamento ou emissão, nunca por 1000. Um exercício reportado
por lote de mil ações (ITUB4 2019) ou em milésimos (BRSR3 2010-14) é corrigido
pela mesma cadeia; o que não encaixa em escala nenhuma vira nulo.
Valores dos fixtures: payloads reais do armazém local (08/10 e 27/10/2026).
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


def test_empresa_que_passou_a_reportar_em_reais_corrige_o_lote_antigo():
    # Payload real do ITUB4 (28/10/2026): 2025 já em reais, 2024 em MIL e 2019
    # em MIL e por lote de mil ações -- 1e6 acima do implícito. Com a escala
    # decidida por 2025 (1.0), 2019 saía 2.780.000, passava do teto e virava
    # nulo: o ITUB4 sumia do P/L mediano de abr/2020 a mar/2021.
    anuais = [
        {"endDate": "2025-12-31", "netIncomeApplicableToCommonShares": 44_857_000_000,
         "basicEarningsPerPreferredShare": 4.05},
        {"endDate": "2024-12-31", "netIncomeApplicableToCommonShares": 41_085_000_000,
         "basicEarningsPerPreferredShare": 4200},
        {"endDate": "2019-12-31", "netIncomeApplicableToCommonShares": 27_113_000_000,
         "basicEarningsPerPreferredShare": 2_780_000},
    ]
    rows = nz.income_rows(_q("ITUB4", anuais, shares=11_026_869_000))
    assert abs(_eps(rows, 2025) - 4.05) < 1e-9
    assert abs(_eps(rows, 2024) - 4.2) < 1e-9
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


def _a(ano, lpa, lucro, campo="basicEarningsPerCommonShare"):
    return {"endDate": f"{ano}-12-31", "netIncome": lucro, campo: lpa}


def test_desdobramento_depois_da_dfp_ancora_no_trimestre():
    # SBSP3: desdobrou 1:5 em abr/2026. A DFP de 2025 já veio em UNIDADE, mas na
    # base antiga (razão 5 contra as ações de hoje); o ITR de 1T26 fecha. Sem a
    # âncora no trimestre, 2025 caía na regra antiga e gravava 0,01237.
    anuais = [_a(2025, 12.37, 8_462_059_000), _a(2024, 14020.0, 9_579_563_000),
              _a(2012, 8391.56, 1_911_900_000)]
    tri = [{"endDate": "2026-03-31", "netIncome": 1_749_031_000,
            "basicEarningsPerCommonShare": 0.5}]
    rows = nz.income_rows(_q("SBSP3", anuais, shares=3_524_534_000, trimestrais=tri))
    assert abs(_eps(rows, 2025) - 12.37) < 1e-9
    assert abs(_eps(rows, 2024) - 14.02) < 1e-9
    # desdobramento de 1:3 em 2013: muda a base, não a escala
    assert abs(_eps(rows, 2012) - 8.39156) < 1e-9


def test_desdobramentos_e_grupamento_nao_trocam_a_escala():
    # MGLU3: 2025 em UNIDADE, até 2024 em MIL. Grupamento de 10:1 em 2024 e
    # desdobramentos em 2017, 2019 e 2020 deixavam a razão contra as ações de
    # hoje em 2,1 ou 4,5 — nem MIL nem lote de mil. A regra de #590 gravava
    # -146 em 2023 (prejuízo de 146 reais por ação).
    anuais = [_a(2025, 0.264, 204_603_000), _a(2024, 610.0, 448_717_000),
              _a(2023, -146.0, -979_104_000), _a(2020, 61.0, 391_709_000),
              _a(2019, 569.0, 921_827_970), _a(2018, 3162.0, 597_429_000),
              _a(2016, 3980.0, 86_565_000), _a(2012, 36.17, -6_745_000),
              _a(2010, 458.89, 68_834_000)]
    rows = nz.income_rows(_q("MGLU3", anuais, shares=775_945_000))
    esperado = {2025: 0.264, 2024: 0.61, 2023: -0.146, 2020: 0.061, 2019: 0.569,
                2018: 3.162, 2016: 3.98, 2010: 0.45889}
    for ano, v in esperado.items():
        assert abs(_eps(rows, ano) - v) < 1e-9, ano
    # LPA positivo com prejuízo: um dos dois está errado, não há como saber qual
    assert _eps(rows, 2012) is None


def test_exercicios_em_milesimos_e_pico_isolado():
    # BRSR3: 2010-14 sai 1000x menor (0,00152 = R$ 1,52) e 2020 sai 310.665,
    # que não fecha em escala nenhuma com os vizinhos.
    anuais = [_a(2025, 4.19, 1_604_272_000), _a(2021, 2.65, 1_084_195_000),
              _a(2020, 310665.0, 727_476_000), _a(2019, 3.1, 1_269_426_000),
              _a(2014, 0.00152, 620_654_000), _a(2013, 0.00183, 748_803_000)]
    rows = nz.income_rows(_q("BRSR3", anuais, shares=408_974_460))
    assert abs(_eps(rows, 2025) - 4.19) < 1e-9
    assert abs(_eps(rows, 2021) - 2.65) < 1e-9
    assert _eps(rows, 2020) is None
    assert abs(_eps(rows, 2019) - 3.1) < 1e-9
    assert abs(_eps(rows, 2014) - 1.52) < 1e-9
    assert abs(_eps(rows, 2013) - 1.83) < 1e-9


def test_escala_rara_volta_para_a_comum_no_exercicio_anterior():
    # CBEE3: só 2012 sai em milésimos (0,00014 = R$ 0,14); 2011 volta a UNIDADE.
    # As ações de 2011-14 são ~9x as de hoje (grupamento depois): k=0 fica.
    anuais = [_a(2024, 2.4148, 670_153_000), _a(2014, 0.04967, 194_830_000),
              _a(2013, 0.13131, 515_059_000), _a(2012, 0.00014, 493_376_000),
              _a(2011, 0.05, 210_352_000)]
    rows = nz.income_rows(_q("CBEE3", anuais, shares=453_223_940))
    assert abs(_eps(rows, 2013) - 0.13131) < 1e-9
    assert abs(_eps(rows, 2012) - 0.14) < 1e-9
    assert abs(_eps(rows, 2011) - 0.05) < 1e-9


def test_base_que_muda_muito_sem_trocar_escala():
    # CEED3: as ações de 2015 são 40x as de 2016 (razão 1,6 em log10). O k mais
    # perto seria -1 (1,4), que transformaria -1,33 em -1.330; mantém UNIDADE.
    anuais = [_a(2021, -5.7759, -394_234_000), _a(2020, -167.64, -1_622_898_000),
              _a(2016, -54.46, -527_179_000), _a(2015, -1.33, -514_244_000)]
    rows = nz.income_rows(_q("CEED3", anuais, shares=68_254_930))
    assert abs(_eps(rows, 2016) - (-54.46)) < 1e-9
    assert abs(_eps(rows, 2015) - (-1.33)) < 1e-9


def test_grupamento_junto_com_troca_de_escala():
    # HAPV3: 2025 em UNIDADE depois de grupamento de 15:1; 2024 em MIL. A razão
    # 1,89 junta as duas mudanças e ainda é MIL.
    anuais = [_a(2025, -0.28, -141_747_000), _a(2024, -100.0, -649_301_000)]
    rows = nz.income_rows(_q("HAPV3", anuais, shares=502_630_880))
    assert abs(_eps(rows, 2024) - (-0.1)) < 1e-9


def test_desdobramento_antigo_em_mil():
    # TOTS3: 2025 em UNIDADE, até 2024 em MIL; #590 gravava 2019 = 1160,46 e
    # 2010 = 4410 porque os desdobramentos afastavam a razão de 3.
    anuais = [_a(2025, 1.51902, 891_741_000), _a(2024, 1205.89, 717_513_000),
              _a(2019, 1160.46, 209_796_000), _a(2010, 4410.0, 137_863_010)]
    rows = nz.income_rows(_q("TOTS3", anuais, shares=579_401_600))
    assert abs(_eps(rows, 2024) - 1.20589) < 1e-9
    assert abs(_eps(rows, 2019) - 1.16046) < 1e-9
    assert abs(_eps(rows, 2010) - 4.41) < 1e-9


def test_lpa_lixo_nao_vira_referencia():
    # AMAR3 2021 (-2,7e16) e MEAL3 2021 (-2,9e11) não fecham em escala nenhuma:
    # viram nulo e o exercício anterior continua comparado ao seguinte.
    amar = nz.income_rows(_q("AMAR3", [
        _a(2022, -1.5719, -519_382_000), _a(2021, -2.744e16, -71_754_000),
        _a(2020, -1.65335, -432_196_000)], shares=513_516_300))
    assert _eps(amar, 2021) is None
    assert abs(_eps(amar, 2020) - (-1.65335)) < 1e-9
    meal = nz.income_rows(_q("MEAL3", [
        _a(2022, 254.58, 72_669_000), _a(2021, -285_444_400_000.0, -80_449_000),
        _a(2020, -1649.84, -473_577_980)], shares=286_676_540))
    assert abs(_eps(meal, 2022) - 0.25458) < 1e-9
    assert _eps(meal, 2021) is None
    assert abs(_eps(meal, 2020) - (-1.64984)) < 1e-9


def test_escala_rara_exige_encaixe_justo():
    # NUTR3 2022 (63.020) e PETZ3 2024 (31.350) chegavam a k=2 a ~1 de log10 do
    # vizinho; lote de mil de verdade (ITUB4 2019, BRSR3 2014) fecha a < 0,1.
    nutr = nz.income_rows(_q("NUTR3", [
        _a(2025, 0.41, 5_386_000), _a(2024, 780.0, 10_276_000),
        _a(2023, 260.0, 3_507_000), _a(2022, 63020.0, 8_319_000)], shares=13_244_400))
    assert abs(_eps(nutr, 2024) - 0.78) < 1e-9
    assert abs(_eps(nutr, 2023) - 0.26) < 1e-9
    assert _eps(nutr, 2022) is None
    petz = nz.income_rows(_q("PETZ3", [
        _a(2025, 0.26, 109_339_000), _a(2024, 31350.0, 116_793_000)], shares=860_793_800))
    assert abs(_eps(petz, 2025) - 0.26) < 1e-9
    assert _eps(petz, 2024) is None


def test_ancora_trimestral_acumulada_no_ano_nao_desloca_a_referencia():
    # AVLL3: grupamento em jun/2026; o ITR de 2T26 traz o LPA do semestre
    # (-7,08) e ancora, mas a referência é a das ações de hoje. Medida pelo
    # trimestre, 2025 (base antiga, -0,64) passava de _TOL_TROCA e virava nulo.
    tri = [{"endDate": "2026-06-30", "netIncome": -20_617_000,
            "basicEarningsPerCommonShare": -7.08}]
    rows = nz.income_rows(_q("AVLL3", [_a(2025, -0.64, -125_700_000),
                                       _a(2024, 0.44, 66_144_000)],
                             shares=7_813_202, trimestrais=tri))
    assert abs(_eps(rows, 2025) - (-0.64)) < 1e-9
    assert abs(_eps(rows, 2024) - 0.44) < 1e-9

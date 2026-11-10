"""Bancos e seguradoras têm plano de contas próprio, na brapi e na DFP.

Na brapi, a DRE de banco não traz ``ebit``. O ``operatingIncome`` dela é a
linha de despesas operacionais (``grossProfit + operatingIncome`` dá o
``incomeBeforeTax``). Usá-lo como EBIT punha a margem operacional dos bancos
negativa, em vez da do Status Invest e do Investidor10, que é o resultado
antes dos tributos sobre a receita.

Na DFP, os códigos fixos 2.03, 3.05 e 3.11 só valem no plano das empresas
comerciais. Nos bancos, o PL fica em 2.07 ou 2.08 e o lucro em 3.11 ou 3.09.
Nas seguradoras, o EBIT fica em 3.07 e o lucro em 3.13. As descrições das
contas abaixo são as da DFP; todos os números são sintéticos.
"""
from datetime import datetime, timezone

import pandas as pd

import data_pipeline.market.normalize as nz
from data_pipeline.market import b3_saidas as bs
from data_pipeline.market.metrics import compute_snapshot


def _ebit(dre: dict):
    q = {"symbol": "XPTO3",
         "incomeStatementHistory": [{"endDate": "2025-12-31", **dre}]}
    return nz.income_rows(q)[0]["ebit"]


# ---------------------------------------------------------------- brapi

def test_dre_de_banco_usa_o_resultado_antes_dos_tributos_como_ebit():
    # sem ebit; operatingIncome = despesas operacionais; gp + oi = ibt
    assert _ebit({"totalRevenue": 1000.0, "grossProfit": 300.0,
                  "operatingIncome": -180.0, "totalOperatingExpenses": -180.0,
                  "incomeBeforeTax": 120.0, "netIncome": 90.0}) == 120.0


def test_dre_de_banco_sem_total_de_despesas_tambem():
    # nos itens de 4º trimestre a brapi omite totalOperatingExpenses
    assert _ebit({"totalRevenue": 1000.0, "grossProfit": 300.0,
                  "operatingIncome": -180.0, "incomeBeforeTax": 120.0}) == 120.0


def test_dre_de_banco_com_resultado_bruto_negativo():
    # trimestre em que a intermediação perde e as outras receitas compensam
    assert _ebit({"totalRevenue": 1000.0, "grossProfit": -20.0,
                  "operatingIncome": 50.0, "incomeBeforeTax": 30.0}) == 30.0


def test_dre_de_banco_com_despesa_explicita_que_nao_fecha():
    # exercícios antigos: operatingIncome = totalOperatingExpenses, mas há
    # outra linha entre o resultado bruto e o resultado antes dos tributos
    assert _ebit({"totalRevenue": 1500.0, "grossProfit": 500.0,
                  "operatingIncome": -110.0, "totalOperatingExpenses": -110.0,
                  "incomeBeforeTax": 350.0}) == 350.0


def test_dre_de_banco_com_ebit_zero_e_ausencia():
    assert _ebit({"totalRevenue": 1000.0, "grossProfit": 300.0, "ebit": 0,
                  "operatingIncome": -180.0, "incomeBeforeTax": 120.0}) == 120.0


def test_dre_comercial_mantem_o_ebit():
    assert _ebit({"totalRevenue": 1000.0, "grossProfit": 400.0, "ebit": 150.0,
                  "operatingIncome": 150.0, "incomeBeforeTax": 130.0}) == 150.0


def test_dre_comercial_sem_ebit_usa_o_operating_income():
    # operatingIncome aqui é o lucro operacional: gp + oi não dá o ibt
    assert _ebit({"totalRevenue": 1000.0, "grossProfit": 400.0,
                  "operatingIncome": 150.0, "incomeBeforeTax": 130.0}) == 150.0


def test_dre_com_despesa_igual_ao_lucro_bruto_nao_e_banco():
    # gp + oi = 0 = ibt só por coincidência não basta: ibt precisa existir e
    # o ebit precisa faltar; aqui o ebit existe
    assert _ebit({"totalRevenue": 1000.0, "grossProfit": 200.0, "ebit": 10.0,
                  "operatingIncome": -200.0, "incomeBeforeTax": 0.0}) == 10.0


def test_margem_operacional_de_banco_e_positiva():
    ebit = _ebit({"totalRevenue": 1000.0, "grossProfit": 300.0,
                  "operatingIncome": -180.0, "incomeBeforeTax": 120.0})
    snap = compute_snapshot({"revenue": 1000.0, "ebit": ebit, "net_income": 90.0})
    assert abs(snap["Margem_Operacional"][0] - 0.12) < 1e-12


# ---------------------------------------------------------------- DFP

def _dem(contas: dict) -> bs.DemonstracaoAnual:
    return bs.DemonstracaoAnual(
        cd_cvm=1, ano=2025,
        available_at=pd.Timestamp(datetime(2026, 3, 1, tzinfo=timezone.utc)).tz_localize(None),
        consolidado=True, contas=contas)


_COMERCIAL = {
    "1": ("Ativo Total", 2000.0),
    "1.01": ("Ativo Circulante", 800.0),
    "2.01": ("Passivo Circulante", 400.0),
    "2.03": ("Patrimônio Líquido Consolidado", 900.0),
    "2.03.09": ("Participação dos Acionistas Não Controladores", 100.0),
    "3.01": ("Receita de Venda de Bens e/ou Serviços", 1000.0),
    "3.05": ("Resultado Antes do Resultado Financeiro e dos Tributos", 200.0),
    "3.07": ("Resultado Antes dos Tributos sobre o Lucro", 170.0),
    "3.11": ("Lucro/Prejuízo Consolidado do Período", 120.0),
    "3.11.01": ("Atribuído a Sócios da Empresa Controladora", 110.0),
}

# Banco, plano A (o do Banco do Brasil): PL em 2.07, lucro em 3.11
_BANCO_A = {
    "1": ("Ativo Total", 20000.0),
    "1.01": ("Caixa e Equivalentes de Caixa", 500.0),
    "2.01": ("Passivos Financeiros Avaliados ao Valor Justo através do Resultado", 300.0),
    "2.03": ("Provisões", 700.0),
    "2.07": ("Patrimônio Líquido Consolidado", 1500.0),
    "2.07.01": ("Patrimônio Líquido Atribuído ao Controlador", 1400.0),
    "2.07.02": ("Patrimônio Líquido Atribuído aos Não Controladores", 100.0),
    "3.01": ("Receitas de Intermediação Financeira", 3000.0),
    "3.05": ("Resultado antes dos Tributos sobre o Lucro", 400.0),
    "3.09": ("Lucro ou Prejuízo antes das Participações e Contribuições Estatutárias", 330.0),
    "3.11": ("Lucro ou Prejuízo Líquido Consolidado do Período", 300.0),
    "3.11.01": ("Atribuído aos Sócios da Empresa Controladora", 280.0),
}

# Banco, plano B (o do Itaú): PL em 2.08, lucro em 3.09, sem 3.11
_BANCO_B = {
    "1": ("Ativo Total", 30000.0),
    "1.01": ("Caixa e Equivalentes de Caixa", 600.0),
    "2.01": ("Passivos Financeiros ao Valor Justo através do Resultado", 900.0),
    "2.03": ("Passivos Financeiros ao Custo Amortizado", 25000.0),
    "2.08": ("Patrimônio Líquido Consolidado", 2500.0),
    "2.08.09": ("Participação dos Acionistas Não Controladores", 200.0),
    "3.01": ("Receitas da Intermediação Financeira", 4000.0),
    "3.05": ("Resultado Antes dos Tributos sobre o Lucro", 600.0),
    "3.07": ("Resultado Líquido das Operações Continuadas", 450.0),
    "3.09": ("Lucro/Prejuízo Consolidado do Período", 450.0),
    "3.09.01": ("Atribuído a Sócios da Empresa Controladora", 430.0),
}

# Seguradora: EBIT em 3.07, lucro em 3.13, PL em 2.03
_SEGURADORA = {
    "1": ("Ativo Total", 5000.0),
    "1.01": ("Ativo Circulante", 1500.0),
    "2.01": ("Passivo Circulante", 1000.0),
    "2.03": ("Patrimônio Líquido Consolidado", 1200.0),
    "2.03.09": ("Participação dos Acionistas Não Controladores", 50.0),
    "3.01": ("Receitas das Atividades Seguradoras/Resseguradoras", 2000.0),
    "3.05": ("Outras Receitas e Despesas Operacionais", -30.0),
    "3.07": ("Resultado Antes do Resultado Financeiro e dos Tributos", 350.0),
    "3.09": ("Resultado Antes dos Tributos sobre o Lucro", 420.0),
    "3.11": ("Resultado Líquido das Operações Continuadas", 310.0),
    "3.13": ("Lucro/Prejuízo Consolidado do Período", 310.0),
    "3.13.01": ("Atribuído a Sócios da Empresa Controladora", 300.0),
}


# Plano antigo das financeiras (demonstração individual): PL em 2.05, lucro
# em 3.13, 3.05 é o resultado operacional
_FINANCEIRA_ANTIGA = {
    "1": ("Ativo Total", 9000.0),
    "2.01": ("Passivo Circulante", 5000.0),
    "2.02": ("Passivo Exigível a Longo Prazo", 3000.0),
    "2.03": ("Resultados de Exercícios Futuros", 0.0),
    "2.05": ("Patrimônio Líquido", 1000.0),
    "3.01": ("Receitas da Intermediação Financeira", 1200.0),
    "3.05": ("Resultado Operacional", 150.0),
    "3.06": ("Resultado Não Operacional", 20.0),
    "3.07": ("Resultado Antes Tributação/Participações", 170.0),
    "3.11": ("Reversão dos Juros sobre Capital Próprio", 0.0),
    "3.13": ("Lucro/Prejuízo do Período", 120.0),
}


def test_dfp_financeira_no_plano_antigo():
    d = bs.DemonstracaoAnual(cd_cvm=1, ano=2022, available_at=pd.Timestamp("2023-03-01"),
                             consolidado=False, contas=_FINANCEIRA_ANTIGA)
    f = bs.fundamentos(d)
    # antes: PL dos Resultados de Exercícios Futuros (0) e lucro da reversão de JCP (0)
    assert (f["equity"], f["net_income"], f["ebit"]) == (1000.0, 120.0, 170.0)


def test_dfp_comercial_continua_igual():
    f = bs.fundamentos(_dem(_COMERCIAL))
    assert (f["equity"], f["net_income"], f["ebit"]) == (800.0, 110.0, 200.0)
    assert (f["current_assets"], f["current_liabilities"]) == (800.0, 400.0)


def test_dfp_banco_plano_a():
    f = bs.fundamentos(_dem(_BANCO_A))
    # PL do controlador (2.07.01), não as Provisões (2.03)
    assert f["equity"] == 1400.0
    assert f["net_income"] == 280.0
    # o resultado antes dos tributos, como na brapi e nos sites
    assert f["ebit"] == 400.0


def test_dfp_banco_plano_b():
    f = bs.fundamentos(_dem(_BANCO_B))
    # 2.08 menos os não controladores, não os passivos ao custo amortizado
    assert f["equity"] == 2300.0
    # 3.09.01: não há 3.11
    assert f["net_income"] == 430.0
    assert f["ebit"] == 600.0


def test_dfp_seguradora():
    f = bs.fundamentos(_dem(_SEGURADORA))
    assert f["equity"] == 1150.0
    # 3.13.01, não o 3.11 das operações continuadas
    assert f["net_income"] == 300.0
    # 3.07, não as outras receitas e despesas do 3.05
    assert f["ebit"] == 350.0


def test_dfp_banco_nao_tem_liquidez_corrente():
    # 1.01 de banco é caixa e 2.01 são passivos a valor justo: a razão entre
    # eles não é liquidez corrente, e os sites não a mostram
    for contas in (_BANCO_A, _BANCO_B):
        f = bs.fundamentos(_dem(contas))
        assert f["current_assets"] is None and f["current_liabilities"] is None


def test_dfp_individual_sem_consolidado_usa_o_total():
    # demonstração individual: sem filho dos controladores, o total vale
    contas = {k: v for k, v in _BANCO_B.items() if k not in ("3.09.01", "2.08.09")}
    contas["2.08"] = ("Patrimônio Líquido", 2500.0)
    contas["3.09"] = ("Lucro/Prejuízo do Período", 450.0)
    d = bs.DemonstracaoAnual(cd_cvm=1, ano=2025, available_at=pd.Timestamp("2026-03-01"),
                             consolidado=False, contas=contas)
    f = bs.fundamentos(d)
    assert (f["equity"], f["net_income"]) == (2500.0, 450.0)

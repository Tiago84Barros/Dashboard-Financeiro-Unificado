"""Lucro e PL das ações da B3 são os dos controladores, como na DFP.

O P/L e o P/VP dos sites (Fundamentus, Status Invest, Investidor10) e o LPA
da DFP usam o lucro atribuído aos controladores (3.11.01) e o PL dos
controladores (2.03 menos a participação dos não controladores). A brapi
grava o lucro consolidado em ``netIncome``, o dos controladores em
``netIncomeApplicableToCommonShares`` e o PL consolidado em
``shareholdersEquity``, com os minoritários à parte. Todos os números aqui
são sintéticos.
"""
from datetime import datetime, timezone

import pandas as pd

import data_pipeline.market.normalize as nz
from data_pipeline.market import b3_saidas as bs
from data_pipeline.market.metrics import compute_snapshot


def _quote(dre: dict | None = None, bal: dict | None = None) -> dict:
    q = {"symbol": "XPTO3"}
    if dre is not None:
        q["incomeStatementHistory"] = [{"endDate": "2025-12-31", **dre}]
    if bal is not None:
        q["balanceSheetHistory"] = [{"endDate": "2025-12-31", **bal}]
    return q


def _lucro(dre: dict):
    return nz.income_rows(_quote(dre=dre))[0]["net_income"]


def _pl(bal: dict):
    return nz.balance_rows(_quote(bal=bal))[0]["equity"]


# ---------------------------------------------------------------- lucro

def test_lucro_e_o_dos_controladores_quando_a_brapi_o_traz():
    assert _lucro({"netIncome": 100.0, "netIncomeApplicableToCommonShares": 80.0,
                   "netIncomeFromContinuingOps": 100.0}) == 80.0


def test_lucro_dos_controladores_zero_e_ausencia_e_cai_no_consolidado():
    # a brapi grava 0 no lugar de nulo
    assert _lucro({"netIncome": 100.0, "netIncomeApplicableToCommonShares": 0}) == 100.0


def test_lucro_sem_controladores_nem_consolidado_usa_operacoes_continuadas():
    assert _lucro({"netIncomeFromContinuingOps": 70.0}) == 70.0


def test_prejuizo_dos_controladores_vence_o_lucro_consolidado():
    # minoritários lucram, controladores perdem: o sinal que vale é o deles
    assert _lucro({"netIncome": 10.0, "netIncomeApplicableToCommonShares": -30.0}) == -30.0


def test_lucro_zero_de_todos_os_campos_continua_zero():
    # (com receita: a demonstração toda em 0 é ausência do provedor, ``_vazio``)
    assert _lucro({"totalRevenue": 500.0, "netIncome": 0,
                   "netIncomeApplicableToCommonShares": 0}) == 0


def test_lucro_e_lpa_implicito_usam_a_mesma_ordem():
    # um só tuplo de campos de lucro; o do LPA implícito não diverge mais
    assert not hasattr(nz, "_LUCRO_LPA")
    assert nz._LUCRO[0] == "netIncomeApplicableToCommonShares"


# ---------------------------------------------------------------- PL

def test_pl_tira_os_minoritarios_do_consolidado():
    assert _pl({"shareholdersEquity": 500.0, "minorityInterest": 50.0}) == 450.0


def test_pl_sem_minoritarios_e_o_consolidado():
    assert _pl({"shareholdersEquity": 500.0, "minorityInterest": 0}) == 500.0
    assert _pl({"shareholdersEquity": 500.0}) == 500.0


def test_pl_dos_controladores_explicito_vence():
    assert _pl({"shareholdersEquity": 500.0, "minorityInterest": 50.0,
                "controllerShareholdersEquity": 430.0}) == 430.0


def test_pl_tira_nao_controladores_quando_minority_interest_falta():
    assert _pl({"shareholdersEquity": 500.0, "minorityInterest": None,
                "nonControllingShareholdersEquity": 30.0}) == 470.0


def test_pl_controladores_zero_e_ausencia():
    assert _pl({"shareholdersEquity": 500.0, "minorityInterest": 50.0,
                "controllerShareholdersEquity": 0}) == 450.0


def test_pl_ausente_continua_ausente():
    assert _pl({"totalAssets": 1000.0}) is None


def test_pl_negativo_tira_minoritarios_tambem():
    assert _pl({"shareholdersEquity": -100.0, "minorityInterest": 20.0}) == -120.0


# ---------------------------------------------------------------- ROE e P/VP

def test_roe_e_pvp_usam_lucro_e_pl_dos_controladores():
    q = _quote(dre={"netIncome": 100.0, "netIncomeApplicableToCommonShares": 80.0},
               bal={"shareholdersEquity": 500.0, "minorityInterest": 100.0})
    inc = nz.income_rows(q)[0]
    bal = nz.balance_rows(q)[0]
    snap = compute_snapshot({"net_income": inc["net_income"], "equity": bal["equity"],
                             "market_cap": 800.0})
    assert abs(snap["ROE"][0] - 80.0 / 400.0) < 1e-12
    assert abs(snap["P/VP"][0] - 800.0 / 400.0) < 1e-12


# ---------------------------------------------------------------- gêmeo da CVM

def _dem(contas: dict) -> bs.DemonstracaoAnual:
    return bs.DemonstracaoAnual(
        cd_cvm=1, ano=2025,
        available_at=pd.Timestamp(datetime(2026, 3, 1, tzinfo=timezone.utc)).tz_localize(None),
        consolidado=True, contas=contas)


def test_cvm_lucro_dos_controladores_zero_cai_no_total():
    d = _dem({"3.11": ("Lucro/Prejuízo Consolidado do Período", 100.0),
              "3.11.01": ("Atribuído a Sócios da Empresa Controladora", 0.0)})
    assert bs.fundamentos(d)["net_income"] == 100.0


def test_cvm_lucro_dos_controladores_vence_o_total():
    d = _dem({"3.11": ("Lucro/Prejuízo Consolidado do Período", 100.0),
              "3.11.01": ("Atribuído a Sócios da Empresa Controladora", 80.0)})
    assert bs.fundamentos(d)["net_income"] == 80.0

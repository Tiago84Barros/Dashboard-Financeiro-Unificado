from datetime import date, datetime

import pandas as pd
import pytest

import core.carteira20 as c20
import core.carteira20_desempenho as cd
from scripts import carteira20_desempenho as pub

EXEC = date(2026, 10, 26)
DIAS = [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
CARTEIRA = [{"id": "A", "ticker": "AAAA3"}, {"id": "B", "ticker": "BBBB4"}]
UNIVERSO = CARTEIRA + [{"id": "C", "ticker": "CCCC3"}]


def _retornos(linhas):
    return pd.DataFrame(linhas, columns=["id", "data", "ret"])


def _bova(valores):
    return pd.Series(valores, index=[EXEC, *DIAS][:len(valores)], dtype=float)


def _cdi(dias=(EXEC, *DIAS[:2]), taxa=0.05):
    return {d: taxa for d in dias}


def test_base_da_execucao_ja_desconta_a_compra():
    ret = _retornos([("A", DIAS[0], 0.0), ("B", DIAS[0], 0.0), ("C", DIAS[0], 0.0)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 100.0]), _cdi(), CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 1 and r["datas"] == ["2026-10-26", "2026-10-27"]
    venda = cd.CAPITAL * (1 - cd.CUSTO_LADO) ** 2
    assert r["curvas"]["carteira20"][0] == pytest.approx(venda, abs=1e-4)
    assert r["curvas"]["bova11"][0] == pytest.approx(venda, abs=1e-4)
    assert r["curvas"]["cdi"][0] == cd.CAPITAL


def test_buy_and_hold_pesos_iguais_e_ir_dos_comparadores():
    ret = _retornos([("A", DIAS[0], 0.10), ("B", DIAS[0], -0.10), ("C", DIAS[0], 0.40),
                     ("A", DIAS[1], 0.10), ("B", DIAS[1], 0.0), ("C", DIAS[1], 0.0)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 110.0, 121.0]), _cdi(), CARTEIRA, UNIVERSO)
    por_acao = cd.CAPITAL * (1 - cd.CUSTO_LADO) / 2
    bruto = por_acao * 1.21 + por_acao * 0.9
    # abaixo de R$ 20 mil de venda no mês: ação isenta
    assert r["curvas"]["carteira20"][-1] == pytest.approx(bruto * (1 - cd.CUSTO_LADO), abs=1e-3)
    venda_etf = cd.CAPITAL * (1 - cd.CUSTO_LADO) ** 2 * 1.21
    assert r["curvas"]["bova11"][-1] == pytest.approx(
        venda_etf - (venda_etf - cd.CAPITAL) * cd.ALIQUOTA_IR, abs=1e-3)
    bruto_cdi = cd.CAPITAL * 1.0005 ** 2
    assert r["curvas"]["cdi"][-1] == pytest.approx(
        bruto_cdi - (bruto_cdi - cd.CAPITAL) * cd.ALIQUOTA_IR, abs=1e-3)
    ativos = {a["ticker"]: a for a in r["ativos"]}
    assert ativos["AAAA3"]["retorno"] == pytest.approx(0.21)
    assert ativos["AAAA3"]["peso_atual"] + ativos["BBBB4"]["peso_atual"] == pytest.approx(1.0)
    assert ativos["AAAA3"]["peso_atual"] == pytest.approx(1.21 / 2.11)


def test_isencao_so_vale_ate_vinte_mil():
    abaixo = cd._liquido_acoes(pd.Series([1100.0]))
    assert abaixo.iloc[0] == pytest.approx(1100 * (1 - cd.CUSTO_LADO))
    acima = cd._liquido_acoes(pd.Series([25_000.0]))
    venda = 25_000 * (1 - cd.CUSTO_LADO)
    assert acima.iloc[0] == pytest.approx(venda - (venda - cd.CAPITAL) * cd.ALIQUOTA_IR)


def test_lacuna_do_cdi_corta_as_curvas_e_avisa():
    ret = _retornos([(i, d, 0.01) for d in DIAS for i in "ABC"])
    r = cd.calcular(EXEC, ret, _bova([100.0, 101.0, 102.0, 103.0]), _cdi(dias=(EXEC,)),
                    CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 1 and r["datas"][-1] == "2026-10-27"
    assert all(len(v) == 2 for v in r["curvas"].values())
    assert any("CDI publicado só até 27/10/2026" in a for a in r["avisos"])


def test_acao_sem_dado_conta_zero_e_e_nomeada():
    ret = _retornos([("A", DIAS[0], 0.05), ("C", DIAS[0], 0.0)])
    r = cd.calcular(EXEC, ret, _bova([100.0, 100.0]), _cdi(), CARTEIRA, UNIVERSO)
    assert any("BBBB4" in a for a in r["avisos"])
    assert {a["ticker"]: a["retorno"] for a in r["ativos"]}["BBBB4"] == 0.0


def test_sem_pregao_depois_da_execucao_nao_publica():
    r = cd.calcular(EXEC, _retornos([]), _bova([100.0]), {}, CARTEIRA, UNIVERSO)
    assert r["pregoes"] == 0 and r["curvas"] == {}


def test_sem_fechamento_na_execucao_e_erro():
    ret = _retornos([("A", DIAS[0], 0.0)])
    bova = pd.Series([100.0], index=[DIAS[0]], dtype=float)
    with pytest.raises(ValueError, match="pregão de execução"):
        cd.calcular(EXEC, ret, bova, _cdi(), CARTEIRA, UNIVERSO)


def test_amostrar_diario_no_primeiro_ano_e_mensal_depois():
    curtas = [f"2026-11-{d:02d}" for d in range(1, 20)]
    assert cd.amostrar(curtas) == list(range(len(curtas)))
    longas = [d.date().isoformat() for d in pd.bdate_range("2026-10-26", periods=400)]
    idx = cd.amostrar(longas)
    assert idx[0] == 0 and idx[-1] == len(longas) - 1
    meses = [longas[i][:7] for i in idx[1:]]
    assert len(meses) == len(set(meses))  # um ponto por mês
    assert cd.retorno_acumulado([1000.0, 1100.0]) == [0.0, pytest.approx(0.1)]


def test_montar_grava_revisao_vigente_e_regras():
    regs = c20.ler(c20.REGISTRO)
    atual = c20.vigente(regs)
    if not atual:
        pytest.skip("registro sem revisão publicada")
    ids = [c["id"] for c in atual["corpo"]["carteira"]]
    ret = _retornos([(i, DIAS[0], 0.01) for i in ids])
    art = pub.montar(regs, retornos=lambda ids_, desde: ret,
                     bova=lambda desde: _bova([100.0, 101.0]),
                     cdi=lambda a, b: (_cdi(), "teste"),
                     agora=datetime(2026, 10, 27, 21, tzinfo=c20.FUSO_B3))
    assert art["revisao"]["hash"] == atual["registro"]["hash"]
    assert art["revisao"]["pregao_execucao"] == "2026-10-26"
    assert art["dados_ate"] == "2026-10-27" and art["pregoes"] == 1
    assert art["regras"]["custo_por_lado"] == cd.CUSTO_LADO
    assert set(art["curvas"]) == set(cd.SERIES)


def test_montar_sem_pregao_devolve_none():
    regs = c20.ler(c20.REGISTRO)
    if not c20.vigente(regs):
        pytest.skip("registro sem revisão publicada")
    art = pub.montar(regs, retornos=lambda ids_, desde: _retornos([]),
                     bova=lambda desde: _bova([100.0]), cdi=lambda a, b: ({}, "teste"),
                     agora=datetime(2026, 10, 26, 12, tzinfo=c20.FUSO_B3))
    assert art is None

"""Carteira "rota de valor" da aba B3: padrão da tela desde 26/11/2026."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import core.b3_comparacao_estrategias as ce
import core.b3_estrategias_fundamentalistas as fe
import views.portfolio_b3_rota_valor as rv

ROOT = Path(__file__).resolve().parents[1]


def _empresa(ticker: str, **campos) -> dict:
    """Saudável e cara por padrão (o mesmo molde dos testes da regra)."""
    base = {
        "Ticker": ticker,
        "P/L": 15.0, "P/VP": 2.0, "DY": 0.03, "ROE": 0.15,
        "ROIC": 0.15, "Margem_Operacional": 0.18, "Margem_Liquida": 0.12,
        "Endividamento_Total": 0.8, "Liquidez_Corrente": 1.8, "P_FCO": 10.0,
        "EV_EBIT": 8.0,
    }
    base.update(campos)
    return base


def _quadro() -> pd.DataFrame:
    return pd.DataFrame([
        _empresa("boa3", **{"P/L": 5.0, "P/VP": 0.8, "DY": 0.09}),
        _empresa("BOB4", **{"P/L": 6.0, "P/VP": 0.9, "DY": 0.08}),
        _empresa("CARA3"),
        _empresa("TRAP3", **{"P/L": 4.0, "P/VP": 0.5, "P_FCO": -3.0}),
        _empresa("FORA3", **{"P/L": 5.0, "P/VP": 0.8, "DY": 0.09}),
    ])


_RESULTADOS = [
    {"score_proximo": {"BOA3": 1.0, "CARA3": 0.5}},
    {"score_proximo": {"TRAP3": 0.2, "BOB4": 0.1, "SEMDADO3": 0.3}},
]


# ── Selic da safra: gêmea da medição ────────────────────────────────────────

def test_selic_da_safra_le_o_ano_anterior_como_a_medicao():
    selic = {2023: 0.1175, 2024: 0.1225, 2025: 0.15, 2026: 0.14}
    assert rv.selic_da_safra(selic, 2026, 0.10) == pytest.approx(0.15)
    # Ano anterior sem observação: a última antes dele.
    assert rv.selic_da_safra({2023: 0.1175}, 2026, 0.10) == pytest.approx(0.1175)
    # Nada até N-1: a taxa de reserva da aba.
    assert rv.selic_da_safra({2026: 0.14}, 2026, 0.10) == pytest.approx(0.10)
    assert rv.selic_da_safra(None, 2026, 0.10) == pytest.approx(0.10)


def test_selic_da_safra_igual_a_do_script_de_medicao():
    """Equivalência com `selic_ate` do script que gerou a evidência."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "medir_fe", ROOT / "scripts" / "medir_estrategias_fundamentalistas_b3.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    selic = {2016: 0.1375, 2019: 0.045, 2021: 0.0925, 2025: 0.15}
    for safra in range(2017, 2028):
        esperado = mod.selic_ate(selic, safra - 1)
        assert rv.selic_da_safra(selic, safra, 0.0) == pytest.approx(
            esperado if esperado is not None else 0.0), safra


# ── Carteira de hoje ────────────────────────────────────────────────────────

def test_formar_hoje_usa_a_regra_da_medicao_sobre_o_universo_da_aba():
    estado = rv.formar_hoje(_RESULTADOS, _quadro(), selic=0.15, perfil="Equilibrado (padrão)",
                            hoje="2026-11-26")
    universo = {"BOA3", "CARA3", "TRAP3", "BOB4", "SEMDADO3"}
    esperado = fe.carteira_rota_valor(_quadro(), universo, selic=0.15)
    assert sorted(estado["pesos"]) == esperado == ["BOA3", "BOB4"]
    assert estado["pesos"] == {"BOA3": 0.5, "BOB4": 0.5}
    assert estado["safra"] == 2026 and estado["previa"] is False
    assert estado["n_universo"] == 5 and estado["n_com_dado"] == 4
    assert estado["contagem"]["oportunidade"] == 2
    assert estado["contagem"]["armadilha_potencial"] == 1
    assert estado["versao"] == fe.VERSAO
    assert "FORA3" not in set(estado["tabela"]["Ticker"])


def test_formar_hoje_antes_de_abril_e_previa_e_sem_universo_e_none():
    estado = rv.formar_hoje(_RESULTADOS, _quadro(), selic=0.15, perfil="x", hoje="2027-02-10")
    assert estado["safra"] == 2027 and estado["previa"] is True
    assert rv.formar_hoje([], _quadro(), selic=0.15, perfil="x") is None
    assert rv.formar_hoje([{"score_proximo": {}}], _quadro(), selic=0.15, perfil="x") is None


def test_formar_hoje_sem_quadro_da_lista_vazia_sem_erro():
    estado = rv.formar_hoje(_RESULTADOS, None, selic=0.15, perfil="x", hoje="2026-11-26")
    assert estado["pesos"] == {} and estado["n_com_dado"] == 0
    assert rv.tabela_para_exibicao(estado["tabela"]).empty


def test_tabela_para_exibicao_so_oportunidades_na_ordem_da_rota():
    estado = rv.formar_hoje(_RESULTADOS, _quadro(), selic=0.15, perfil="x", hoje="2026-11-26")
    t = rv.tabela_para_exibicao(estado["tabela"])
    assert list(t.columns) == ["Ticker", "Nota da rota", "Desconto (%)",
                               "Folga de solvência", "Por quê"]
    assert set(t["Ticker"]) == set(estado["pesos"])
    assert list(t["Nota da rota"]) == sorted(t["Nota da rota"], reverse=True)
    assert (t["Desconto (%)"] >= 20).all()
    assert rv.tabela_para_exibicao(None).empty


# ── Evidência gravada ───────────────────────────────────────────────────────

def _medicao() -> dict:
    return json.loads((ROOT / "data" / "oos_comparacao_estrategias_b3.json")
                      .read_text(encoding="utf-8"))


def test_medicao_gravada_tem_o_braco_da_tela():
    dados = _medicao()
    assert rv.BRACO in dados["estrategias"]
    rota = dados["estrategias"][rv.BRACO]
    assert rota["decisao"]["candidata"] is True
    assert rota["vs_equal_weight"]["ic95"][0] > 0


def test_renda_ano_a_ano_alinha_rota_e_pesos_iguais_por_safra():
    dados = _medicao()
    t = rv.renda_ano_a_ano(dados)
    anos = dados["estrategias"][rv.BRACO]["renda"]["anos"]
    assert list(t["Safra"]) == [a["safra"] for a in anos]
    assert t["Renda da rota (R$)"].sum() == pytest.approx(
        dados["estrategias"][rv.BRACO]["renda"]["renda_total"])
    assert t["Renda de pesos iguais (R$)"].notna().all()
    assert rv.renda_ano_a_ano(None).empty


def test_avisos_da_medicao():
    dados = _medicao()
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    assert rv.avisos_da_medicao(None, None, "x", "y") == [
        "Ainda não há medição gravada desta carteira."]
    em_dia = rv.avisos_da_medicao(dados, dados["perfil"], dados["versao_metodologia"],
                                  dados["versao_presets"])
    assert em_dia == []
    velha = rv.avisos_da_medicao(dados, None, "0.0.0", dados["versao_presets"])
    assert len(velha) == 1 and "metodologia" in velha[0]
    outro = rv.avisos_da_medicao(dados, "Agressivo", dados["versao_metodologia"],
                                 dados["versao_presets"])
    assert len(outro) == 1 and "perfil" in outro[0]
    assert dados["versao_regua"] == ce.VERSAO
    # A medição gravada está em dia com a metodologia e os perfis atuais.
    assert dados["versao_metodologia"] == SCORE_VERSION
    assert dados["versao_presets"] == PRESETS_VERSION


def test_grau_de_confianca_mede_a_mesma_medicao_e_a_mesma_regra_de_vencida():
    """A nota do Grau de Confiança e a tela leem o mesmo arquivo e a mesma regra."""
    import core.confianca_secao as cs
    dados = _medicao()
    nota = cs.nota_medicao_rota_valor()
    assert "rota de valor" in nota and "oos_comparacao_estrategias_b3.json" in nota
    assert f"{len(dados['safras'])} safras" in nota
    assert "VENCIDA" not in nota
    velha = dict(dados, versao_metodologia="0.0.0")
    assert ce.motivos_vencida(velha, "9.9.9", dados["versao_presets"])
    assert rv.avisos_da_medicao(velha, None, "9.9.9", dados["versao_presets"])

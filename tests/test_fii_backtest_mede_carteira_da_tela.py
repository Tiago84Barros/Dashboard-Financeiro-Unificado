"""O backtest PIT mede a carteira que a tela mostra, publicavel ou nao.

Quando o otimizador monta carteira e o portao de transparencia recusa
publicar (`can_publish=False`), a tela (`views/fiis.py`) mostra a carteira
como "Rascunho nao publicavel", com pesos e motivos. O walk-forward que roda
em producao (`robust_optimizer_point_in_time_backtest`, chamado por
`data_pipeline/market/fii_pit.py`) descartava esse periodo, e a curva media
uma estrategia que so existia nos meses em que o portao abriu.

A regra testada e uma equivalencia: a recusa de publicacao muda a MARCACAO do
periodo, nunca o retorno dele. O periodo recusado tem de render exatamente o
que renderia se a mesma carteira fosse publicavel.
"""
import pandas as pd
import pytest

import core.fii_carteira_protegida as protegida
from core.fii_validation import (
    robust_optimizer_point_in_time_backtest,
    validate_methodology,
)
from tests.test_fii_robusto_saida_de_campo import DATAS, DECISOES, _retornos, _snapshots

_ORIGINAL = protegida.montar_carteira_com_concessao


def _rodar(monkeypatch, *, recusar: set[int] = frozenset(), esvaziar: set[int] = frozenset()):
    """Roda o backtest com o orquestrador real, alterando so a chamada N."""
    chamadas = {"n": 0}

    def orquestrador(*args, **kwargs):
        indice = chamadas["n"]
        chamadas["n"] += 1
        resultado = dict(_ORIGINAL(*args, **kwargs))
        if indice in esvaziar:
            return {**resultado, "items": [], "can_publish": False,
                    "status": "blocked", "blockers": ["inviavel no teste"]}
        if indice in recusar:
            assert resultado.get("items"), "o teste precisa de carteira montada"
            return {**resultado, "can_publish": False, "status": "diligence_only",
                    "blockers": ["cobertura de dimensão obrigatória ausente"]}
        return resultado

    monkeypatch.setattr(protegida, "montar_carteira_com_concessao", orquestrador)
    return robust_optimizer_point_in_time_backtest(
        _snapshots(), _retornos(), pd.Series(.005, index=DATAS),
        {d: {"selic": 12.0, "ipca": 4.5} for d in DECISOES},
        transaction_cost=0, slippage=0,
    )


@pytest.fixture
def base(monkeypatch):
    resultado = _rodar(monkeypatch)
    assert resultado["status"] == "calculated", resultado
    assert resultado["periods"] == len(DECISOES)
    return resultado


_NUMERICOS = ("portfolio_return", "portfolio_return_bruto",
              "portfolio_return_apos_custos", "benchmark_return", "ir_periodo",
              "coverage", "turnover", "peso_ausente")


def test_periodo_recusado_entra_na_curva_com_o_mesmo_retorno(monkeypatch, base):
    recusado = _rodar(monkeypatch, recusar={1})

    assert recusado["periods"] == base["periods"], (
        "a carteira que a tela mostrou como rascunho tem de entrar na curva")
    for antes, depois in zip(base["observations"], recusado["observations"]):
        assert depois["decision_date"] == antes["decision_date"]
        assert depois["holdings"] == pytest.approx(antes["holdings"], abs=1e-12)
        for campo in _NUMERICOS:
            assert depois[campo] == pytest.approx(antes[campo], abs=1e-12), campo
    assert recusado["mean_return"] == pytest.approx(base["mean_return"], abs=1e-12)
    assert recusado["max_drawdown"] == pytest.approx(base["max_drawdown"], abs=1e-12)


def test_periodo_recusado_sai_marcado_e_na_coorte_propria(monkeypatch, base):
    recusado = _rodar(monkeypatch, recusar={1})

    marcas = [obs["publicacao_recusada"] for obs in recusado["observations"]]
    assert marcas == [False, True]
    coortes = recusado["curvas_por_publicacao"]
    assert coortes["publicavel"]["periods"] == 1
    assert coortes["publicacao_recusada"]["periods"] == 1
    assert coortes["publicacao_recusada"]["mean_return"] == pytest.approx(
        base["observations"][1]["portfolio_return"], abs=1e-12)
    assert base["curvas_por_publicacao"]["publicacao_recusada"] == {"periods": 0}


def test_fracao_viavel_continua_medindo_carteira_publicavel(monkeypatch, base):
    recusado = _rodar(monkeypatch, recusar={1})

    assert base["optimizer_feasible_fraction"] == pytest.approx(1.0)
    assert recusado["optimizer_attempted_periods"] == 2
    assert recusado["optimizer_successful_periods"] == 1
    assert recusado["optimizer_feasible_fraction"] == pytest.approx(.5)
    assert recusado["publication_refused_periods"] == [DECISOES[1]]
    # O diagnostico da recusa continua no relatorio de falhas.
    assert [f["decision_date"] for f in recusado["optimizer_failures"]] == [DECISOES[1]]
    # E o bloqueador segue nomeando a recusa como causa dominante.
    blockers = validate_methodology(recusado, {})["blockers"]
    assert any("publicação recusada" in b for b in blockers), blockers


def test_periodo_sem_carteira_continua_fora_da_curva(monkeypatch, base):
    vazio = _rodar(monkeypatch, esvaziar={1})

    assert vazio["periods"] == base["periods"] - 1
    assert vazio["publication_refused_periods"] == []
    assert vazio["optimizer_attempted_periods"] == 2
    assert vazio["optimizer_successful_periods"] == 1


def test_giro_do_periodo_seguinte_parte_da_carteira_recusada(monkeypatch, base):
    # A carteira recusada foi a que o investidor teve; o giro do periodo
    # seguinte parte dela, nao da ultima publicavel.
    recusado = _rodar(monkeypatch, recusar={0})

    assert [obs["turnover"] for obs in recusado["observations"]] == pytest.approx(
        [obs["turnover"] for obs in base["observations"]])

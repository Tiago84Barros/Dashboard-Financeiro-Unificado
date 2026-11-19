"""Periodo sem carteira publicavel e caixa a Selic no walk-forward FII.

Unificacao com a medicao fora da amostra da Empresas B3 (18/11/2026, regra
unica em `core.b3_oos_carteira.carteira_salvavel`). Quando o otimizador monta
carteira e o portao de transparencia recusa publicar (`can_publish=False`), a
tela (`views/fiis.py`) mostra o rascunho, mas nao deixa publica-lo: quem
seguia a carteira-modelo ficou em caixa. O mesmo vale para o periodo em que o
otimizador nao montou carteira nenhuma. A manchete do walk-forward que roda
em producao (`robust_optimizer_point_in_time_backtest`, chamado por
`data_pipeline/market/fii_pit.py`) conta os dois como caixa a Selic da
decisao. Os pesos do rascunho vao para uma sensibilidade a parte.

A versao anterior (PR #773) media o rascunho com os proprios pesos na
manchete e descartava o periodo vazio. Esse comportamento continua existindo,
com `medir_rascunho=True`, e a equivalencia com ele e testada aqui.
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
_SELIC = 12.0
_CAIXA_UM_MES = (1 + _SELIC / 100) ** (1 / 12) - 1


def _rodar(monkeypatch, *, recusar: set[int] = frozenset(),
           esvaziar: set[int] = frozenset(), retornos: pd.DataFrame | None = None,
           custo: float = 0.0, **kwargs):
    """Roda o backtest com o orquestrador real, alterando so a chamada N."""
    chamadas = {"n": 0}

    def orquestrador(*args, **kw):
        indice = chamadas["n"]
        chamadas["n"] += 1
        resultado = dict(_ORIGINAL(*args, **kw))
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
        _snapshots(), retornos if retornos is not None else _retornos(),
        pd.Series(.005, index=DATAS),
        {d: {"selic": _SELIC, "ipca": 4.5} for d in DECISOES},
        transaction_cost=custo, slippage=0, **kwargs,
    )


@pytest.fixture
def base(monkeypatch):
    resultado = _rodar(monkeypatch)
    assert resultado["status"] == "calculated", resultado
    assert resultado["periods"] == len(DECISOES)
    assert resultado["cash_periods"] == 0
    return resultado


_NUMERICOS = ("portfolio_return", "portfolio_return_bruto",
              "portfolio_return_apos_custos", "benchmark_return", "ir_periodo",
              "coverage", "turnover", "peso_ausente")


def test_periodo_recusado_vira_caixa_a_selic(monkeypatch, base):
    recusado = _rodar(monkeypatch, recusar={1})

    assert recusado["periods"] == base["periods"], "o periodo recusado conta"
    primeiro, segundo = recusado["observations"]
    assert primeiro["portfolio_return"] == pytest.approx(
        base["observations"][0]["portfolio_return"], abs=1e-12)
    assert segundo["caixa"] is True
    assert segundo["motivo_caixa"] == "publicacao_recusada"
    assert segundo["publicacao_recusada"] is True
    assert segundo["holdings"] == {}
    assert segundo["portfolio_return_bruto"] == pytest.approx(_CAIXA_UM_MES, abs=1e-12)
    assert segundo["portfolio_return"] == pytest.approx(_CAIXA_UM_MES, abs=1e-12)
    # O benchmark do periodo nao depende de ter carteira.
    assert segundo["benchmark_return"] == pytest.approx(
        base["observations"][1]["benchmark_return"], abs=1e-12)
    assert recusado["mean_return"] == pytest.approx(
        (primeiro["portfolio_return"] + _CAIXA_UM_MES) / 2, abs=1e-12)
    assert recusado["cash_periods"] == 1
    assert recusado["cash_periods_publication_refused"] == 1


def test_medir_rascunho_reproduz_a_carteira_da_tela(monkeypatch, base):
    # Equivalencia com o comportamento do PR #773: com `medir_rascunho`, a
    # recusa muda a MARCACAO do periodo, nunca o retorno dele.
    rascunho = _rodar(monkeypatch, recusar={1}, medir_rascunho=True)

    assert rascunho["periods"] == base["periods"]
    for antes, depois in zip(base["observations"], rascunho["observations"]):
        assert depois["decision_date"] == antes["decision_date"]
        assert depois["holdings"] == pytest.approx(antes["holdings"], abs=1e-12)
        assert depois["caixa"] is False
        for campo in _NUMERICOS:
            assert depois[campo] == pytest.approx(antes[campo], abs=1e-12), campo
    assert rascunho["mean_return"] == pytest.approx(base["mean_return"], abs=1e-12)
    assert "sensibilidade_rascunho" not in rascunho


def test_sensibilidade_traz_o_rascunho_medido(monkeypatch, base):
    recusado = _rodar(monkeypatch, recusar={1})

    sens = recusado["sensibilidade_rascunho"]
    assert sens["periodos_com_rascunho"] == [DECISOES[1]]
    assert sens["periods"] == base["periods"]
    assert sens["mean_return"] == pytest.approx(base["mean_return"], abs=1e-12)
    assert "sensibilidade_rascunho" not in base


def test_coortes_separam_publicavel_caixa_e_rascunho(monkeypatch, base):
    recusado = _rodar(monkeypatch, recusar={1})
    coortes = recusado["curvas_por_publicacao"]

    assert coortes["publicavel"]["periods"] == 1
    assert coortes["caixa"]["periods"] == 1
    assert coortes["caixa"]["mean_return"] == pytest.approx(_CAIXA_UM_MES, abs=1e-12)
    assert coortes["rascunho_medido"] == {"periods": 0}
    # O caixa nao e carteira sob o padrao da casa.
    assert recusado["curvas_por_padrao"]["padrao_da_casa"]["periods"] == 1

    sens = _rodar(monkeypatch, recusar={1}, medir_rascunho=True)
    assert sens["curvas_por_publicacao"]["rascunho_medido"]["periods"] == 1
    assert sens["curvas_por_publicacao"]["caixa"] == {"periods": 0}


def test_fracao_viavel_e_bloqueador_nao_mudam(monkeypatch, base):
    recusado = _rodar(monkeypatch, recusar={1})

    assert base["optimizer_feasible_fraction"] == pytest.approx(1.0)
    assert recusado["optimizer_attempted_periods"] == 2
    assert recusado["optimizer_successful_periods"] == 1
    assert recusado["optimizer_feasible_fraction"] == pytest.approx(.5)
    assert recusado["publication_refused_periods"] == [DECISOES[1]]
    assert [f["decision_date"] for f in recusado["optimizer_failures"]] == [DECISOES[1]]
    blockers = validate_methodology(recusado, {})["blockers"]
    assert any("publicação recusada" in b for b in blockers), blockers


def test_periodo_vazio_depois_da_primeira_carteira_vira_caixa(monkeypatch, base):
    vazio = _rodar(monkeypatch, esvaziar={1})

    assert vazio["periods"] == base["periods"]
    segundo = vazio["observations"][1]
    assert segundo["caixa"] is True
    assert segundo["motivo_caixa"] == "sem_carteira"
    assert segundo["publicacao_recusada"] is False
    assert segundo["portfolio_return"] == pytest.approx(_CAIXA_UM_MES, abs=1e-12)
    assert vazio["publication_refused_periods"] == []
    assert vazio["optimizer_attempted_periods"] == 2
    assert vazio["optimizer_successful_periods"] == 1
    # Sem recusa nao ha rascunho para medir.
    assert "sensibilidade_rascunho" not in vazio
    # Cobertura media so dos periodos investidos: o caixa nao a puxa.
    assert vazio["mean_coverage"] == pytest.approx(
        base["observations"][0]["coverage"], abs=1e-12)


def test_periodo_vazio_no_aquecimento_fica_fora(monkeypatch, base):
    # Antes da primeira carteira a estrategia ainda nao existe: caixa ali
    # seria um periodo inventado.
    vazio = _rodar(monkeypatch, esvaziar={0})

    assert vazio["periods"] == base["periods"] - 1
    assert vazio["cash_periods"] == 0
    assert [o["decision_date"] for o in vazio["observations"]] == [
        pd.Timestamp(DECISOES[1])]


def test_recusa_no_primeiro_periodo_ja_conta_como_caixa(monkeypatch, base):
    # Houve carteira montada: o aquecimento acabou, mesmo sem publicacao.
    recusado = _rodar(monkeypatch, recusar={0})

    assert recusado["periods"] == base["periods"]
    assert recusado["observations"][0]["caixa"] is True
    assert recusado["observations"][0]["turnover"] == pytest.approx(0.0)
    # A recompra parte do caixa: giro cheio, a mesma regra da 1a carteira.
    assert recusado["observations"][1]["turnover"] == pytest.approx(1.0)


def test_giro_e_custo_de_ir_para_o_caixa(monkeypatch, base):
    custo = .002
    vazio = _rodar(monkeypatch, esvaziar={1}, custo=custo)

    segundo = vazio["observations"][1]
    # Zerar a carteira inteira: 0,5 x soma |delta| = 0,5.
    assert segundo["turnover"] == pytest.approx(.5)
    assert segundo["portfolio_return_apos_custos"] == pytest.approx(
        _CAIXA_UM_MES - .5 * custo, abs=1e-12)


def test_ir_de_zerar_a_posicao_cai_no_periodo_em_caixa(monkeypatch):
    retornos = _retornos()
    retornos["price_return"] = retornos["total_return"] * .5
    vazio = _rodar(monkeypatch, esvaziar={1}, retornos=retornos)

    primeiro, segundo = vazio["observations"]
    assert vazio["ir_calculado"] is True
    # A primeira carteira ganhou preco; vender tudo realiza o ganho.
    assert segundo["ir_periodo"] > 0
    assert segundo["portfolio_return"] == pytest.approx(
        _CAIXA_UM_MES - segundo["ir_periodo"], abs=1e-12)
    assert primeiro["ir_periodo"] == pytest.approx(0.0)

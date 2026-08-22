"""Piso absoluto de qualidade: reprova, substitui no mesmo segmento, declara vazio."""
from __future__ import annotations

import pandas as pd
import pytest

from core.b3_quality_floor import (
    APROVADO,
    REPROVADO,
    SEM_EVIDENCIA,
    FloorPolicy,
    apply_with_substitution,
    evaluate,
)

SELIC = 0.15


def _empresa(ticker, **kw):
    base = {
        "Ticker": ticker, "Payout": 0.40, "DY": 0.06, "ROIC": 0.20,
        "Endividamento_Total": 0.50, "Liquidez_Corrente": 1.80,
        "Margem_Operacional": 0.20, "P_FCO": 8.0,
    }
    base.update(kw)
    return base


def test_empresa_solida_e_aprovada():
    df = pd.DataFrame([_empresa("BOA3")])
    assert evaluate(df, ["BOA3"], selic=SELIC)["BOA3"].situacao == APROVADO


def test_fco_negativo_reprova():
    """O sinal existe porque P_FCO negativo NUNCA chega ao banco (faixa 0,01–200)."""
    df = pd.DataFrame([_empresa("QUEIMA3", P_FCO=float("nan"), FCO_Negativo=1.0)])
    v = evaluate(df, ["QUEIMA3"], selic=SELIC)["QUEIMA3"]
    assert v.situacao == REPROVADO
    assert any("FCO negativo" in m for m in v.motivos)


def test_patrimonio_negativo_reprova_sozinho():
    """Passivo acima do ativo é insolvência contábil: não há segundo sinal a esperar."""
    df = pd.DataFrame([_empresa("INSOLV3", Endividamento_Total=float("nan"),
                                Patrimonio_Negativo=1.0)])
    v = evaluate(df, ["INSOLV3"], selic=SELIC)["INSOLV3"]
    assert v.situacao == REPROVADO


def test_ausencia_de_dado_nao_reprova():
    """Holding não tem margem operacional própria — ITSA4/BRAP3 não podem cair por isso."""
    df = pd.DataFrame([_empresa("HOLD4", Margem_Operacional=float("nan"),
                                P_FCO=float("nan"))])
    assert evaluate(df, ["HOLD4"], selic=SELIC)["HOLD4"].situacao == SEM_EVIDENCIA


def test_payout_alto_sem_aperto_de_caixa_nao_reprova():
    """SBSP3 real: 151% por distribuição de privatização, com dívida 1,18 e FCO forte."""
    df = pd.DataFrame([_empresa("SBSP3", Payout=1.51, ROIC=0.143,
                                Endividamento_Total=1.18, Margem_Operacional=0.33,
                                P_FCO=13.67)])
    assert evaluate(df, ["SBSP3"], selic=SELIC)["SBSP3"].situacao == APROVADO


def test_payout_alto_com_aperto_de_caixa_reprova():
    """UNIP6 real: 310% com dívida/PL 3,24 — a confirmação existe."""
    df = pd.DataFrame([_empresa("UNIP6", Payout=3.10, ROIC=0.085,
                                Endividamento_Total=3.24, Margem_Operacional=0.12,
                                P_FCO=5.37)])
    assert evaluate(df, ["UNIP6"], selic=SELIC)["UNIP6"].situacao == REPROVADO


def test_substituicao_preserva_a_vaga_do_segmento():
    """É isto que torna qualidade e diversificação compatíveis."""
    df = pd.DataFrame([
        _empresa("RUIM3", Margem_Operacional=-0.10, ROE=-0.10),
        _empresa("BOA3"),
    ])
    log: dict = {}
    finais = apply_with_substitution(
        ["RUIM3"], [("RUIM3", 90.0), ("BOA3", 80.0)], df,
        seg_label="Setor › Segmento", selic=SELIC,
        pesos={"RUIM3": 0.25}, log=log,
    )
    assert finais == ["BOA3"]
    assert log["substituicoes"] == [
        {"entra": "BOA3", "sai": "RUIM3", "segmento": "Setor › Segmento"}]


def test_substituto_herda_o_orcamento_de_peso():
    df = pd.DataFrame([_empresa("RUIM3", Margem_Operacional=-0.10, ROE=-0.10), _empresa("BOA3")])
    pesos = {"RUIM3": 0.30}
    apply_with_substitution(["RUIM3"], [("RUIM3", 9.0), ("BOA3", 8.0)], df,
                            selic=SELIC, pesos=pesos, log={})
    assert pesos["BOA3"] == pytest.approx(0.30)


def test_segmento_sem_candidato_bom_fica_vazio_e_declarado():
    """Rebaixar para o menos ruim devolveria ao usuário o problema que o piso resolve."""
    df = pd.DataFrame([
        _empresa("RUIM3", Margem_Operacional=-0.10, ROE=-0.10),
        _empresa("PIOR3", FCO_Negativo=1.0, P_FCO=float("nan")),
    ])
    log: dict = {}
    finais = apply_with_substitution(
        ["RUIM3"], [("RUIM3", 9.0), ("PIOR3", 8.0)], df,
        seg_label="Setor › Segmento", selic=SELIC, log=log)
    assert finais == []
    assert log["sem_substituto"] == [
        {"tk": "RUIM3", "segmento": "Setor › Segmento"}]


def test_piso_desligado_por_politica_nao_reprova_nada():
    df = pd.DataFrame([_empresa("RUIM3", Margem_Operacional=-0.10, ROE=-0.10)])
    politica = FloorPolicy(reprovar_criticos=False)
    v = evaluate(df, ["RUIM3"], policy=politica, selic=SELIC)["RUIM3"]
    assert v.situacao != REPROVADO


def test_mesma_regua_da_secao_de_saude():
    """Contrato central: o piso não tem limiares próprios.

    Se alguém adicionar critério aqui em vez de em check_holdings, este teste
    quebra — é o que impede o retorno da divergência entre motores.
    """
    from core.b3_holdings_health import CRITICO, check_holdings
    df = pd.DataFrame([
        _empresa("A3"), _empresa("B3", Margem_Operacional=-0.10, ROE=-0.10),
        _empresa("C3", Payout=3.10, Endividamento_Total=3.24),
        _empresa("D3", Patrimonio_Negativo=1.0, Endividamento_Total=float("nan")),
    ])
    tickers = ["A3", "B3", "C3", "D3"]
    criticos = {h.ticker for h in check_holdings(df, tickers, selic=SELIC)
                if h.nivel == CRITICO}
    reprovados = {t for t, v in evaluate(df, tickers, selic=SELIC).items()
                  if v.situacao == REPROVADO}
    assert reprovados == criticos

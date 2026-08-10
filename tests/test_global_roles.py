"""Classificador de papel estrategico por ativo."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.roles import (
    LIMIARES,
    PAPEIS,
    ROTULOS_PAPEL,
    classificar,
)


def _linha(symbol, classe="b3", peso=0.1, currency="BRL", sector="financials",
           fundamentals=None, history=None, classification=None):
    return {
        "asset_class": classe, "symbol": symbol, "name": symbol,
        "sector": sector, "currency": currency, "weight_global": peso,
        "payload": {
            "fundamentals": fundamentals or {},
            "history": history or {},
            "classification": classification or {},
        },
    }


def test_todo_papel_tem_rotulo():
    assert set(ROTULOS_PAPEL) == set(PAPEIS)


def test_papeis_sao_deterministicos():
    assert PAPEIS == tuple(sorted(PAPEIS))


def test_renda_exige_dy_alto_e_payout_estavel():
    payout_estavel = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("ALTO", fundamentals={"DY": 9.0}, history=payout_estavel),
        _linha("BAIXO", fundamentals={"DY": 1.0}, history=payout_estavel),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "renda" in saida["ALTO"].papeis
    assert "renda" not in saida["BAIXO"].papeis


def test_renda_recusa_payout_erratico():
    erratico = {"multiplos_anuais": [{"Payout": v} for v in (5.0, 90.0, 10.0, 80.0, 15.0)]}
    estavel = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("ERRATICO", fundamentals={"DY": 9.0}, history=erratico),
        _linha("ESTAVEL", fundamentals={"DY": 9.0}, history=estavel),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "renda" not in saida["ERRATICO"].papeis
    assert "renda" in saida["ESTAVEL"].papeis


def test_crescimento_usa_cagr_de_lpa():
    crescendo = {"demonstracoes_anuais": [{"LPA": v} for v in (1.0, 1.3, 1.7, 2.2, 2.9)]}
    parado = {"demonstracoes_anuais": [{"LPA": 1.0} for _ in range(5)]}
    df = pd.DataFrame([
        _linha("CRESCE", history=crescendo),
        _linha("PARADO", history=parado),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "crescimento" in saida["CRESCE"].papeis
    assert "crescimento" not in saida["PARADO"].papeis


def test_hedge_cambial_vem_da_moeda():
    df = pd.DataFrame([
        _linha("AAPL", classe="us", currency="USD"),
        _linha("PETR4", currency="BRL"),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "hedge_cambial" in saida["AAPL"].papeis
    assert "hedge_cambial" not in saida["PETR4"].papeis


def test_protecao_inflacao_por_fii_de_papel_ou_por_setor():
    df = pd.DataFrame([
        _linha("KNCR11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_papel": 94.0, "pct_imoveis": 0.0}}),
        _linha("SBSP3", sector="utilities"),
        _linha("LEVE3", sector="consumer"),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "protecao_inflacao" in saida["KNCR11"].papeis
    assert "protecao_inflacao" in saida["SBSP3"].papeis
    assert "protecao_inflacao" not in saida["LEVE3"].papeis


def test_reserva_valor_e_fii_de_tijolo():
    df = pd.DataFrame([
        _linha("HGLG11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_imoveis": 96.0, "pct_papel": 0.0}}),
        _linha("KNCR11", classe="fii", sector="real_estate",
               classification={"composition": {"pct_imoveis": 0.0, "pct_papel": 94.0}}),
    ])
    saida = {p.symbol: p for p in classificar(df)}
    assert "reserva_valor" in saida["HGLG11"].papeis
    assert "reserva_valor" not in saida["KNCR11"].papeis


def test_baixa_volatilidade_e_diversificacao_vem_da_serie():
    n = 60
    idx = pd.date_range("2020-01-31", periods=n, freq="ME")
    quieto = np.full(n, 0.001)
    agitado = np.tile([0.20, -0.18], n // 2)
    ret = pd.DataFrame({"QUIETO": quieto, "AGITADO": agitado}, index=idx)
    df = pd.DataFrame([_linha("QUIETO"), _linha("AGITADO")])

    saida = {p.symbol: p for p in classificar(
        df, retornos=ret, correlacoes={"QUIETO": 0.05, "AGITADO": 0.85})}
    assert "baixa_volatilidade" in saida["QUIETO"].papeis
    assert "baixa_volatilidade" not in saida["AGITADO"].papeis
    assert "diversificacao" in saida["QUIETO"].papeis
    assert "diversificacao" not in saida["AGITADO"].papeis


def test_sem_serie_o_papel_fica_indeterminado_e_nao_negado():
    df = pd.DataFrame([_linha("SEMSERIE")])
    p = classificar(df, retornos=None, correlacoes=None)[0]
    assert "baixa_volatilidade" in p.indeterminados
    assert "diversificacao" in p.indeterminados
    assert "baixa_volatilidade" not in p.papeis


def test_toda_evidencia_acompanha_o_papel_que_a_gerou():
    payout = {"multiplos_anuais": [{"Payout": 40.0} for _ in range(5)]}
    df = pd.DataFrame([_linha("X", fundamentals={"DY": 9.0}, history=payout)])
    p = classificar(df)[0]
    papeis_com_evidencia = {e.papel for e in p.evidencias}
    assert set(p.papeis) <= papeis_com_evidencia, "papel sem numero e rotulo, nao classificacao"
    for e in p.evidencias:
        assert e.texto, "evidencia precisa de texto legivel"


def test_ativo_sem_papel_algum_e_declarado_explicitamente():
    df = pd.DataFrame([_linha("NADA", fundamentals={"DY": 0.5})])
    p = classificar(df)[0]
    assert p.papeis == ()
    assert "nenhum papel" in p.justificativa.lower()


def test_ordem_da_saida_segue_o_quadro():
    df = pd.DataFrame([_linha("B"), _linha("A"), _linha("C")])
    assert [p.symbol for p in classificar(df)] == ["B", "A", "C"]


def test_quadro_vazio_devolve_lista_vazia():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "name", "sector",
                                  "currency", "weight_global", "payload"])
    assert classificar(vazio) == []


def test_limiares_estao_num_so_lugar():
    for chave in ("payout_instavel", "cagr_minimo", "vol_baixa",
                  "correlacao_baixa", "papel_dominante", "tijolo_dominante"):
        assert chave in LIMIARES

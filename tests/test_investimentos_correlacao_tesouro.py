"""Lacuna a4cb28ec: o Tesouro saía da matriz de correlação por classe.

A curva oficial (`tesouro_market_rates`) guarda o PU diário de cada título com
a mesma chave do ticker da carteira (TIPCA2029, TPRE2032...). A tela excluía
todo título do Tesouro antes de olhar se a série existia. Estes testes montam
o cenário: PU disponível na curva, e a matriz precisa entregá-lo.
"""
import sys
import types

import numpy as np
import pandas as pd

import views.investimentos as view
from core.correlation_analysis import MIN_CORR_MONTHS


def _dias(meses: int) -> pd.DatetimeIndex:
    fim = pd.Timestamp("2026-09-30")
    inicio = (fim - pd.DateOffset(months=meses)).normalize()
    return pd.bdate_range(inicio, fim)


def _serie(dias: pd.DatetimeIndex, semente: int) -> np.ndarray:
    rng = np.random.default_rng(semente)
    return 100.0 * np.cumprod(1.0 + rng.normal(0.0, 0.01, len(dias)))


def _yfinance_falso(monkeypatch, precos: pd.DataFrame) -> None:
    def download(symbols, **_kwargs):
        simbolos = [symbols] if isinstance(symbols, str) else list(symbols)
        colunas = [s for s in simbolos if s in precos.columns]
        if not colunas:
            return pd.DataFrame()
        bruto = precos[colunas].copy()
        bruto.columns = pd.MultiIndex.from_product([["Close"], colunas])
        return bruto

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=download))


def _posicoes():
    return [
        {"ticker": "PETR4", "classe": "Ações", "pais": "BR", "moeda": "BRL",
         "valor_mercado": 9_000.0},
        {"ticker": "ITUB4", "classe": "Ações", "pais": "BR", "moeda": "BRL",
         "valor_mercado": 8_000.0},
        {"ticker": "TIPCA2029", "classe": "Tesouro IPCA+", "pais": "BR",
         "moeda": "BRL", "valor_mercado": 7_000.0},
        {"ticker": "TPRE2032", "classe": "Tesouro Prefixado", "pais": "BR",
         "moeda": "BRL", "valor_mercado": 6_000.0},
        {"ticker": "CDB8266EXTI", "classe": "Renda Fixa", "pais": "BR",
         "moeda": "BRL", "valor_mercado": 5_000.0},
    ]


def test_tesouro_com_pu_na_curva_entra_na_matriz(monkeypatch):
    dias = _dias(40)
    _yfinance_falso(monkeypatch, pd.DataFrame(
        {"PETR4.SA": _serie(dias, 1), "ITUB4.SA": _serie(dias, 2)}, index=dias))

    pedidos = {}
    curto = _dias(20)

    def pu_falso(tesouro):
        pedidos["tesouro"] = tesouro
        longo = pd.DataFrame({"TIPCA2029": _serie(dias, 3)}, index=dias)
        recente = pd.DataFrame({"TPRE2032": _serie(curto, 4)}, index=curto)
        return longo.join(recente, how="outer")

    monkeypatch.setattr(view, "_pu_tesouro", pu_falso)
    view._load_corr_precos.clear()

    dados = view._build_corr_data(_posicoes())

    assert pedidos["tesouro"] == ("TIPCA2029", "TPRE2032")
    corr = dados["corr"]
    assert "TIPCA2029" in corr.columns
    assert pd.notna(corr.loc["TIPCA2029", "PETR4"])
    assert dados["weights"]["TIPCA2029"] == 7_000.0
    # Título com PU curto continua fora, e a tela diz por quê.
    assert "TPRE2032" not in corr.columns
    assert (f"TPRE2032 (PU do Tesouro sem {MIN_CORR_MONTHS} meses de histórico)"
            in dados["skipped"])
    assert "TIPCA2029" not in " ".join(dados["skipped"])
    # Renda fixa privada não tem série: segue declarada, sem preço inventado.
    assert "CDB8266EXTI" in dados["skipped"]
    assert "CDB8266EXTI" not in corr.columns


def test_tesouro_sem_pu_na_curva_fica_declarado(monkeypatch):
    dias = _dias(40)
    _yfinance_falso(monkeypatch, pd.DataFrame(
        {"PETR4.SA": _serie(dias, 5), "ITUB4.SA": _serie(dias, 6)}, index=dias))
    monkeypatch.setattr(view, "_pu_tesouro", lambda tesouro: pd.DataFrame())
    view._load_corr_precos.clear()

    dados = view._build_corr_data(_posicoes())

    assert set(dados["corr"].columns) == {"PETR4", "ITUB4"}
    assert any(s.startswith("TIPCA2029 (") for s in dados["skipped"])
    assert any(s.startswith("TPRE2032 (") for s in dados["skipped"])

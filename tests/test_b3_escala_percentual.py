"""Escala dos campos % da tela Empresas B3 e o DY de reserva do yfinance.

O banco guarda os campos % em decimal (``core.data_quality.PCT_FIELDS``). A
tela adivinhava a escala por um teto por campo e exibia o ROA de 1,149 (114,9%)
como 1,1%. O fallback do yfinance lia ``dividendYield``, que vem em %, como
decimal. Nada aqui toca banco nem rede: ``yf.Ticker`` é substituído.
"""
from __future__ import annotations

import pandas as pd
import pytest

import views.empresas_b3 as b3


def _cards(**valores) -> dict[str, tuple]:
    inds = b3._build_indicators(pd.Series(valores), grupo="todos")
    return {lbl: (val, sub) for lbl, val, sub, _cor in inds}


def test_campo_percentual_sempre_vezes_cem():
    cards = _cards(ROA=1.149, ROE=0.15, Margem_Liquida=0.07)
    assert cards["ROA"][0] == "114.90%"
    assert cards["ROE"][0] == "15.00%"
    assert cards["Margem Líquida"][0] == "7.00%"


def test_valor_fora_da_faixa_aparece_e_e_rotulado():
    cards = _cards(Margem_Liquida=-1.5, ROE=6.0)
    assert cards["Margem Líquida"][0] == "-150.00%"
    assert "Fora da faixa plausível" in cards["Margem Líquida"][1]
    assert cards["ROE"][0] == "600.00%"
    assert "Fora da faixa plausível" in cards["ROE"][1]


def test_valor_plausivel_mantem_o_subtitulo():
    cards = _cards(ROA=1.149, DY=0.0)
    assert cards["ROA"][1] == "Retorno s/ Ativos"
    # DY zero é quem não paga; não é número implausível.
    assert cards["Dividend Yield"] == ("0.00%", "Dividendos/Preço")


def test_metric_decimal_pct_sem_teto():
    assert b3._metric_decimal_pct(pd.Series({"ROIC": 3.5}), "ROIC") == "350.0%"
    assert b3._metric_decimal_pct(pd.Series({"ROIC": 0.12}), "ROIC") == "12.0%"
    assert b3._metric_decimal_pct(pd.Series({"ROIC": None}), "ROIC") == "—"


class _TickerFalso:
    def __init__(self, info, dividends=None):
        self.info = info
        self.dividends = dividends if dividends is not None else pd.Series(dtype=float)


@pytest.fixture
def yf_falso(monkeypatch):
    def _instalar(info, dividends=None):
        monkeypatch.setattr(b3.yf, "Ticker", lambda _tk: _TickerFalso(info, dividends))
        b3._yf_multiplos_dividendos.clear()  # st.cache_data: cada caso do zero
    yield _instalar
    b3._yf_multiplos_dividendos.clear()


def test_dividend_yield_em_percentual_nao_e_lido_como_decimal(yf_falso):
    yf_falso({"dividendYield": 3.36, "trailingAnnualDividendYield": 0.0434,
              "payoutRatio": 0.6})
    out = b3._yf_multiplos_dividendos("WEGE3")
    assert out["DY"] == pytest.approx(0.0434)
    assert out["Payout"] == pytest.approx(0.6)


def test_sem_trailing_cai_para_taxa_sobre_preco(yf_falso):
    yf_falso({"dividendYield": 3.36, "trailingAnnualDividendRate": 2.0,
              "currentPrice": 50.0})
    assert b3._yf_multiplos_dividendos("WEGE3")["DY"] == pytest.approx(0.04)


def test_proventos_antigos_nao_viram_dy_atual(yf_falso):
    hoje = pd.Timestamp.now(tz="America/Sao_Paulo")
    antigos = pd.Series([1.0, 1.0], index=pd.DatetimeIndex(
        [hoje - pd.Timedelta(days=900), hoje - pd.Timedelta(days=800)]))
    yf_falso({"currentPrice": 10.0}, antigos)
    assert "DY" not in b3._yf_multiplos_dividendos("XPTO3")

    recentes = pd.Series([0.5, 0.5], index=pd.DatetimeIndex(
        [hoje - pd.Timedelta(days=200), hoje - pd.Timedelta(days=20)]))
    yf_falso({"currentPrice": 10.0}, recentes)
    assert b3._yf_multiplos_dividendos("XPTO3")["DY"] == pytest.approx(0.10)

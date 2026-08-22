"""Proveniência PIT na Criação de Portfólio B3 (achado A-002)."""
from __future__ import annotations

import numpy as np
import pandas as pd

import views.portfolio_b3 as portfolio

_TICKERS = ["AAAA3", "BBBB3", "CCCC3"]
_GRUPO = ("Materiais Básicos", "Teste", "Segmento de teste")


def _hist(available_at: object) -> dict[str, pd.DataFrame]:
    anos = list(range(2018, pd.Timestamp.now().year))
    return {
        ticker: pd.DataFrame({
            "Ticker": ticker,
            "Data": [pd.Timestamp(ano, 12, 31) for ano in anos],
            "ROE": [0.12 + idx * 0.01] * len(anos),
            "ROIC": [0.10] * len(anos),
            "Margem_Liquida": [0.08] * len(anos),
            "P/L": [8.0] * len(anos),
            "P/VP": [1.2] * len(anos),
            "DY": [0.04] * len(anos),
            "AvailableAt": available_at,
        })
        for idx, ticker in enumerate(_TICKERS)
    }


def _precos() -> pd.DataFrame:
    indice = pd.date_range("2018-04-30", periods=100, freq="ME")
    valores = {
        ticker: 100.0 * np.cumprod(np.full(len(indice), 1.004 + idx * 0.0001))
        for idx, ticker in enumerate(_TICKERS)
    }
    return pd.DataFrame(valores, index=indice)


def _resultado(hist_batch: dict[str, pd.DataFrame]) -> dict | None:
    setor, subsetor, segmento = _GRUPO
    resultado = portfolio._processar_segmento(
        _TICKERS, hist_batch, _precos(), setor, subsetor, segmento,
        taxa_selic_aa=0.0, selic_macro={}, macro_history={}, aporte=1000.0,
        ano_inicio=2019, gamma=1.0, cap=1.0, soft=0.0,
    )
    return resultado


def test_segmento_baseline_agrega_pit_modelado_e_nao_declara_validacao_pit():
    resultado = _resultado(_hist([pd.NaT] * (pd.Timestamp.now().year - 2018)))
    assert resultado is not None

    cobertura = resultado["pit_coverage"]
    assert cobertura.cobertura_medida == 0.0
    assert cobertura.nivel == "modelada"
    assert resultado["pit_coverage_validacao"].nivel == "modelada"
    assert resultado["pit_coverage_por_ano"]
    assert all(cov.nivel == "modelada"
               for cov in resultado["pit_coverage_por_ano"].values())
    assert "SIMULAÇÃO" in portfolio._rotulo_validacao_pit(resultado)
    assert "Validação point-in-time validado" not in portfolio._rotulo_validacao_pit(resultado)


def test_segmento_vintage_posterior_agrega_linha_barrada_sem_reabilitar_como_baseline():
    anos = list(range(2018, pd.Timestamp.now().year))
    disponivel = [pd.NaT] * len(anos)
    disponivel[-1] = pd.Timestamp(pd.Timestamp.now().year, 12, 1, tz="UTC")
    resultado = _resultado(_hist(disponivel))
    assert resultado is not None

    cobertura = resultado["pit_coverage"]
    assert cobertura.linhas_barradas_vintage >= len(_TICKERS)
    assert cobertura.snapshots_modelados > 0
    assert cobertura.cobertura_medida == 0.0


def test_timestamp_malformado_nao_vira_baseline_modelada_sem_proveniencia():
    anos = list(range(2018, pd.Timestamp.now().year))
    resultado = _resultado(_hist(["timestamp-invalido"] * len(anos)))

    # Não há score suficiente porque nenhuma data de disponibilidade pode ser
    # comprovada. O contrato fail-closed impede que strings ruins virem NaT de
    # baseline e alimentem uma simulação modelada.
    assert resultado is None

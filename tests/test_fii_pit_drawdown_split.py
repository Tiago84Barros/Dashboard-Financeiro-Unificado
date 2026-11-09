"""Drawdown e tendencia PIT nao confundem desdobramento com queda.

As features PIT de mercado (``_features_as_of``) mediam drawdown e tendencia
sobre o fechamento bruto. Num desdobramento 1:10 o preco cai 90% de um mes
para o outro sem o cotista perder nada, e o fundo era cortado pela
elegibilidade (``max_drawdown`` acima de 35%). O ajuste de split ja existia,
mas so em ``_monthly_returns``.
"""
import pandas as pd
import pytest

from data_pipeline.market.fii_pit import _features_as_of, _monthly_market_features

MESES = pd.date_range("2023-01-31", periods=40, freq="ME")
EVENTO = 24  # mes do desdobramento


def _bundle(fechamento, ajustado=None):
    dados = {
        "ticker": ["TEST11"] * len(MESES), "date": MESES, "close": fechamento,
        "volume": [21_000.0] * len(MESES), "source": ["brapi_legacy_quote"] * len(MESES),
    }
    if ajustado is not None:
        dados["adjusted_close"] = ajustado
    return _monthly_market_features(pd.DataFrame(dados), pd.DataFrame())["TEST11"]


def _desdobramento():
    # Preco estavel em 100, desdobra 1:10 e segue estavel em 10.
    fechamento = [100.0] * EVENTO + [10.0] * (len(MESES) - EVENTO)
    ajustado = [10.0] * len(MESES)
    return fechamento, ajustado


def test_desdobramento_nao_vira_drawdown_de_90_por_cento():
    resultado = _features_as_of(_bundle(*_desdobramento()), MESES[-1])

    assert resultado["max_drawdown"] == pytest.approx(0.0, abs=1e-9)
    assert resultado["total_return_trend"] == pytest.approx(0.0, abs=1e-9)


def test_queda_real_continua_sendo_queda():
    # Fechamento e ajustado caem juntos: nao e split.
    fechamento = [100.0] * EVENTO + [40.0] * (len(MESES) - EVENTO)
    resultado = _features_as_of(_bundle(fechamento, list(fechamento)), MESES[-1])

    assert resultado["max_drawdown"] == pytest.approx(-0.60)


def test_ajustado_reescrito_com_fechamento_plausivel_fica_no_fechamento():
    # O inverso do split (XPML11/2026): o ajustado salta, o fechamento nao.
    # A fonte reescreveu o fator; o fechamento observavel e que vale.
    fechamento = [100.0] * EVENTO + [95.0] * (len(MESES) - EVENTO)
    ajustado = [100.0] * EVENTO + [700.0] * (len(MESES) - EVENTO)
    resultado = _features_as_of(_bundle(fechamento, ajustado), MESES[-1])

    assert resultado["max_drawdown"] == pytest.approx(-0.05)


def test_corte_antes_do_evento_nao_muda():
    fechamento, ajustado = _desdobramento()
    antes = _features_as_of(_bundle(fechamento, ajustado), MESES[EVENTO - 1])

    assert antes["max_drawdown"] == pytest.approx(0.0)


def test_sem_coluna_ajustada_o_comportamento_e_o_antigo():
    fechamento, _ = _desdobramento()
    resultado = _features_as_of(_bundle(fechamento), MESES[-1])

    assert resultado["max_drawdown"] == pytest.approx(-0.90)


def test_preco_atual_continua_sendo_o_fechamento_bruto():
    # P/VP e preco de tela usam o fechamento observavel, nao a serie encadeada.
    resultado = _features_as_of(_bundle(*_desdobramento()), MESES[-1])

    assert resultado["price"] == pytest.approx(10.0)

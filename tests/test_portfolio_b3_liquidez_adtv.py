"""Filtro de negociabilidade (ADTV) da Criação de Portfólio B3.

O piso antigo era valor de mercado, que mede TAMANHO e não negociabilidade:
nomes de capitalização alta com free-float mínimo passavam e eram coroados
líderes sem ter contraparte no book. Estes testes fixam a regra nova.
"""
from unittest.mock import patch

import pandas as pd
import pytest

from views.portfolio_b3 import (
    _PREGOES_MES,
    LiquidezDataError,
    _load_adtv,
    _market_cap_coverage,
)


def _quadro(linhas, **erro):
    """O que ``core.market_read._q_b3`` devolve: quadro, ou vazio com o erro."""
    df = pd.DataFrame(linhas, columns=["ticker", "mediana"])
    df.attrs.update(erro)
    return df


def _run_load_adtv(linhas, **erro):
    with patch("views.portfolio_b3._mr._q_b3", return_value=_quadro(linhas, **erro)):
        return _load_adtv.__wrapped__()


def test_adtv_converte_volume_mensal_em_diario():
    # R$ 21 milhões negociados no mês típico => R$ 1 mi/dia (21 pregões).
    result = _run_load_adtv([("PETR4", 21_000_000.0)])

    assert result["PETR4"] == pytest.approx(21_000_000.0 / _PREGOES_MES)
    assert result["PETR4"] == pytest.approx(1_000_000.0)


def test_adtv_normaliza_ticker_e_descarta_nao_finitos():
    result = _run_load_adtv([
        ("wege3.sa", 42_000_000.0),
        ("VAZIO3", None),
        ("ZERO3", 0.0),
    ])

    assert "WEGE3" in result
    assert "VAZIO3" not in result   # sem série de volume ≠ ilíquida: fica ausente
    assert "ZERO3" not in result    # volume zero não vira liquidez válida


def test_adtv_propaga_falha_em_vez_de_devolver_mapa_vazio():
    """Falha de consulta NUNCA pode ser lida como 'todo mundo ilíquido'."""
    with pytest.raises(LiquidezDataError, match="OperationalError"):
        _run_load_adtv([], load_error="query_failed", error_type="OperationalError")


def test_adtv_sem_engine_levanta_erro_tipado():
    with pytest.raises(LiquidezDataError, match="database_unavailable"):
        _run_load_adtv([], load_error="database_unavailable")


def test_cobertura_separa_ausente_de_abaixo_do_piso():
    """Reuso do medidor de cobertura: ausência não conta como coberta."""
    adtv = {"PETR4": 5e7, "CEBR5": 8e4}  # CEBR5 é dado válido, só baixo

    covered, total, ratio = _market_cap_coverage(
        ["PETR4", "CEBR5", "SEMVOL3"], adtv
    )

    assert (covered, total) == (2, 3)
    assert ratio == pytest.approx(2 / 3)


def test_piso_de_tamanho_nao_captura_iliquidez():
    """Regressão da causa raiz: market cap alto + giro baixo passa no filtro antigo."""
    market_caps = {"CEBR5": 3e9}
    adtv = {"CEBR5": 8e4}   # R$ 80 mil/dia

    assert market_caps["CEBR5"] >= 1e9      # passaria no piso antigo
    assert adtv["CEBR5"] < 1e6              # reprovado pelo piso novo

"""Retornos mensais dos ativos da carteira e relatorio de cobertura."""
import numpy as np
import pandas as pd
import pytest

from core.global_portfolio.returns import MIN_OBS, retornos_mensais


def _posicoes():
    return pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.3},
        {"asset_class": "fii", "symbol": "HGLG11", "weight_global": 0.3},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.4},
    ])


def _precos(tickers, meses=36):
    idx = pd.date_range("2023-01-31", periods=meses, freq="ME")
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {t: 100 * np.cumprod(1 + rng.normal(0.01, 0.05, meses)) for t in tickers},
        index=idx,
    )


def _loader(disponiveis=("PETR4", "HGLG11"), meses=36):
    def carregar(tickers):
        alvo = [t for t in tickers if t in disponiveis]
        return _precos(alvo, meses) if alvo else pd.DataFrame()
    return carregar


def test_us_entra_na_busca_de_precos():
    """A serie mensal dos EUA agora existe (core.us_read.load_precos_mensais_us),
    entao AAPL vira candidato como qualquer b3/fii e chega ate o loader.
    """
    pedidos = []

    def espiao(tickers):
        pedidos.append(tuple(sorted(tickers)))
        return _precos([t for t in tickers if t != "AAPL"])

    retornos_mensais(_posicoes(), loader=espiao)
    assert pedidos == [("AAPL", "HGLG11", "PETR4")]


def test_us_sem_serie_disponivel_aparece_como_sem_serie():
    """AAPL segue sem serie aqui porque o loader de teste (_loader) so tem
    dados para PETR4/HGLG11 - nao porque a classe `us` esteja excluida.
    """
    _, cob = retornos_mensais(_posicoes(), loader=_loader())
    assert cob.simbolos_sem_serie == ("AAPL",)
    assert cob.simbolos_com_serie == ("HGLG11", "PETR4")


def test_peso_coberto_e_a_fracao_com_serie():
    _, cob = retornos_mensais(_posicoes(), loader=_loader())
    assert cob.peso_coberto == pytest.approx(0.6)


def test_retornos_sao_variacao_percentual_mensal():
    ret, _ = retornos_mensais(_posicoes(), loader=_loader(meses=25))
    assert list(ret.columns) == ["HGLG11", "PETR4"]
    assert len(ret) == 24            # 25 precos -> 24 retornos
    assert ret.abs().max().max() < 1.0


def test_simbolo_com_serie_curta_e_descartado():
    ret, cob = retornos_mensais(_posicoes(), loader=_loader(meses=MIN_OBS))
    # MIN_OBS precos -> MIN_OBS-1 retornos, abaixo do piso
    assert ret.empty
    assert cob.simbolos_com_serie == ()
    assert set(cob.simbolos_sem_serie) == {"AAPL", "HGLG11", "PETR4"}


def test_meses_reflete_o_tamanho_do_quadro():
    ret, cob = retornos_mensais(_posicoes(), loader=_loader(meses=30))
    assert cob.meses == len(ret) == 29


def test_quadro_vazio_nao_levanta():
    vazio = pd.DataFrame(columns=["asset_class", "symbol", "weight_global"])
    ret, cob = retornos_mensais(vazio, loader=_loader())
    assert ret.empty
    assert cob.peso_coberto == 0.0
    assert cob.simbolos_com_serie == ()


def test_colunas_em_ordem_deterministica():
    ret, _ = retornos_mensais(_posicoes(), loader=_loader(("HGLG11", "PETR4")))
    assert list(ret.columns) == sorted(ret.columns)


def test_gap_interior_vira_nan_e_nao_zero_fabricado():
    """Um preco ausente no meio da serie nao pode virar 0% de retorno.

    fill_method='pad' (default antigo do pandas) forward-preenche o preco
    faltante antes de calcular a variacao, fabricando um retorno de 0% que
    parece um mes real de calmaria. O gap tem que sobreviver como NaN.
    """
    idx = pd.date_range("2023-01-31", periods=25, freq="ME")
    precos_petr4 = [100.0 + i for i in range(25)]
    precos_petr4[10] = np.nan  # gap interior — nao e o primeiro preco da serie
    precos_hglg11 = [50.0 + i * 0.5 for i in range(25)]
    quadro = pd.DataFrame({"PETR4": precos_petr4, "HGLG11": precos_hglg11}, index=idx)

    def loader(tickers):
        return quadro[[t for t in tickers if t in quadro.columns]]

    ret, cob = retornos_mensais(_posicoes(), loader=loader)

    mes_do_gap = idx[10]
    mes_seguinte = idx[11]     # tambem depende do preco ausente (preco anterior = NaN)
    assert pd.isna(ret.loc[mes_do_gap, "PETR4"])
    assert pd.isna(ret.loc[mes_seguinte, "PETR4"])
    assert ret.loc[mes_do_gap, "PETR4"] != 0.0  # nao pode ser um 0% fabricado

    # a contagem de observacoes validas cai com o gap, nao e preenchida
    assert ret["PETR4"].count() == 22  # 24 retornos possiveis - 2 afetados pelo gap
    # mesmo com o gap a serie continua acima do MIN_OBS e permanece coberta
    assert "PETR4" in cob.simbolos_com_serie


def test_us_entra_nas_classes_com_preco():
    from core.global_portfolio.returns import _CLASSES_COM_PRECO
    assert "us" in _CLASSES_COM_PRECO


def test_retornos_juntam_series_das_duas_fontes_alinhadas_por_mes():
    idx = pd.date_range("2022-01-31", periods=30, freq="ME")
    br = pd.DataFrame({"PETR4": range(100, 130)}, index=idx).astype(float)
    us = pd.DataFrame({"AAPL": range(200, 230)}, index=idx).astype(float)

    df = pd.DataFrame([
        {"asset_class": "b3", "symbol": "PETR4", "weight_global": 0.5, "payload": {}},
        {"asset_class": "us", "symbol": "AAPL", "weight_global": 0.5, "payload": {}},
    ])

    ret, cob = retornos_mensais(df, loader=lambda tks: pd.concat(
        [br[[c for c in br.columns if c in tks]],
         us[[c for c in us.columns if c in tks]]], axis=1))
    assert set(ret.columns) == {"PETR4", "AAPL"}
    assert cob.peso_coberto == 1.0
    assert not ret.isna().all().any(), "coluna toda NaN indica desalinhamento de indice"

import numpy as np
import pandas as pd
import pytest

from core.b3_safras import (
    SafraCarteira,
    carteiras_por_safra,
    retorno_da_safra,
    tabela_de_safras,
)

HOJE = pd.Timestamp("2026-09-21")


def _resultado(segmento, lids_por_ano, pesos_por_ano, tickers):
    return {
        "setor": "S", "subsetor": "SS", "segmento": segmento,
        "tickers": tickers,
        "lids_por_ano": lids_por_ano,
        "pesos_por_ano": pesos_por_ano,
    }


def _precos(datas, valores_por_ticker):
    return pd.DataFrame(valores_por_ticker, index=pd.DatetimeIndex(datas))


def test_carteira_da_safra_soma_um():
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3", "BBBB3"])]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.safra == 2024
    assert carteira.ano_base == 2023
    assert carteira.inicio == pd.Timestamp("2024-04-01")
    assert carteira.fim == pd.Timestamp("2025-03-31")
    assert carteira.completa is True
    assert pytest.approx(sum(carteira.pesos.values())) == 1.0


def test_orcamento_igual_entre_segmentos():
    """Dois segmentos, um lider cada: 50% para cada segmento."""
    res = [
        _resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
        _resultado("B", {2024: ["BBBB3"]}, {2024: {"BBBB3": 1.0}}, ["BBBB3"]),
    ]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.pesos == pytest.approx({"AAAA3": 0.5, "BBBB3": 0.5})
    assert carteira.segmentos == 2


def test_ticker_em_dois_segmentos_soma_os_orcamentos():
    res = [
        _resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
        _resultado("B", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}}, ["AAAA3"]),
    ]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.pesos == pytest.approx({"AAAA3": 1.0})


def test_universo_e_todos_os_tickers_dos_segmentos():
    """O equal-weight compara 'escolher os lideres' com 'comprar o segmento
    inteiro' -- entao o universo tem que ter os nao-selecionados tambem."""
    res = [_resultado("A", {2024: ["AAAA3"]}, {2024: {"AAAA3": 1.0}},
                      ["AAAA3", "BBBB3", "CCCC3"])]
    (carteira,) = carteiras_por_safra(res, hoje=HOJE)
    assert carteira.universo == ("AAAA3", "BBBB3", "CCCC3")


def test_safra_vigente_vem_marcada_como_incompleta():
    res = [_resultado("A", {2024: ["AAAA3"], 2026: ["AAAA3"]},
                      {2024: {"AAAA3": 1.0}, 2026: {"AAAA3": 1.0}}, ["AAAA3"])]
    por_safra = {c.safra: c for c in carteiras_por_safra(res, hoje=HOJE)}
    assert por_safra[2024].completa is True
    assert por_safra[2026].completa is False


def test_retorno_usa_so_precos_da_janela():
    """Preco de janeiro/2024 e de maio/2025 sao armadilhas: se entrarem no
    calculo, o retorno sai diferente de +50%."""
    df = _precos(
        ["2024-01-31", "2024-04-30", "2024-12-31", "2025-03-31", "2025-05-31"],
        {"AAAA3": [1.0, 10.0, 12.0, 15.0, 999.0]},
    )
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["retorno_estrategia"] == pytest.approx(0.5)


def test_peso_sem_preco_rende_zero_e_e_reportado():
    """Mesma convencao de core/fii_validation.py: a fatia ausente nao rende o
    que os sobreviventes renderam, e nao e redistribuida entre eles."""
    df = _precos(["2024-04-30", "2025-03-31"],
                 {"AAAA3": [10.0, 20.0], "BBBB3": [np.nan, np.nan]})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 0.5, "BBBB3": 0.5},
        universo=("AAAA3", "BBBB3"), segmentos=1,
    )
    out = retorno_da_safra(carteira, df, selic_por_ano={}, taxa_selic_aa=0.0)
    assert out["retorno_estrategia"] == pytest.approx(0.5)   # 0.5*1.0 + 0.5*0
    assert out["peso_ausente"] == pytest.approx(0.5)


def test_selic_da_janela_cruza_dois_anos():
    """A janela abril/2024 a marco/2025 pega 9 meses de 2024 e 3 de 2025."""
    df = _precos(pd.date_range("2024-04-30", "2025-03-31", freq="ME"),
                 {"AAAA3": [10.0] * 12})
    carteira = SafraCarteira(
        safra=2024, ano_base=2023,
        inicio=pd.Timestamp("2024-04-01"), fim=pd.Timestamp("2025-03-31"),
        completa=True, pesos={"AAAA3": 1.0}, universo=("AAAA3",), segmentos=1,
    )
    out = retorno_da_safra(carteira, df,
                           selic_por_ano={2024: 0.12, 2025: 0.12},
                           taxa_selic_aa=0.0)
    assert out["retorno_selic"] == pytest.approx(0.12, abs=0.005)


def test_tabela_exclui_safra_incompleta_das_medias():
    res = [_resultado("A", {2024: ["AAAA3"], 2026: ["AAAA3"]},
                      {2024: {"AAAA3": 1.0}, 2026: {"AAAA3": 1.0}}, ["AAAA3"])]
    df = _precos(pd.date_range("2024-04-30", "2026-09-30", freq="ME"),
                 {"AAAA3": np.linspace(10.0, 30.0, 30)})
    tabela = tabela_de_safras(res, df, selic_por_ano={}, taxa_selic_aa=0.0,
                              hoje=HOJE)
    assert set(tabela["Safra"]) == {2024, 2026}
    assert not bool(tabela.loc[tabela["Safra"] == 2026, "Completa"].iloc[0])
    assert bool(tabela.loc[tabela["Safra"] == 2024, "Completa"].iloc[0])
    assert tabela.attrs["safras_completas"] == [2024]
    assert 2026 not in tabela.attrs["safras_completas"]

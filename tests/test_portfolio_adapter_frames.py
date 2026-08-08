"""Helpers de DataFrame compartilhados pelos adaptadores."""
import pandas as pd

from core.portfolio.adapters._frames import indexar, registros

DF = pd.DataFrame({"Ticker": ["petr4", " vale3"], "P/L": [4.1, None]})


def test_registros_converte_para_lista_de_dicts():
    assert registros(DF)[0]["Ticker"] == "petr4"


def test_registros_converte_nan_para_none():
    assert registros(DF)[1]["P/L"] is None


def test_registros_tolera_none_e_dataframe_vazio():
    assert registros(None) == []
    assert registros(pd.DataFrame()) == []


def test_indexar_normaliza_a_chave_para_maiusculo_sem_espaco():
    assert set(indexar(DF, "Ticker")) == {"PETR4", "VALE3"}


def test_indexar_devolve_vazio_quando_a_coluna_nao_existe():
    assert indexar(DF, "Symbol") == {}


def test_indexar_tolera_none_e_dataframe_vazio():
    assert indexar(None, "Ticker") == {}
    assert indexar(pd.DataFrame(), "Ticker") == {}

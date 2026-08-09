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


def test_registros_preserva_tipo_bool():
    df = pd.DataFrame({"Flag": [True, False]})
    out = registros(df)
    assert out[0]["Flag"] is True
    assert isinstance(out[0]["Flag"], bool)
    assert out[1]["Flag"] is False
    assert isinstance(out[1]["Flag"], bool)


def test_registros_preserva_tipo_int():
    df = pd.DataFrame({"Count": [42, 0]})
    out = registros(df)
    assert out[0]["Count"] == 42
    assert isinstance(out[0]["Count"], (int, pd.Int64Dtype, pd.Int32Dtype))
    assert out[1]["Count"] == 0
    assert isinstance(out[1]["Count"], (int, pd.Int64Dtype, pd.Int32Dtype))


def test_registros_converte_nat_para_none():
    df = pd.DataFrame({"Data": pd.to_datetime(["2026-01-01", pd.NaT])})
    out = registros(df)
    assert out[0]["Data"] is not None
    assert out[1]["Data"] is None


def test_registros_preserva_none_nativo_em_object():
    df = pd.DataFrame({"Valor": ["texto", None]})
    out = registros(df)
    assert out[0]["Valor"] == "texto"
    assert out[1]["Valor"] is None

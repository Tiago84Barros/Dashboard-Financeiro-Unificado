"""Cortes de CPU depois do throttle do Streamlit Cloud (30/09/2026).

Cada corte troca trabalho repetido por reaproveitamento. O que se testa aqui
é que o reaproveitamento não muda o resultado nem esconde dado novo.
"""
import gzip
import json
import os

import numpy as np
import pandas as pd
import pytest

from core.global_portfolio import factors
from core.inteligencia_ativos import arquivo_publicado
from design import market_companies as mc

# -- logos ------------------------------------------------------------------

@pytest.fixture
def cache_de_logo_limpo(monkeypatch):
    monkeypatch.setattr(mc, "_CACHE_LOGOS", {})


def test_logo_presente_fica_lembrado_por_dias(monkeypatch, cache_de_logo_limpo):
    chamadas = []
    monkeypatch.setattr(mc, "_logo_disponivel",
                        lambda url: chamadas.append(url) or True)
    relogio = [1000.0]
    monkeypatch.setattr(mc.time, "monotonic", lambda: relogio[0])

    assert mc._logo_disponivel_cached("https://x/a.png")
    relogio[0] += 6 * 24 * 3600
    assert mc._logo_disponivel_cached("https://x/a.png")
    assert len(chamadas) == 1
    relogio[0] += 2 * 24 * 3600
    assert mc._logo_disponivel_cached("https://x/a.png")
    assert len(chamadas) == 2


def test_logo_ausente_volta_a_ser_checado_em_uma_hora(monkeypatch,
                                                       cache_de_logo_limpo):
    respostas = iter([False, True])
    monkeypatch.setattr(mc, "_logo_disponivel", lambda url: next(respostas))
    relogio = [0.0]
    monkeypatch.setattr(mc.time, "monotonic", lambda: relogio[0])

    assert not mc._logo_disponivel_cached("https://x/b.png")
    relogio[0] += 1800
    assert not mc._logo_disponivel_cached("https://x/b.png")
    relogio[0] += 1801
    assert mc._logo_disponivel_cached("https://x/b.png")


def test_checagem_de_logo_reaproveita_a_mesma_sessao(monkeypatch):
    class Resposta:
        status_code = 200

    class Sessao:
        def __init__(self):
            self.urls = []

        def head(self, url, **kw):
            self.urls.append(url)
            return Resposta()

    sessao = Sessao()
    monkeypatch.setattr(mc, "_SESSAO_LOGOS", sessao)
    assert mc._logo_disponivel("https://x/1.png")
    assert mc._logo_disponivel("https://x/2.png")
    assert sessao.urls == ["https://x/1.png", "https://x/2.png"]
    assert mc._sessao_logos() is sessao


def test_url_vazia_nao_vai_a_rede(monkeypatch, cache_de_logo_limpo):
    monkeypatch.setattr(mc, "_logo_disponivel",
                        lambda url: pytest.fail("não devia checar"))
    assert not mc._logo_disponivel_cached("")


# -- arquivo publicado ------------------------------------------------------

def _grava(caminho, dado):
    with gzip.open(caminho, "wb") as fh:
        fh.write(json.dumps(dado).encode("utf-8"))


def test_arquivo_publicado_e_lido_uma_vez_e_relido_quando_muda(tmp_path):
    caminho = str(tmp_path / "x.json.gz")
    _grava(caminho, {"v": 1})
    primeiro = arquivo_publicado.ler(caminho, "teste")
    assert primeiro == {"v": 1}
    assert arquivo_publicado.ler(caminho, "teste") is primeiro

    _grava(caminho, {"v": 2, "mais": "dado"})
    st = os.stat(caminho)
    os.utime(caminho, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert arquivo_publicado.ler(caminho, "teste") == {"v": 2, "mais": "dado"}


def test_arquivo_publicado_ausente_ou_corrompido_devolve_vazio(tmp_path):
    assert arquivo_publicado.ler(str(tmp_path / "nao.json.gz"), "teste") == {}
    ruim = tmp_path / "ruim.json.gz"
    ruim.write_bytes(b"isto nao e gzip")
    assert arquivo_publicado.ler(str(ruim), "teste") == {}


# -- seleção de fatores -----------------------------------------------------

def _selecao_original(y, X):
    """A regra de antes, com ``_n_obs_comuns`` a cada subconjunto."""
    colunas = sorted(X.columns)
    excluidos = []
    while colunas and factors._n_obs_comuns(y, X[colunas]) < factors.MIN_OBS_FATOR:
        def ganho(c):
            resto = [k for k in colunas if k != c]
            return (factors._n_obs_comuns(y, X[resto]) if resto
                    else len(y.dropna()))
        pior = min(colunas, key=lambda c: (-ganho(c), c))
        colunas.remove(pior)
        excluidos.append(pior)
    return colunas, tuple(sorted(excluidos))


@pytest.mark.parametrize("semente", range(40))
def test_selecao_de_fatores_igual_a_regra_original(semente):
    rng = np.random.default_rng(semente)
    datas = pd.date_range("2010-01-31", periods=int(rng.integers(20, 90)),
                          freq="ME")
    X = pd.DataFrame(rng.normal(size=(len(datas), 6)), index=datas,
                     columns=list(factors.PROXIES))
    # buracos em blocos, como IMAB11/IVVB11, e soltos
    for c in X.columns:
        if rng.random() < 0.6:
            ini = int(rng.integers(0, len(datas)))
            X.iloc[ini:ini + int(rng.integers(1, 40)),
                   X.columns.get_loc(c)] = np.nan
        X.loc[rng.random(len(datas)) < rng.random() * 0.3, c] = np.nan
    # y com datas que X não tem e vice-versa
    datas_y = datas[int(rng.integers(0, 10)):].append(
        pd.date_range(datas[-1] + pd.offsets.MonthEnd(1), periods=3, freq="ME"))
    y = pd.Series(rng.normal(size=len(datas_y)), index=datas_y)
    y[rng.random(len(y)) < 0.1] = np.nan

    X_novo, excl_novo = factors._selecionar_fatores(y, X)
    colunas_antes, excl_antes = _selecao_original(y, X)
    assert list(X_novo.columns) == colunas_antes
    assert excl_novo == excl_antes


def test_selecao_com_data_repetida_usa_a_contagem_original():
    datas = pd.date_range("2015-01-31", periods=30, freq="ME")
    X = pd.DataFrame(np.ones((30, 2)), index=datas, columns=["a", "b"])
    y = pd.Series(np.ones(31), index=datas.append(datas[:1]))
    X_novo, excl = factors._selecionar_fatores(y, X)
    assert list(X_novo.columns) == _selecao_original(y, X)[0]
    assert excl == ()

# -*- coding: utf-8 -*-
"""A-160: painel PIT vazio tem de dizer POR QUE está vazio.

O warehouse local tinha 55 mil linhas de safra (0.3.0 e 0.4.0) enquanto o
código já pedia a metodologia 0.5.0. A consulta filtrada por versão voltava
vazia, o backtest e o Rank-IC por indústria desligavam, e a tela culpava a
vitrine publicada -- mandando rodar `score-history`, que já tinha rodado.
Falha silenciosa: nenhuma exceção, nenhum log, uma explicação errada na tela.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool


def _engine_com_safras(linhas):
    eng = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.execute(text("ATTACH ':memory:' AS market_us"))
        c.execute(text("CREATE TABLE market_us.score_vintages (company_id INT, "
                       "symbol TEXT, score_version TEXT, as_of_date TEXT, "
                       "track TEXT, score REAL)"))
        c.execute(text("CREATE TABLE market_us.prices_monthly (symbol TEXT, "
                       "month_end TEXT, adjusted_close REAL)"))
        for v, s in linhas:
            c.execute(text("INSERT INTO market_us.score_vintages VALUES "
                           "(1,:s,:v,'2020-06-30','fundamental',60.0)"),
                      {"v": v, "s": s})
    return eng


@pytest.fixture
def leitor(monkeypatch):
    import core.us_read as ur
    monkeypatch.setattr(ur, "schema_ready", lambda *a, **k: True)
    return ur


def _preparar(leitor, monkeypatch, linhas):
    monkeypatch.setattr(leitor, "_engine", lambda: _engine_com_safras(linhas))


def test_safra_de_outra_versao_nao_e_ausencia_de_safra(leitor, monkeypatch):
    """A causa que a tela escondia: existe histórico, só não o desta versão."""
    _preparar(leitor, monkeypatch, [("0.4.0", "AAPL")])
    painel = leitor.load_score_panel(score_version="0.5.0")
    assert painel.empty
    motivo = painel.attrs["motivo"]
    assert "0.4.0" in motivo and "0.5.0" in motivo


def test_sem_nenhuma_safra_manda_rodar_score_history(leitor, monkeypatch):
    _preparar(leitor, monkeypatch, [])
    painel = leitor.load_score_panel(score_version="0.5.0")
    assert "score-history" in painel.attrs["motivo"]
    assert "0.4.0" not in painel.attrs["motivo"]


def test_sem_banco_o_motivo_e_o_banco(leitor, monkeypatch):
    monkeypatch.setattr(leitor, "_engine", lambda: None)
    assert "banco" in leitor.load_score_panel().attrs["motivo"]


def test_safra_certa_sem_preco_nao_culpa_a_versao(leitor, monkeypatch):
    """Casar safra com preço é outro passo; confundi-lo com versão errada
    mandaria reconstruir a base inteira para resolver falta de cotação."""
    _preparar(leitor, monkeypatch, [("0.5.0", "AAPL")])
    painel = leitor.load_score_panel(score_version="0.5.0")
    assert painel.empty
    assert "preço mensal" in painel.attrs["motivo"]


def test_tela_nao_inventa_causa_quando_a_camada_nao_registra(monkeypatch):
    import pandas as pd

    from views.empresas_americanas import _motivo_sem_painel
    assert "vitrine publicada" in _motivo_sem_painel(pd.DataFrame(), True)
    assert "não registrada" in _motivo_sem_painel(pd.DataFrame(), False)

"""Foto do Tesouro sozinha não apaga a renda variável do recálculo.

ADELAIDE (12/10/2026) subiu o relatório do Tesouro Direto e nunca o extrato
de custódia da B3. Como o snapshot manda na carteira assim que existe uma
linha dele, a tela passou a mostrar só os oito títulos públicos -- as ações
e os FIIs, que para ela só existem em portfolio_positions, sumiram.
"""
from types import SimpleNamespace

import pytest

from core import database, investimentos


def _carteira_so_do_tesouro(rows, tx_costs, manuais, curva_tesouro=None):
    return {
        "posicoes": [{"ticker": "TIPCA2029", "classe": "Tesouro Direto",
                      "setor": "Tesouro Direto", "moeda": "BRL",
                      "total_investido": 800.0, "valor_mercado": 1000.0,
                      "cotacao_fonte": "snapshot", "data_referencia": None,
                      "cor": "#000000"}],
        "total_investido": 800.0,
        "total_mercado": 1000.0,
        "diferenca_reais": 200.0,
        "num_ativos": 1,
        "rentabilidade_total_pct": 25.0,
        "cotacoes_disponiveis": False,
        "n_cotacoes_live": 0,
        "n_cotacoes_stale": 0,
        "por_classe": [],
        "por_setor": [],
        "avisos_dados": [],
    }


def _pp(ticker, classe, qtd, custo, moeda="BRL", preco=None):
    return SimpleNamespace(
        quantity=qtd, average_price=custo / qtd if qtd else 0, total_invested=custo,
        ticker=ticker, asset_name=ticker, asset_class=classe, sector=None,
        currency=moeda, current_price=preco, current_price_timestamp=None,
        usd_brl_rate=None, usd_brl_timestamp=None,
    )


_RECALCULO = [
    _pp("MXRF11", "reit", 100, 900.0, preco=10.0),
    _pp("TAEE3", "stock", 10, 300.0),
    _pp("TAEE3F", "stock", 5, 150.0),          # fracionário soma no lote padrão
    _pp("TIPCA2029", "treasury", 1, 800.0),    # a foto já cobre
    _pp("SPY", "etf", 1, 500.0, moeda="USD"),  # USD é dos extras
    _pp("VENDIDO3", "stock", 0, 0.0),
]


class _Resultado:
    def __init__(self, linhas, escalar=True):
        self._linhas, self._escalar = linhas, escalar

    def fetchall(self):
        return list(self._linhas)

    def scalar(self):
        return self._escalar


class _Conexao:
    def __init__(self, tem_custodia):
        self.tem_custodia = tem_custodia

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if sql == investimentos._SQL_POSICOES_SNAPSHOT:
            return _Resultado([object()])
        if sql == investimentos._SQL_TEM_FOTO_DE_CUSTODIA:
            return _Resultado([], escalar=self.tem_custodia)
        if sql == investimentos._SQL_POSICOES:
            return _Resultado(_RECALCULO)
        return _Resultado([])


def _banco(monkeypatch, tem_custodia):
    engine = SimpleNamespace(connect=lambda: _Conexao(tem_custodia))
    monkeypatch.setattr(investimentos.settings, "OWNER_USER_ID", "u1")
    monkeypatch.setattr(database, "get_engine", lambda: engine)
    monkeypatch.setattr(investimentos, "_montar_carteira_snapshot", _carteira_so_do_tesouro)
    monkeypatch.setattr(investimentos, "_calcular_custos_transacoes", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "cambio_medio_de_aquisicao", lambda conn, owner: {})
    monkeypatch.setattr(investimentos, "listar_precos_manuais", lambda owner, conn: {})


def test_so_tesouro_traz_acoes_e_fiis_do_recalculo(monkeypatch):
    _banco(monkeypatch, tem_custodia=False)

    carteira = investimentos._carteira_real()

    tickers = sorted(p["ticker"] for p in carteira["posicoes"])
    assert tickers == ["MXRF11", "TAEE3", "TIPCA2029"]
    taee = next(p for p in carteira["posicoes"] if p["ticker"] == "TAEE3")
    assert taee["quantidade"] == 15
    # 1000 do Tesouro + 100 x 10 do MXRF11 + TAEE3 sem cotação, a custo (450)
    assert carteira["total_mercado"] == pytest.approx(2450.0)
    assert carteira["total_investido"] == pytest.approx(2150.0)
    assert carteira["diferenca_reais"] == pytest.approx(300.0)
    assert carteira["num_ativos"] == 3
    assert sum(p["pct_carteira"] for p in carteira["posicoes"]) == pytest.approx(100.0)
    assert {c["nome"] for c in carteira["por_classe"]} >= {"Tesouro Direto", "FII"}


def test_com_custodia_o_recalculo_nao_entra(monkeypatch):
    # Com extrato da B3, o recálculo é o que a custódia desmente: posição
    # vendida em outra corretora continua nele.
    _banco(monkeypatch, tem_custodia=True)

    carteira = investimentos._carteira_real()

    assert [p["ticker"] for p in carteira["posicoes"]] == ["TIPCA2029"]
    assert carteira["total_mercado"] == pytest.approx(1000.0)


def test_foto_do_tesouro_nao_conta_como_custodia():
    sql = " ".join(investimentos._SQL_TEM_FOTO_DE_CUSTODIA.split())

    assert "NOT LIKE 'td-snap-%'" in sql
    assert "'b3_posicao_detalhada'" in sql

"""A guarda de valor de mercado tem que valer no caminho que publica a vitrine.

O defeito que originou estes testes: `load_scoring_frame` confrontava as duas
fontes de valor de mercado com `market_cap_confiavel` e saía com dividend yield
máximo de 0,52; `load_company_bundle` -- que alimenta o dossiê e o construtor da
vitrine -- devolvia o publicado cru e a vitrine no Supabase carregava 13.508
(1,35 milhão por cento ao ano). Nenhum teste pegava, porque os dois caminhos
nunca eram comparados entre si.
"""
from __future__ import annotations

import pytest

from core.us_metrics import market_cap_confiavel
from core.us_read import _latest_market_cap


class _Conn:
    """Conexão de mentira: responde às duas consultas por trecho do SQL."""

    def __init__(self, market_cap=None, close=None):
        self._mc = market_cap
        self._close = close

    def execute(self, sql, params=None):  # noqa: ARG002
        alvo = self._mc if "market_cap_history" in str(sql) else self._close
        return _Escalar(alvo)


class _Escalar:
    def __init__(self, valor):
        self._valor = valor

    def scalar(self):
        return self._valor


def _bal(shares):
    return [{"fiscal_year": 2024, "shares_outstanding": shares}]


def _inc(receita):
    return [{"fiscal_year": 2024, "revenue": receita}]


def test_publicado_absurdo_vira_lacuna_e_nao_valor_de_mercado():
    """PSKY: 10.290 dólares publicados com a ação a 10,86 = 947 ações implícitas."""
    conn = _Conn(market_cap=10_290.0, close=10.86)
    assert _latest_market_cap(conn, "PSKY", _bal(None), _inc(30e9)) is None


def test_escala_trocada_denunciada_pela_receita():
    """CHTR: 31 milhões de valor de mercado contra 54,8 bilhões de receita."""
    conn = _Conn(market_cap=31e6, close=300.0)
    assert _latest_market_cap(conn, "CHTR", _bal(140_000_000), _inc(54.8e9)) is None


def test_valor_coerente_passa_inteiro():
    conn = _Conn(market_cap=1.0e11, close=100.0)
    assert _latest_market_cap(
        conn, "OK", _bal(1_000_000_000), _inc(2.0e10)) == pytest.approx(1.0e11)


def test_sem_publicado_cai_no_derivado():
    conn = _Conn(market_cap=None, close=50.0)
    assert _latest_market_cap(
        conn, "OK", _bal(2_000_000), _inc(1.0e8)) == pytest.approx(1.0e8)


def test_sem_preco_e_sem_publicado_devolve_lacuna():
    conn = _Conn(market_cap=None, close=None)
    assert _latest_market_cap(conn, "OK", _bal(2_000_000), _inc(1.0e8)) is None


@pytest.mark.parametrize("publicado,preco,acoes,receita", [
    (10_290.0, 10.86, None, 30e9),
    (31e6, 300.0, 140_000_000, 54.8e9),
    (1.0e11, 100.0, 1_000_000_000, 2.0e10),
    (None, 50.0, 2_000_000, 1.0e8),
    (470_000.0, 12.0, 91_000_000, 8.0e8),
])
def test_dossie_e_cross_section_respondem_a_mesma_pergunta(
        publicado, preco, acoes, receita):
    """O que separa os dois caminhos é a origem dos dados, não a régua.

    Este é o teste que faltava: não basta cada lado estar "certo" sozinho, eles
    precisam devolver o MESMO valor de mercado para a mesma empresa. Enquanto a
    régua morava só em `load_scoring_frame`, a divergência era invisível.
    """
    derivado = preco * acoes if preco and acoes else None
    esperado = market_cap_confiavel(publicado, derivado, preco, receita)
    obtido = _latest_market_cap(
        _Conn(market_cap=publicado, close=preco), "X", _bal(acoes), _inc(receita))
    assert obtido == esperado

"""Câmbio de aquisição: ponderação, cobertura parcial e falha de leitura.

O defeito que estes testes prendem não era um número errado — era um campo
(`fx_rate_compra`) fixado em ``None``, que fazia o card de retorno consolidado
dizer "falta câmbio histórico" enquanto o dado estava no banco. Por isso há
aqui um teste que afirma o oposto do sintoma: com taxas diferentes nas duas
pontas, o retorno em BRL **tem** de diferir do retorno em USD.
"""
import pytest

from core.currency_returns import retorno_em_brl, retorno_moeda_origem
from core.fx_aquisicao import cambio_medio_de_aquisicao, taxa_para


class _Linha:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ConnFake:
    """Conexão que devolve linhas prontas — ou levanta, para o caminho de falha."""

    def __init__(self, linhas=None, erro: Exception | None = None):
        self._linhas = linhas or []
        self._erro = erro
        self.params: dict | None = None

    def execute(self, _sql, params=None):
        if self._erro is not None:
            raise self._erro
        self.params = params
        return self

    def fetchall(self):
        return self._linhas


def _linha(ticker, custo_usd, custo_coberto, custo_brl, n=2, n_taxa=2):
    return _Linha(ticker=ticker, custo_usd=custo_usd, custo_coberto=custo_coberto,
                  custo_brl=custo_brl, n_compras=n, n_com_taxa=n_taxa)


def test_taxa_media_e_ponderada_pelo_custo_nao_pela_contagem():
    """Uma compra de US$ 9.000 a 5,00 e outra de US$ 1.000 a 6,00 dão 5,10 —
    não 5,50, que seria a média simples das taxas."""
    conn = _ConnFake([_linha("SPY", 10_000.0, 10_000.0, 9_000 * 5.0 + 1_000 * 6.0)])
    mapa = cambio_medio_de_aquisicao(conn, "u1")
    assert mapa["SPY"]["taxa_media"] == pytest.approx(5.10)
    assert mapa["SPY"]["cobertura"] == pytest.approx(1.0)


def test_cobertura_parcial_nao_entrega_taxa():
    """Metade do custo sem câmbio: converter só a metade coberta pela taxa da
    época e o resto pela de hoje devolveria um custo que não existiu."""
    conn = _ConnFake([_linha("IEFA", 10_000.0, 5_000.0, 5_000 * 5.0, n=4, n_taxa=2)])
    mapa = cambio_medio_de_aquisicao(conn, "u1")
    assert mapa["IEFA"]["cobertura"] == pytest.approx(0.5)
    assert taxa_para(mapa, "IEFA") is None
    # Quem aceitar o risco precisa dizê-lo explicitamente.
    assert taxa_para(mapa, "IEFA", cobertura_minima=0.5) == pytest.approx(5.0)


def test_ticker_sem_nenhuma_taxa_fica_fora_do_mapa():
    conn = _ConnFake([_linha("XXXX", 1_000.0, 0.0, 0.0, n=1, n_taxa=0)])
    assert cambio_medio_de_aquisicao(conn, "u1") == {}


def test_falha_de_leitura_devolve_mapa_vazio_sem_derrubar():
    """Tabela ausente não pode derrubar a carteira inteira; o chamador volta
    ao comportamento anterior, que já se declarava estimado."""
    conn = _ConnFake(erro=RuntimeError("relation does not exist"))
    assert cambio_medio_de_aquisicao(conn, "u1") == {}


def test_busca_e_filtrada_por_usuario_e_janela():
    conn = _ConnFake([])
    cambio_medio_de_aquisicao(conn, "usuario-x", janela_dias=7)
    assert conn.params == {"uid": "usuario-x", "janela": 7}


def test_taxa_para_e_insensivel_a_caixa_e_a_ticker_ausente():
    conn = _ConnFake([_linha("SPY", 100.0, 100.0, 500.0)])
    mapa = cambio_medio_de_aquisicao(conn, "u1")
    assert taxa_para(mapa, "spy") == pytest.approx(5.0)
    assert taxa_para(mapa, "IVV") is None
    assert taxa_para({}, "SPY") is None


def test_retorno_em_brl_difere_do_retorno_em_usd_quando_as_taxas_diferem():
    """O sintoma original: usar a mesma taxa nos dois lados cancela o câmbio e
    devolve o retorno em USD com rótulo de BRL."""
    valor_hoje_usd, custo_usd = 11_000.0, 10_000.0
    fx_hoje, fx_compra = 5.1442, 5.4440

    em_usd = retorno_moeda_origem(valor_hoje_usd, custo_usd)
    em_brl = retorno_em_brl(valor_hoje_usd, custo_usd, fx_hoje, fx_compra)

    assert em_usd == pytest.approx(0.10)
    # Dólar caiu entre a compra e hoje: em reais o ganho é menor que em dólar.
    assert em_brl < em_usd
    assert em_brl == pytest.approx(
        (valor_hoje_usd * fx_hoje) / (custo_usd * fx_compra) - 1
    )

    # Com a mesma taxa dos dois lados, o efeito cambial some — é exatamente o
    # número que a tela exibia antes, acreditando ser BRL.
    assert retorno_em_brl(valor_hoje_usd, custo_usd, fx_hoje, fx_hoje) == pytest.approx(em_usd)

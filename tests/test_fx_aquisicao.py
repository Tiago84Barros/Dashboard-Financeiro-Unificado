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


def _compra(ticker, custo_usd, taxa, qtd=1.0):
    return _Linha(ticker=ticker, tipo="buy", quantidade=qtd, custo_usd=custo_usd, taxa=taxa)


def _venda(ticker, qtd):
    return _Linha(ticker=ticker, tipo="sell", quantidade=qtd, custo_usd=0.0, taxa=None)


def test_taxa_media_e_ponderada_pelo_custo_nao_pela_contagem():
    """Uma compra de US$ 9.000 a 5,00 e outra de US$ 1.000 a 6,00 dão 5,10 —
    não 5,50, que seria a média simples das taxas."""
    conn = _ConnFake([_compra("SPY", 9_000.0, 5.0), _compra("SPY", 1_000.0, 6.0)])
    mapa = cambio_medio_de_aquisicao(conn, "u1")
    assert mapa["SPY"]["taxa_media"] == pytest.approx(5.10)
    assert mapa["SPY"]["cobertura"] == pytest.approx(1.0)


def test_cobertura_parcial_nao_entrega_taxa():
    """Metade do custo sem câmbio: converter só a metade coberta pela taxa da
    época e o resto pela de hoje devolveria um custo que não existiu."""
    conn = _ConnFake([_compra("IEFA", 5_000.0, 5.0), _compra("IEFA", 5_000.0, None)])
    mapa = cambio_medio_de_aquisicao(conn, "u1")
    assert mapa["IEFA"]["cobertura"] == pytest.approx(0.5)
    assert mapa["IEFA"]["n_compras"] == 2 and mapa["IEFA"]["n_com_taxa"] == 1
    assert taxa_para(mapa, "IEFA") is None
    # Quem aceitar o risco precisa dizê-lo explicitamente.
    assert taxa_para(mapa, "IEFA", cobertura_minima=0.5) == pytest.approx(5.0)


def test_ticker_sem_nenhuma_taxa_fica_fora_do_mapa():
    conn = _ConnFake([_compra("XXXX", 1_000.0, None)])
    assert cambio_medio_de_aquisicao(conn, "u1") == {}


def test_venda_total_descarta_o_lote_e_a_recompra_tem_a_propria_taxa():
    """Comprado a 4,00, vendido inteiro, recomprado a 6,00: a posição de hoje
    custou 6,00 por dólar. O lote vendido não pode puxar a taxa para 5,00."""
    conn = _ConnFake([_compra("AAPL", 1_000.0, 4.0, qtd=10), _venda("AAPL", 10),
                      _compra("AAPL", 1_000.0, 6.0, qtd=5)])
    mapa = cambio_medio_de_aquisicao(conn, "u1")
    assert taxa_para(mapa, "AAPL") == pytest.approx(6.0)
    assert mapa["AAPL"]["custo_usd"] == pytest.approx(1_000.0)
    assert mapa["AAPL"]["n_compras"] == 1


def test_venda_sem_cobertura_zera_como_o_calculo_de_posicoes():
    """Venda maior que a posição (histórico truncado): positions.py zera e a
    compra seguinte abre base nova; aqui tem de ser igual."""
    conn = _ConnFake([_compra("MSFT", 500.0, 3.0, qtd=2), _venda("MSFT", 7),
                      _compra("MSFT", 800.0, 5.5, qtd=2)])
    assert taxa_para(cambio_medio_de_aquisicao(conn, "u1"), "MSFT") == pytest.approx(5.5)


def test_lote_sem_taxa_vendido_nao_bloqueia_a_cobertura_da_recompra():
    conn = _ConnFake([_compra("NVDA", 1_000.0, None, qtd=4), _venda("NVDA", 4),
                      _compra("NVDA", 1_000.0, 5.2, qtd=4)])
    assert taxa_para(cambio_medio_de_aquisicao(conn, "u1"), "NVDA") == pytest.approx(5.2)


def test_venda_parcial_nao_muda_a_taxa_mas_muda_o_peso_da_compra_seguinte():
    """Venda parcial tira a mesma fração de todo o lote: a taxa média fica.
    Mas a compra depois dela pesa contra o que **sobrou**, não contra o lote
    original — é a mesma conta do preço médio."""
    so_venda = _ConnFake([_compra("VOO", 1_000.0, 4.0, qtd=10),
                          _compra("VOO", 1_000.0, 6.0, qtd=10), _venda("VOO", 15)])
    assert taxa_para(cambio_medio_de_aquisicao(so_venda, "u1"), "VOO") == pytest.approx(5.0)

    # Sobram 5 de 20 (custo US$ 500 a 5,00); a recompra de US$ 500 a 7,00
    # pesa metade: (500*5 + 500*7) / 1000 = 6,00 — não (2000*5 + 500*7)/2500.
    com_recompra = _ConnFake([_compra("VOO", 1_000.0, 4.0, qtd=10),
                              _compra("VOO", 1_000.0, 6.0, qtd=10), _venda("VOO", 15),
                              _compra("VOO", 500.0, 7.0, qtd=5)])
    mapa = cambio_medio_de_aquisicao(com_recompra, "u1")
    assert taxa_para(mapa, "VOO") == pytest.approx(6.0)
    assert mapa["VOO"]["custo_usd"] == pytest.approx(1_000.0)


def test_taxa_historica_corrompida_nao_cobre_a_compra():
    """USDBRL 0,01 é lixo de importação; aceitá-lo levaria o custo em reais a
    quase zero e o retorno a milhares por cento."""
    conn = _ConnFake([_compra("QQQ", 1_000.0, 0.01)])
    assert taxa_para(cambio_medio_de_aquisicao(conn, "u1"), "QQQ") is None


def test_falha_de_leitura_devolve_mapa_vazio_sem_derrubar():
    """Tabela ausente não pode derrubar a carteira inteira; o chamador volta
    ao comportamento anterior, que já se declarava estimado."""
    conn = _ConnFake(erro=RuntimeError("relation does not exist"))
    assert cambio_medio_de_aquisicao(conn, "u1") == {}


def test_busca_e_filtrada_por_usuario_janela_e_taxa_minima():
    conn = _ConnFake([])
    cambio_medio_de_aquisicao(conn, "usuario-x", janela_dias=7)
    assert conn.params == {"uid": "usuario-x", "janela": 7, "taxa_minima": 2.0}


def test_taxa_para_e_insensivel_a_caixa_e_a_ticker_ausente():
    conn = _ConnFake([_compra("SPY", 100.0, 5.0)])
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

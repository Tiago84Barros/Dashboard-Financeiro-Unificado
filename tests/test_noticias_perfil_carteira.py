"""O sexto portão só existe se alguém lhe entregar uma carteira.

Estes testes prendem três coisas que já falharam em silêncio neste projeto:
ausência declarada em vez de zero, escala convertida na fronteira, e recusa de
dado sintético em decisão real.
"""
from __future__ import annotations

import pytest

from core.noticias import perfil_carteira as pc
from core.noticias import portoes
from core.noticias.portoes import PERFIL_VAZIO


def _carteira(posicoes, fonte="real"):
    return {"data_source": fonte, "posicoes": posicoes}


def test_pct_carteira_vira_fracao_e_nao_percentual_cru():
    """5% do patrimônio é 0,05 -- não 5,0.

    ``relevancia`` satura a exposição em 1,0. Sem a divisão por 100, qualquer
    posição acima de 1% viraria "carteira inteira exposta" e toda notícia da
    carteira receberia a nota máxima de exposição.
    """
    perfil, lim = pc.carregar(carteira=_carteira([
        {"ticker": "PETR4", "pct_carteira": 5.0},
        {"ticker": "VALE3", "pct_carteira": 12.5},
    ]))
    assert perfil.exposicao_por_ativo["PETR4"] == pytest.approx(0.05)
    assert perfil.exposicao_por_ativo["VALE3"] == pytest.approx(0.125)
    assert perfil.tickers == ("PETR4", "VALE3")
    assert lim == ()


def test_mock_mode_nao_vira_perfil():
    """Carteira de demonstração não pode abrir portão sobre notícia real."""
    perfil, lim = pc.carregar(carteira=_carteira(
        [{"ticker": "XPTO3", "pct_carteira": 90.0}], fonte="mock"))
    assert perfil is PERFIL_VAZIO
    assert lim and "mock" in lim[0]


def test_falha_de_leitura_e_declarada_e_nao_vira_carteira_vazia():
    """``data_source='error'`` é "não medido", e a diferença tem de aparecer.

    Uma carteira que não pôde ser lida e uma carteira sem posições levam à
    mesma decisão hoje (perfil vazio) e a diagnósticos opostos amanhã. Só a
    limitação escrita separa as duas.
    """
    perfil, lim = pc.carregar(carteira={
        "data_source": "error", "posicoes": [],
        "error_message": "banco fora do ar"})
    assert perfil is PERFIL_VAZIO
    assert lim and "banco fora do ar" in lim[0]

    vazia, lim_vazia = pc.carregar(carteira=_carteira([]))
    assert vazia is PERFIL_VAZIO
    assert lim_vazia and "sem posicoes" in lim_vazia[0]
    assert lim_vazia != lim


def test_peso_ilegivel_mantem_o_ativo_e_declara_a_lacuna():
    """Sem peso legível o ativo continua na carteira, com exposição 0,0.

    Tirá-lo de ``tickers`` faria o portão de relação dizer "nenhum ativo da
    notícia está na carteira" sobre um ativo que está.
    """
    perfil, lim = pc.carregar(carteira=_carteira([
        {"ticker": "PETR4", "pct_carteira": 5.0},
        {"ticker": "BBAS3", "pct_carteira": None},
    ]))
    assert "BBAS3" in perfil.tickers
    assert perfil.exposicao_por_ativo["BBAS3"] == 0.0
    assert lim and "BBAS3" in lim[0]


def test_o_portao_de_carteira_deixa_de_ser_inalcancavel():
    """Com perfil vazio o portão só podia devolver ``None``; com perfil, não.

    Este é o defeito que a correção fecha: um critério que nunca podia ser
    satisfeito nunca é revisto (``memoria: gate-que-so-dava-false``). O teste
    passa o portão de verdade, e não só o dataclass, porque o que estava
    quebrado era o caminho -- cada peça isolada já funcionava.
    """
    from tests.test_noticias_portoes import _nota_alta

    avaliada = _nota_alta()

    vazio = portoes._portao_carteira(avaliada, PERFIL_VAZIO)
    assert vazio.satisfeito is None and vazio.aprovado is False

    perfil, _ = pc.carregar(carteira=_carteira([
        {"ticker": "ALFA3", "pct_carteira": 4.0}]))
    com_carteira = portoes._portao_carteira(avaliada, perfil)
    assert com_carteira.satisfeito is True


def test_o_job_entrega_o_perfil_a_coleta():
    """Espelha ``test_o_job_entrega_o_universo_a_coleta`` para o sexto portao.

    O perfil e o universo tinham o mesmo defeito de origem -- modulo correto,
    testado, e sem chamador no pipeline. O teste falha se ``perfil=`` sair da
    chamada a ``coletar``, que e exatamente a regressao que ninguem veria: sem
    o argumento, ``coletar`` cai em ``PERFIL_VAZIO`` por omissao e nao levanta
    nada. Defeito silencioso se esconde melhor que erro.
    """
    from data_pipeline.jobs import update_noticias as job
    from tests.test_noticias_universo_entidades import (
        _Parar, _rodar_ate_a_coleta, _universo)

    recebido: dict = {}
    u, _ = _universo()

    def _coletar(consulta, provedores, **kw):
        recebido.update(kw)
        raise _Parar()

    class _EntUni:
        @staticmethod
        def carregar(*, engine=None):
            return u, ()

    perfil, _ = pc.carregar(carteira=_carteira([
        {"ticker": "ALFA3", "pct_carteira": 4.0}]))

    class _PerfilMod:
        @staticmethod
        def carregar():
            return perfil, ("limitacao de teste",)

    with pytest.raises(_Parar):
        _rodar_ate_a_coleta(job, _EntUni, _coletar, perfil_mod=_PerfilMod)

    assert "perfil" in recebido, (
        "coletar foi chamado sem perfil=: o portao de carteira devolve None em "
        "toda coleta e 'sugerir_revisao' fica inalcancavel")
    assert recebido["perfil"] is perfil
    assert not recebido["perfil"].vazio

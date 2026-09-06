"""A entrada do portao quantitativo tem fonte de producao (A-141).

Por que este arquivo existe
---------------------------
``coleta.coletar`` aceita ``bases`` desde que o motor de impacto foi escrito, e
ate 05/09/2026 nenhum chamador de producao preenchia o argumento. A revisao de
02/09 mediu a consequencia: em 12 de 12 cenarios o portao quantitativo saia
``indeterminado``, e ``None`` nao aprova -- ``sugerir_revisao`` era
estruturalmente inalcancavel. Corrigir o portao teria mudado o lugar do
problema; o que faltava era a porta de entrada.

O que se cobra aqui:

1. **Ausencia e declarada, nunca silenciosa.** Todo caminho que nao mede
   devolve ``({}, (motivo,))`` -- nunca ``({}, ())``, que a coleta leria como
   "medimos e nao ha nada".
2. **O horizonte pedido e um horizonte medido.** ``amostra.resumir`` filtra por
   horizonte medido; pedir um que o pipeline nao mede devolve base vazia sem
   erro nenhum, para sempre.
3. **A fiacao existe nos dois pontos de entrada** -- job e botao manual --,
   verificada por mutacao: tirar ``bases=`` da chamada tem de reprovar.
"""
from __future__ import annotations

import pytest

from core.noticias import bases_historicas as bh

# ─────────────────────────── o horizonte pedido ──────────────────────────────

def test_o_horizonte_pedido_e_um_dos_horizontes_medidos():
    """O defeito que este teste tranca ja estava escrito no modulo.

    ``HORIZONTE_PREGOES`` nasceu 21 -- um mes corrido, numero plausivel --, e
    21 nao esta em ``retornos.HORIZONTES``. ``resumir`` so empilha evento com
    aquele horizonte medido, entao a consequencia nao seria excecao: seria
    amostra vazia, base ``None`` e portao em "nao medido" em toda noticia, com
    o codigo parecendo correto. Gate que so podia dar False nunca e revisto.
    """
    from core.memoria_mercado.retornos import HORIZONTES

    assert bh.HORIZONTE_PREGOES in HORIZONTES


def test_horizonte_nao_medido_nao_vira_base_vazia_silenciosa():
    """Se alguem pedir um horizonte fora da grade, tem de sobrar limitacao."""
    class _Repo:
        @staticmethod
        def carregar_eventos(engine):
            return [{"chave": "a", "simbolo": "ALFA3",
                     "tipo_evento": "fusao_aquisicao", "data_evento": None,
                     "janelas": {}, "limitacoes": []}]

    bases, limitacoes = _com_repo(_Repo, horizonte=99)
    assert bases == {}
    assert limitacoes, (
        "horizonte fora da grade devolveu base vazia sem dizer por que")


# ───────────────────────── os caminhos de ausencia ───────────────────────────

def _com_repo(repo, **kw):
    """Troca ``repositorio.carregar_eventos`` pelo duble, no lugar certo.

    A primeira versao trocava ``sys.modules["core.memoria_mercado.repositorio"]``
    e passava sozinha, reprovando na suite inteira -- porque ``from pkg import
    mod`` resolve pelo **atributo do pacote** quando o modulo real ja foi
    importado, e so cai em ``sys.modules`` quando nao ha atributo. Na execucao
    isolada o pacote ainda nao tinha o atributo; na suite completa, tinha. O
    duble certo e a funcao, no modulo real.
    """
    import unittest.mock as _mock

    from core.memoria_mercado import repositorio as real

    with _mock.patch.object(real, "carregar_eventos", repo.carregar_eventos):
        return bh.carregar(engine=object(), **kw)


def test_sem_armazem_a_ausencia_e_declarada(monkeypatch):
    """Sem armazem local, a coleta precisa saber que nao mediu.

    ``({}, ())`` diria "medimos e nao ha base historica nenhuma", que e uma
    afirmacao sobre o mundo que ninguem apurou.
    """
    from core.memoria_mercado import destino as mm_destino

    monkeypatch.setattr(mm_destino, "engine_memoria", lambda: None)
    bases, limitacoes = bh.carregar()

    assert bases == {}
    assert limitacoes, "ausencia de armazem passou sem limitacao declarada"
    assert "nao medido" in " ".join(limitacoes)


def test_sem_safra_a_limitacao_diz_como_construir():
    """Limitacao que nao diz o que fazer vira ruido de log."""
    class _Repo:
        @staticmethod
        def carregar_eventos(engine):
            return []

    bases, limitacoes = _com_repo(_Repo)
    assert bases == {}
    assert any("construir_memoria_mercado" in m for m in limitacoes)


def test_leitura_que_falha_nao_vira_base_vazia():
    """Excecao na leitura e ausencia declarada, nao base vazia.

    Engolir a excecao e devolver ``({}, ())`` seria o modo de falha classico:
    o defeito silencioso se esconde melhor que o erro.
    """
    class _Repo:
        @staticmethod
        def carregar_eventos(engine):
            raise RuntimeError("conexao recusada")

    bases, limitacoes = _com_repo(_Repo)
    assert bases == {}
    assert any("conexao recusada" in m for m in limitacoes)


def test_a_leitura_nunca_toca_o_supabase():
    """A instrucao do usuario e literal, e o repositorio a aplica sozinho.

    ``repositorio.carregar_eventos`` chama ``exigir_local`` na primeira linha:
    um destino remoto aqui vira excecao, e nao leitura -- muito menos gravacao.
    """
    import inspect

    from core.memoria_mercado import repositorio as repo

    fonte = inspect.getsource(repo.carregar_eventos)
    assert "exigir_local(engine)" in fonte


# ──────────────────────────────── a fiacao ───────────────────────────────────

def test_o_job_entrega_as_bases_a_coleta():
    """Espelha o teste do perfil: sem ``bases=``, ``coletar`` cai no padrao.

    E o padrao e ``None``, que nao levanta nada e reabre A-141 inteiro sem
    deixar rastro no log.
    """
    from data_pipeline.jobs import update_noticias as job
    from tests.test_noticias_universo_entidades import (
        _Parar,
        _rodar_ate_a_coleta,
        _universo,
    )

    esperadas = {"fusao_aquisicao": object()}
    u, _ = _universo()

    class _EntUni:
        @staticmethod
        def carregar(*, engine=None):
            return u, ()

    class _BasesMod:
        @staticmethod
        def carregar():
            return esperadas, ("limitacao de teste",)

    recebido: dict = {}

    def _coletar(consulta, provedores, **kw):
        recebido.update(kw)
        raise _Parar()

    with pytest.raises(_Parar):
        _rodar_ate_a_coleta(job, _EntUni, _coletar, bases_mod=_BasesMod)

    assert "bases" in recebido, (
        "coletar foi chamado sem bases=: o portao quantitativo volta a sair "
        "'indeterminado' em toda noticia e 'sugerir_revisao' fica inalcancavel")
    assert recebido["bases"] is esperadas


def test_o_botao_manual_tambem_entrega_as_bases():
    """A view tem o mesmo defeito potencial e nao e coberta pelo teste do job.

    Duas portas de entrada, uma fiada e outra nao, e a diferenca so aparece em
    producao: o mesmo motor daria vereditos diferentes conforme quem o chamou.
    """
    import inspect

    from views import inteligencia_mercado as view

    fonte = inspect.getsource(view.coletar_noticias)
    assert "bases_historicas" in fonte
    assert "bases=bases" in fonte


@pytest.mark.parametrize("modulo", ["data_pipeline.jobs.update_noticias",
                                    "views.inteligencia_mercado"])
def test_as_limitacoes_das_bases_nao_sao_descartadas(modulo):
    """No job, "nao medimos" precisa chegar ao relatorio da execucao."""
    import importlib
    import inspect

    fonte = inspect.getsource(importlib.import_module(modulo))
    if "update_noticias" in modulo:
        assert "lim_bases" in fonte and "limitacoes.extend(lim_bases)" in fonte
    else:
        assert "bases_mod.carregar()" in fonte

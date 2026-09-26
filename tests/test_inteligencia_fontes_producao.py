"""Inteligência de Mercado em produção: notícia pelo túnel, macro por fallback.

Sem acervo local, ``ler_recentes`` caía no Supabase -- onde ``noticias_itens``
não existe -- e a tela dizia "não pôde ser lido" com o túnel servindo as mesmas
linhas ao chat. O macro do parecer pedia só o Docker.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from core import armazem_remoto
from core.macro_data import context as mc
from core.macro_data import database as macro_db
from core.macro_data import insumos_publicados as ip
from core.noticias import destino
from tests.test_macro_insumos_publicados import _PUBLICADO, _arquivo


def _nao_chamar(*_a, **_k):
    raise AssertionError("não deveria ler por aqui")


def test_sem_acervo_local_a_tela_le_pelo_tunel(monkeypatch):
    from views import inteligencia_mercado as im

    monkeypatch.setattr(destino, "engine_acervo", lambda: None)
    monkeypatch.setattr(armazem_remoto, "noticias_recentes", lambda limite, dias: [
        {"id_dedup": "x", "titulo": "Copom mantém Selic", "nota": "72.5",
         "publicado_em": "2026-09-25T10:00:00+00:00",
         "coletado_em": "2026-09-25T11:00:00"}])
    linhas = im._linhas_do_acervo(_nao_chamar, 50)
    assert linhas[0]["publicado_em"].tzinfo is not None
    assert isinstance(linhas[0]["coletado_em"], datetime)

    itens, quando, motivo = im.carregar_acervo()
    assert motivo == "" and itens[0].titulo == "Copom mantém Selic"
    assert quando == datetime.fromisoformat("2026-09-25T11:00:00+00:00")


def test_sem_acervo_e_sem_tunel_o_motivo_diz_os_dois(monkeypatch):
    from views import inteligencia_mercado as im

    monkeypatch.setattr(destino, "engine_acervo", lambda: None)
    monkeypatch.setattr(armazem_remoto, "noticias_recentes", lambda *a, **k: None)
    itens, _, motivo = im.carregar_acervo()
    assert itens == () and "túnel" in motivo


def test_macro_cai_no_arquivo_publicado(monkeypatch):
    arquivo = _arquivo()
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: None)
    monkeypatch.setattr(armazem_remoto, "macro_recente", lambda: None)
    monkeypatch.setattr(ip, "carregar_insumos_publicados", lambda: arquivo)
    fatos, origem = mc.available_macro_context()
    assert fatos and origem == "insumos macro publicados em 26/09/2026"


def test_macro_prefere_o_tunel_ao_arquivo(monkeypatch):
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: None)
    monkeypatch.setattr(armazem_remoto, "macro_recente", lambda: [{"indicator": "x"}])
    monkeypatch.setattr(ip, "carregar_insumos_publicados", _nao_chamar)
    assert mc.available_macro_context()[1].endswith("pelo túnel")


def test_macro_sem_fonte_nenhuma(monkeypatch):
    monkeypatch.setattr(macro_db, "get_local_macro_engine", lambda: None)

    def cai():
        raise armazem_remoto.ArmazemRemotoIndisponivel("HTTP 503")
    monkeypatch.setattr(armazem_remoto, "macro_recente", cai)
    monkeypatch.setattr(ip, "carregar_insumos_publicados", lambda: None)
    assert mc.available_macro_context() == ((), None)


def test_parecer_declara_a_origem_do_macro(monkeypatch):
    from core.inteligencia import llm
    from core.inteligencia import painel as P

    monkeypatch.setattr(mc, "available_macro_context",
                        lambda: ((), "insumos macro publicados em 26/09/2026"))
    pn = P.montar(agora=_PUBLICADO + timedelta(hours=1))
    texto = llm.contexto(pn)
    assert "MACRO: origem insumos macro publicados em 26/09/2026" in texto

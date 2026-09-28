"""Porta publica `registrar_lacuna`: grava uma vez por sessao e nunca quebra a tela."""
import json

import pytest

from core.lacunas import destino, registrar_lacuna, registro


@pytest.fixture
def arquivo(tmp_path, monkeypatch):
    caminho = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(destino, "ARQUIVO_LOCAL", caminho)
    monkeypatch.setenv("LACUNAS_DESTINO", "local")
    registro._limpar_vistas()
    yield caminho
    registro._limpar_vistas()


def _eventos(caminho):
    if not caminho.exists():
        return []
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]


def test_grava_no_arquivo_local(arquivo):
    registrar_lacuna("motor", "fii.vpa", "sem VPA", entidade="HGLG11")
    (evento,) = _eventos(arquivo)
    assert evento["fonte"] == "motor"
    assert evento["entidade"] == "HGLG11"


def test_mesma_impressao_grava_uma_vez_por_sessao(arquivo):
    registrar_lacuna("motor", "fii.vpa", "faltam 12", entidade="HGLG11")
    registrar_lacuna("motor", "fii.vpa", "faltam 13", entidade="HGLG11")
    registrar_lacuna("motor", "fii.vpa", "faltam 13", entidade="KNRI11")
    assert len(_eventos(arquivo)) == 2


def test_modulo_inferido_e_arquivo_e_funcao_sem_linha(arquivo):
    registrar_lacuna("tela", "x", "m")
    (evento,) = _eventos(arquivo)
    assert evento["modulo"] == "tests/test_lacunas_registro.py:test_modulo_inferido_e_arquivo_e_funcao_sem_linha"


def test_modulo_explicito_vence_a_inferencia(arquivo):
    registrar_lacuna("tela", "x", "m", modulo="views/fiis.py:render")
    assert _eventos(arquivo)[0]["modulo"] == "views/fiis.py:render"


def test_sob_pytest_sem_variavel_nada_e_gravado(tmp_path, monkeypatch):
    caminho = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(destino, "ARQUIVO_LOCAL", caminho)
    monkeypatch.delenv("LACUNAS_DESTINO", raising=False)
    registro._limpar_vistas()
    registrar_lacuna("motor", "c", "m")
    assert not caminho.exists()


def test_falha_de_gravacao_nao_propaga(arquivo, monkeypatch):
    def explode(*a, **k):
        raise OSError("disco cheio")
    monkeypatch.setattr(destino, "gravar_local", explode)
    registrar_lacuna("motor", "c", "m")  # nao levanta


def test_fonte_invalida_nao_propaga(arquivo):
    registrar_lacuna("inventada", "c", "m")
    assert _eventos(arquivo) == []


def test_supabase_sem_engine_nao_faz_nada(monkeypatch):
    monkeypatch.setenv("LACUNAS_DESTINO", "supabase")
    monkeypatch.setattr(registro, "_engine", lambda: None)
    chamadas = []
    monkeypatch.setattr(destino, "gravar_banco", lambda *a: chamadas.append(a))
    registro._limpar_vistas()
    registrar_lacuna("motor", "c", "m")
    registro._aguardar_envios()
    assert chamadas == []


def test_supabase_grava_pela_thread(monkeypatch):
    monkeypatch.setenv("LACUNAS_DESTINO", "supabase")
    sentinela = object()
    monkeypatch.setattr(registro, "_engine", lambda: sentinela)
    chamadas = []
    monkeypatch.setattr(destino, "gravar_banco", lambda eng, lac: chamadas.append((eng, lac.codigo)))
    registro._limpar_vistas()
    registrar_lacuna("motor", "fii.vpa", "m")
    registro._aguardar_envios()
    assert chamadas == [(sentinela, "fii.vpa")]

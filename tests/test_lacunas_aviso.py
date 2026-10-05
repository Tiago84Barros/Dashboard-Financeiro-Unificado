"""`aviso_lacuna` e `detalhe_tecnico`: registram em silencio, sem exibir.

Desde 05/10/2026 restricoes e detalhes tecnicos saem da tela de uso e vao
para Configuracoes -> Restricoes."""
import json

import pytest
import streamlit as st

import design.lacunas as dl
from core.lacunas import destino, registro


@pytest.fixture
def arquivo(tmp_path, monkeypatch):
    caminho = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(destino, "ARQUIVO_LOCAL", caminho)
    monkeypatch.setenv("LACUNAS_DESTINO", "local")
    registro._limpar_vistas()
    yield caminho
    registro._limpar_vistas()


@pytest.fixture
def exibidos(monkeypatch):
    chamadas = []
    # Vigia o proprio streamlit: o modulo nao o importa mais, entao qualquer
    # exibicao que voltasse teria de passar por aqui.
    for nivel in ("info", "warning", "caption", "error", "markdown"):
        monkeypatch.setattr(st, nivel,
                            lambda msg, _n=nivel, **kw: chamadas.append((_n, msg, kw)))
    return chamadas


def _eventos(caminho):
    if not caminho.exists():
        return []
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]


def _tela_do_fii():
    dl.aviso_lacuna("Sem dados para HGLG11.", codigo="tela.fii.sem_dados",
                    nivel="warning", entidade="hglg11")


def test_nao_exibe_e_registra_com_modulo_de_quem_chamou(arquivo, exibidos):
    """Desde 05/10/2026 a lacuna sai da tela de uso: so o log a recebe, e o
    administrador a ve em Configuracoes -> Restricoes."""
    _tela_do_fii()
    assert exibidos == []
    (ev,) = _eventos(arquivo)
    assert ev["fonte"] == "tela"
    assert ev["codigo"] == "tela.fii.sem_dados"
    assert ev["entidade"] == "HGLG11"
    assert ev["modulo"] == "tests/test_lacunas_aviso.py:_tela_do_fii"


def test_nivel_e_icone_sao_aceitos_e_ignorados(arquivo, exibidos):
    dl.aviso_lacuna("x", codigo="c", icon="🎯")
    dl.aviso_lacuna("a", codigo="c1", nivel="caption", icon="🎯")
    dl.aviso_lacuna("b", codigo="c2", nivel="erro")
    assert exibidos == []
    assert len(_eventos(arquivo)) == 3


def test_falha_do_registro_nao_propaga(arquivo, exibidos, monkeypatch):
    monkeypatch.setattr(destino, "gravar_local", lambda *a, **k: 1 / 0)
    dl.aviso_lacuna("x", codigo="c")
    assert exibidos == []


def test_mesmo_codigo_com_texto_diferente_e_a_mesma_lacuna(arquivo, exibidos):
    dl.aviso_lacuna("faltam 3", codigo="tela.x")
    dl.aviso_lacuna("faltam 4", codigo="tela.x")
    assert len(_eventos(arquivo)) == 1


def _rodape_do_fii():
    dl.detalhe_tecnico("Metodologia 2.32.0 · vitrine de 04/10", codigo="fii.metodologia")


def test_detalhe_tecnico_nao_exibe_e_ganha_prefixo(arquivo, exibidos):
    _rodape_do_fii()
    dl.detalhe_tecnico("n=447", codigo="detalhe.b3.amostra")
    assert exibidos == []
    a, b = _eventos(arquivo)
    assert a["codigo"] == "detalhe.fii.metodologia"
    assert a["modulo"] == "tests/test_lacunas_aviso.py:_rodape_do_fii"
    assert b["codigo"] == "detalhe.b3.amostra"


def test_falha_de_acao_mostra_frase_sem_a_excecao_e_registra_erro(arquivo, exibidos):
    try:
        raise RuntimeError("host=db.secreto senha=x")
    except RuntimeError as exc:
        dl.falha_de_acao("Não foi possível salvar.", exc)
    assert [(n, m) for n, m, _ in exibidos] == [
        ("error", f"Não foi possível salvar. {dl.AVISO_REGISTRADO}")]
    (ev,) = _eventos(arquivo)
    assert ev["fonte"] == "excecao" and ev["codigo"] == "RuntimeError"
    assert "secreto" not in json.dumps(ev)

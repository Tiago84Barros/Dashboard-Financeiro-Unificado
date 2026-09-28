"""`aviso_lacuna`: exibe o aviso e registra a lacuna de tela numa chamada so."""
import json

import pytest

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
    for nivel in ("info", "warning", "caption"):
        monkeypatch.setattr(dl.st, nivel,
                            lambda msg, _n=nivel, **kw: chamadas.append((_n, msg, kw)))
    return chamadas


def _eventos(caminho):
    if not caminho.exists():
        return []
    return [json.loads(linha) for linha in caminho.read_text(encoding="utf-8").splitlines()]


def _tela_do_fii():
    dl.aviso_lacuna("Sem dados para HGLG11.", codigo="tela.fii.sem_dados",
                    nivel="warning", entidade="hglg11")


def test_exibe_e_registra_com_modulo_de_quem_chamou(arquivo, exibidos):
    _tela_do_fii()
    assert exibidos == [("warning", "Sem dados para HGLG11.", {})]
    (ev,) = _eventos(arquivo)
    assert ev["fonte"] == "tela"
    assert ev["codigo"] == "tela.fii.sem_dados"
    assert ev["entidade"] == "HGLG11"
    assert ev["modulo"] == "tests/test_lacunas_aviso.py:_tela_do_fii"


def test_nivel_padrao_e_info_e_icone_passa_adiante(arquivo, exibidos):
    dl.aviso_lacuna("x", codigo="c", icon="🎯")
    assert exibidos == [("info", "x", {"icon": "🎯"})]


def test_caption_ignora_icone_e_nivel_invalido_vira_info(arquivo, exibidos):
    dl.aviso_lacuna("a", codigo="c1", nivel="caption", icon="🎯")
    dl.aviso_lacuna("b", codigo="c2", nivel="erro")
    assert exibidos == [("caption", "a", {}), ("info", "b", {})]


def test_aviso_aparece_mesmo_se_o_registro_falhar(arquivo, exibidos, monkeypatch):
    monkeypatch.setattr(destino, "gravar_local", lambda *a, **k: 1 / 0)
    dl.aviso_lacuna("x", codigo="c")
    assert exibidos == [("info", "x", {})]


def test_mesmo_codigo_com_texto_diferente_e_a_mesma_lacuna(arquivo, exibidos):
    dl.aviso_lacuna("faltam 3", codigo="tela.x")
    dl.aviso_lacuna("faltam 4", codigo="tela.x")
    assert len(exibidos) == 2
    assert len(_eventos(arquivo)) == 1

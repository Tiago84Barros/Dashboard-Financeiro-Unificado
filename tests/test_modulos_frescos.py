"""Módulo do projeto que mudou em disco sai do sys.modules antes do rerun.

Reproduz a queda de 30/09/2026: o app.py novo importou um nome que só existia
na versão nova de design/componentes.py, e o processo tinha a antiga em
memória. Nada aqui toca rede nem banco.
"""
import importlib
import sys

import pytest

from core import modulos_frescos as mf


@pytest.fixture
def projeto(tmp_path, monkeypatch):
    pacote = tmp_path / "pacote_fresco"
    pacote.mkdir()
    (pacote / "__init__.py").write_text("", encoding="utf-8")
    (pacote / "componentes.py").write_text("def antiga():\n    return 1\n",
                                           encoding="utf-8")
    (pacote / "view.py").write_text("from pacote_fresco.componentes import antiga\n",
                                    encoding="utf-8")
    monkeypatch.setattr(mf, "RAIZ", str(tmp_path))
    monkeypatch.setattr(mf, "_MARCAS", {})
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield pacote
    for nome in [n for n in sys.modules if n.startswith("pacote_fresco")]:
        sys.modules.pop(nome)


def _deploy(pacote):
    (pacote / "componentes.py").write_text(
        "def antiga():\n    return 2\n\n\ndef nova():\n    return 3\n", encoding="utf-8")
    importlib.invalidate_caches()


def test_sem_o_guarda_o_nome_novo_da_import_error(projeto):
    importlib.import_module("pacote_fresco.view")
    _deploy(projeto)
    with pytest.raises(ImportError):
        exec("from pacote_fresco.componentes import nova", {})


def test_com_o_guarda_o_rerun_importa_a_versao_nova(projeto):
    importlib.import_module("pacote_fresco.view")
    assert mf.descartar_se_o_codigo_mudou() == []
    _deploy(projeto)

    assert mf.descartar_se_o_codigo_mudou() == ["pacote_fresco.componentes"]
    ns = {}
    exec("from pacote_fresco.componentes import nova", ns)
    assert ns["nova"]() == 3


def test_descarta_todos_os_do_projeto_e_nao_so_o_que_mudou(projeto):
    """Quem fez ``from x import y`` guarda o y antigo; reimportar só x
    deixaria duas versões convivendo."""
    importlib.import_module("pacote_fresco.view")
    mf.descartar_se_o_codigo_mudou()
    _deploy(projeto)
    mf.descartar_se_o_codigo_mudou()
    assert "pacote_fresco.view" not in sys.modules
    assert importlib.import_module("pacote_fresco.view").antiga() == 2


def test_sem_mudanca_nada_sai(projeto):
    importlib.import_module("pacote_fresco.view")
    mf.descartar_se_o_codigo_mudou()
    assert mf.descartar_se_o_codigo_mudou() == []
    assert "pacote_fresco.componentes" in sys.modules


def test_fora_do_projeto_e_main_ficam(tmp_path, monkeypatch):
    monkeypatch.setattr(mf, "RAIZ", str(tmp_path))

    class M:
        pass

    dentro, venv, fora = M(), M(), M()
    dentro.__file__ = str(tmp_path / "core" / "x.py")
    venv.__file__ = str(tmp_path / ".venv" / "lib" / "site-packages" / "y.py")
    fora.__file__ = str(tmp_path.parent / "z.py")
    assert mf._arquivo_do_projeto("core.x", dentro)
    assert mf._arquivo_do_projeto("__main__", dentro) is None
    assert mf._arquivo_do_projeto("y", venv) is None
    assert mf._arquivo_do_projeto("z", fora) is None
    assert mf._arquivo_do_projeto("builtin", M()) is None


def test_app_chama_o_guarda_antes_de_importar_o_projeto():
    from pathlib import Path

    fonte = Path(mf.RAIZ, "app.py").read_text(encoding="utf-8")
    chamada = fonte.index("descartar_se_o_codigo_mudou()")
    assert chamada < fonte.index("from core.app_test_mode import")
    assert chamada < fonte.index("from design.componentes import")

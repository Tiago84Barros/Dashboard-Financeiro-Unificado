"""Os dublês de bootstrap precisam acompanhar o que `app.py` importa.

Sem esta trava, acrescentar `from core.X import y` ao `app.py` derruba os
testes que executam o módulo via `runpy` — e derruba com `ImportError`, que
não nomeia o dublê desatualizado e manda procurar no lugar errado. Foi o que
aconteceu quando a sidebar ganhou `encerrar_sessao` e `principal`.

A leitura é por AST, não por texto: o import que interessa está aninhado
dentro de `with st.sidebar:` e de `if not _APP_TEST_MODE:`, e `ast.walk`
enxerga os dois níveis.
"""
from __future__ import annotations

import ast
from pathlib import Path

from tests.app_bootstrap_stubs import (
    MODULOS_EXECUTADOS_DE_VERDADE,
    stubs_de_bootstrap,
)

_APP_TREE = ast.parse(Path("app.py").read_text(encoding="utf-8"))


def _imports_do_app() -> dict[str, set[str]]:
    """Módulo → nomes que `app.py` importa dele, em qualquer profundidade."""
    encontrados: dict[str, set[str]] = {}
    for node in ast.walk(_APP_TREE):
        if isinstance(node, ast.ImportFrom) and node.module:
            nomes = encontrados.setdefault(node.module, set())
            nomes.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                encontrados.setdefault(alias.name, set())
    return encontrados


def test_todo_nome_importado_existe_no_duble_do_modulo():
    stubs = stubs_de_bootstrap()
    faltando: list[str] = []
    for modulo, nomes in _imports_do_app().items():
        duble = stubs.get(modulo)
        if duble is None:
            continue
        faltando += [
            f"{modulo}.{nome}" for nome in sorted(nomes) if not hasattr(duble, nome)
        ]
    assert not faltando, (
        "app.py importa nomes que o dublê não expõe — os testes que rodam "
        f"app.py sob runpy vão falhar com ImportError: {faltando}"
    )


def test_todo_modulo_do_projeto_importado_pelo_app_foi_decidido():
    """Ou tem dublê, ou está na lista dos que executam de verdade."""
    stubs = stubs_de_bootstrap()
    indecisos = sorted(
        modulo
        for modulo in _imports_do_app()
        if modulo.split(".")[0] in {"core", "design", "views"}
        and modulo not in stubs
        and modulo not in MODULOS_EXECUTADOS_DE_VERDADE
    )
    assert not indecisos, (
        "app.py passou a importar módulo do projeto sem decisão registrada: "
        f"{indecisos}. Acrescente um dublê em stubs_de_bootstrap() ou "
        "declare-o em MODULOS_EXECUTADOS_DE_VERDADE."
    )

"""
core/modulos_frescos.py
Descarta os módulos do projeto quando o código deles mudou em disco.

O Streamlit roda o ``app.py`` do zero a cada rerun, mas o que ele importa fica
no ``sys.modules`` do processo. Na Streamlit Cloud, o pull de um merge troca
os arquivos sem invalidar esses módulos, e o app passa a rodar o ``app.py``
novo sobre ``views/``, ``core/`` e ``design/`` antigos. Em 24/09/2026 isso
deixou abas velhas; em 30/09/2026 derrubou a produção: o ``app.py`` do PR #406
importou ``transicao_de_pagina`` de um ``design.componentes`` carregado antes
dela existir (``ImportError``). Nos dois casos só o Reboot resolveu.

Aqui cada arquivo do projeto já importado é marcado por (mtime, tamanho). Se
algum muda, **todos** os módulos do projeto saem do ``sys.modules`` e o rerun
os importa de novo. Todos, e não só o que mudou: quem fez
``from design.componentes import x`` guarda o ``x`` antigo, e descartar só
``design.componentes`` deixaria as duas versões convivendo.

A marca de um arquivo nasce na primeira vez que ele é visto. Um módulo que já
estava velho antes de este guarda existir no processo não é detectado; para
esse, o Reboot continua sendo a saída (acontece uma vez, no deploy deste
arquivo).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from types import ModuleType

logger = logging.getLogger(__name__)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORA_DO_PROJETO = {"site-packages", "dist-packages", ".venv", "venv"}

_MARCAS: dict[str, tuple[int, int] | None] = {}
_TRAVA = threading.Lock()


def _arquivo_do_projeto(nome: str, modulo: ModuleType | None) -> str | None:
    if nome == "__main__" or modulo is None:
        return None
    arquivo = getattr(modulo, "__file__", None)
    if not isinstance(arquivo, str):
        return None
    arquivo = os.path.abspath(arquivo)
    if os.path.commonpath([arquivo, RAIZ]) != RAIZ:
        return None
    partes = set(os.path.relpath(arquivo, RAIZ).split(os.sep))
    return None if partes & _FORA_DO_PROJETO else arquivo


def _marca(arquivo: str) -> tuple[int, int] | None:
    try:
        s = os.stat(arquivo)
    except OSError:
        return None
    return s.st_mtime_ns, s.st_size


def descartar_se_o_codigo_mudou(modulos: dict | None = None) -> list[str]:
    """Devolve os módulos cujo arquivo mudou (vazio se nenhum). Quando algum
    mudou, todos os do projeto já foram retirados de ``modulos``."""
    modulos = sys.modules if modulos is None else modulos
    with _TRAVA:
        projeto: dict[str, str] = {}
        for nome, modulo in list(modulos.items()):
            try:
                arquivo = _arquivo_do_projeto(nome, modulo)
            except ValueError:  # outro drive no Windows
                arquivo = None
            if arquivo:
                projeto[nome] = arquivo
        mudou = []
        for nome, arquivo in projeto.items():
            marca = _marca(arquivo)
            if _MARCAS.setdefault(arquivo, marca) != marca:
                mudou.append(nome)
        if not mudou:
            return []
        for nome in projeto:
            modulos.pop(nome, None)
        _MARCAS.clear()
    mudou.sort()
    logger.warning("Código mudou em disco (%s); %d módulos do projeto descartados "
                   "para reimportação.", ", ".join(mudou[:10]), len(projeto))
    return mudou

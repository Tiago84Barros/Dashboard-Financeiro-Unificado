"""📚 Documentação › Visão geral do app: o diagrama de como o app se encaixa.

A página é HTML estático em ``docs/visao_geral/como_funciona.html``. Estático
envelhece calado: tela que entra no menu e não aparece no diagrama não quebra
nada, só engana. O teste cobra o que a máquina consegue ler -- cada tela do
menu nomeada no diagrama -- e o tema entrando no iframe.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from design import visao_geral_app

RAIZ = Path(__file__).resolve().parents[1]


def _telas_do_menu() -> list[str]:
    """As chaves de ``app.py::_ROTAS``, sem o emoji da frente."""
    arvore = ast.parse((RAIZ / "app.py").read_text(encoding="utf-8"))
    rotas = next(n.value for n in arvore.body
                 if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_ROTAS")
    return [k.value.split(" ", 1)[1].strip() for k in rotas.keys]


@pytest.mark.parametrize("tema", visao_geral_app.TEMAS)
def test_tema_do_app_entra_na_raiz(tema):
    pagina = visao_geral_app.pagina_html(tema)
    assert f'<html lang="pt-BR" data-theme="{tema}">' in pagina
    assert pagina.count("<html") == 1


def test_tema_invalido_nao_vira_atributo():
    with pytest.raises(ValueError):
        visao_geral_app.pagina_html('dark" onload="x')


def test_css_le_os_dois_temas():
    """Sem o seletor, o atributo entra e a página segue o sistema operacional."""
    pagina = visao_geral_app.pagina_html("dark")
    assert ':root[data-theme="dark"]' in pagina
    assert ':root:not([data-theme="light"])' in pagina


def test_pagina_nao_busca_nada_fora():
    """O iframe roda na Streamlit Cloud: script ou folha de estilo de fora é
    ponto de falha e de rastreio que a aba não precisa."""
    pagina = visao_geral_app.pagina_html("light")
    assert not re.search(r"<script[^>]+src=", pagina)
    assert not re.search(r"<link[^>]+stylesheet", pagina)
    assert "http://" not in pagina and "https://" not in pagina


def test_diagrama_nomeia_cada_tela_do_menu():
    pagina = visao_geral_app.pagina_html("light")
    telas = _telas_do_menu()
    assert len(telas) >= 9
    faltam = [t for t in telas if t not in pagina]
    assert not faltam, f"diagrama da Visão geral sem as telas {faltam}"

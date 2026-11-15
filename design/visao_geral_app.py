"""Visão geral do app: o diagrama de como o app4 funciona, na 📚 Documentação.

A página mora pronta em ``docs/visao_geral/como_funciona.html`` -- entradas,
bastidores, onde o dado mora, as telas e as regras que valem para todas. Os
fluxogramas das outras abas explicam cada tela por dentro; este explica como
elas se encaixam.

O iframe do ``components.html`` não herda o CSS do app, então o tema entra
como ``data-theme`` na raiz do documento, igual ao Mapa do App
(``design/mapa_app.py``): o CSS da página já sabe ler o atributo, e sem ele
seguiria o tema do sistema operacional.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ARQUIVO = Path(__file__).resolve().parents[1] / "docs" / "visao_geral" / "como_funciona.html"
RAIZ = '<html lang="pt-BR">'
TEMAS = ("dark", "light")


@lru_cache(maxsize=1)
def _pagina() -> str:
    pagina = ARQUIVO.read_text(encoding="utf-8")
    if pagina.count(RAIZ) != 1:
        raise ValueError(f"como_funciona.html precisa ter {RAIZ} uma vez só")
    return pagina


def pagina_html(tema: str) -> str:
    """Documento completo para o iframe, já com o tema do app."""
    if tema not in TEMAS:
        raise ValueError(f"tema inválido: {tema!r}")
    return _pagina().replace(RAIZ, f'<html lang="pt-BR" data-theme="{tema}">')

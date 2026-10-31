"""Mapa do App: o fluxograma de todas as telas, na aba de Configurações.

A página tem duas metades, guardadas separadas em ``docs/mapa_app/``:

- ``modelo.html`` -- o desenho (estilo e script), um fragmento de ``<body>``
  com o marcador ``/*DADOS*/null`` no lugar dos dados;
- ``mapa.json`` -- as telas e as partes de cada uma, montado por
  ``scripts/montar_mapa_app.py`` a partir de ``docs/mapa_app/areas/``.

Separadas para que mudar o texto de uma tela não exija mexer no desenho, e
vice-versa. Este módulo junta as duas na hora de mostrar, porque o tema entra
junto: o iframe do ``components.html`` não herda o CSS do app, então a cor
chega por ``data-theme`` na raiz do documento -- o CSS do modelo já sabe ler o
atributo, e sem ele seguiria o tema do sistema operacional.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

PASTA = Path(__file__).resolve().parents[1] / "docs" / "mapa_app"
MARCADOR = "/*DADOS*/null"
TEMAS = ("dark", "light")


@lru_cache(maxsize=1)
def _modelo() -> str:
    modelo = (PASTA / "modelo.html").read_text(encoding="utf-8")
    if modelo.count(MARCADOR) != 1:
        raise ValueError(f"modelo.html precisa ter o marcador {MARCADOR} uma vez só")
    return modelo


@lru_cache(maxsize=1)
def _dados_embutidos() -> str:
    """O mapa.json compacto, pronto para morar dentro de ``<script>``.

    Todo ``<`` vira ``\\u003c``: o JSON.parse lê igual, e nenhum texto do mapa
    consegue fechar o ``<script>`` nem abrir comentário HTML no meio dele.
    """
    dados = json.loads((PASTA / "mapa.json").read_text(encoding="utf-8"))
    texto = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))
    return texto.replace("<", "\\u003c")


def pagina_html(tema: str) -> str:
    """Documento completo para o iframe, já com os dados e o tema do app."""
    if tema not in TEMAS:
        raise ValueError(f"tema inválido: {tema!r}")
    corpo = _modelo().replace(MARCADOR, _dados_embutidos())
    return (
        "<!doctype html>\n"
        f'<html lang="pt-BR" data-theme="{tema}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        "<style>[hidden]{display:none!important}body{margin:0}</style>\n"
        "</head>\n"
        f"<body>\n{corpo}\n</body>\n"
        "</html>\n"
    )

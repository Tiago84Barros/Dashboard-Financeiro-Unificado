"""Taxonomia de apresentação para a vitrine de FIIs.

O campo ``segmento`` dos provedores não é uma taxonomia estável: ele pode
descrever a atividade de um inquilino ou um mandato, e não a classe do fundo.
Para agrupamentos da vitrine, a categoria deriva do tipo normalizado usado pelo
motor de seleção.
"""
from __future__ import annotations


_CATEGORIAS_POR_TIPO = {
    "tijolo": "Tijolo",
    "papel": "Papel/CRI",
    "fof": "Fundo de Fundos",
    "hibrido": "Híbrido",
}


def categoria_fii(tipo: object) -> str:
    """Retorna a categoria canônica sem inventar classificação ausente."""
    chave = str(tipo or "").strip().lower()
    return _CATEGORIAS_POR_TIPO.get(chave, "Não classificado")

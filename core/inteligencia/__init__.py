"""Camada de apresentação inteligente: qualifica, orquestra e explica.

Este pacote não calcula score, não decide crise e não estima impacto. Ele
consome o que ``core.noticias``, ``core.memoria_mercado`` e
``core.eventos_extremos`` já produziram, qualifica cada número como fato,
hipótese ou estimativa, e entrega um objeto único que a tela renderiza e a LLM
explica -- sem que nenhum dos dois possa inventar um número ou mexer num score.
"""
from __future__ import annotations

INTELIGENCIA_VERSAO = "1.0.0"

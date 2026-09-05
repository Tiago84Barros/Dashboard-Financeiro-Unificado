"""Conjuntura: a porta de entrada dos motores de contexto na decisão de carteira.

Este pacote **não calcula conjuntura**. Os motores já existem e estão testados:

``core.macro_data.portfolio_context``   impacto macro por ativo, point-in-time
``core.noticias``                       relevância, sentimento e direção do item
``core.memoria_mercado.scores``         Score Conjuntural e as ações permitidas
``core.aporte``                         o plano que consome bloqueio e prioridade

O que faltava era o fio entre eles. ``core.memoria_mercado.scores.para_aporte``
devolve exatamente ``(bloqueios, prioridades)``; ``core.aporte.plano_de_aporte``
recebe exatamente ``bloqueios_conjunturais=`` e ``prioridades=`` — e a docstring
de um cita o outro pelo nome. Os dois lados estavam construídos, testados, e
desligados: ``para_aporte`` só aparecia em testes. Um motor que ninguém consulta
na decisão é decoração (``memoria: diagnostico-precisa-de-porta-de-entrada``).

Três recusas, e as três decidem o comportamento em produção hoje
---------------------------------------------------------------

1. **Ausência de notícia não é notícia neutra.** O acervo pode estar vazio, e no
   dia em que este módulo entrou ele estava — as tabelas nem existiam. Um
   componente ausente sai do denominador (``None``), nunca entra como ``0.0``.
   Zero é uma leitura: significa "o noticiário está equilibrado". Vazio não
   significa nada, e confundir os dois faz a base inteira parecer calma
   (``memoria: medicao-que-pune-a-evidencia``).

2. **Falha de leitura tem que parecer falha.** Tabela ausente levanta
   :class:`AcervoIndisponivel` e vira limitação declarada — não vira zero
   notícias. Um ``except`` que devolvesse acervo vazio publicaria "nada
   relevante aconteceu" toda vez que o banco caísse
   (``memoria: quadro-sem-coluna-passa-por-empty``).

3. **Cobertura rala não move peso.** ``scores.conjuntural`` exige
   ``COBERTURA_MINIMA`` dos componentes; abaixo disso ``avaliar`` devolve
   ``MANTER`` com fator ``1,0``. Isso não é um efeito colateral a corrigir: é o
   comportamento correto, e hoje é o comportamento real, porque só o componente
   macro tem fonte. O sistema fica ligado dizendo o que lhe falta, em vez de
   mexer em peso com um quarto da evidência.

O teto do que este pacote pode fazer
------------------------------------

Bloquear dinheiro **novo** e reordenar prioridade entre os que continuam
elegíveis. Nada aqui vende, e a garantia não é uma promessa em comentário:
``scores.ACOES_QUE_REDUZEM_POSICAO`` é um conjunto vazio com teste que falha se
alguém acrescentar algo. A proposta de ajuste sai para a tela como proposta —
nenhuma operação significativa é executada automaticamente.
"""
from __future__ import annotations

CONJUNTURA_VERSAO = "1.0.0"

from core.conjuntura.ponte import (  # noqa: E402
    AcervoIndisponivel,
    ContextoConjuntural,
    GraoIncompativel,
    LeituraNoticias,
    bloco_para_prompt,
    carregar,
    para_llm,
    para_plano_de_aporte,
)

__all__ = [
    "CONJUNTURA_VERSAO",
    "AcervoIndisponivel",
    "ContextoConjuntural",
    "GraoIncompativel",
    "LeituraNoticias",
    "bloco_para_prompt",
    "carregar",
    "para_llm",
    "para_plano_de_aporte",
]

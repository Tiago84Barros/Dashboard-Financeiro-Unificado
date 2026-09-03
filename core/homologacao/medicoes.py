"""Quem mede os critérios de avanço de fase -- e quem admite não medir.

Os critérios existiam com limiar e sem medidor. Todos saíam ``None``, a tela
escrevia *"não medido nesta instalação"* para os dez, e nenhuma fase podia
avançar por **ausência de medição** -- não por reprovação. Um critério sem
medidor não protege nada: ele só adia a decisão para fora do sistema, onde
ninguém a audita.

Este módulo é o registro dos medidores. Cada entrada é uma função sem
argumentos que devolve ``float`` quando conseguiu medir e ``None`` quando não
conseguiu. ``None`` continua sendo **não medido**, e continua não avançando
fase -- a lei do projeto vale aqui igual: ausência de medição jamais vira
aprovação silenciosa.

O que este módulo **não** faz: inventar medidor. Um critério como
``falsos_positivos_nivel_3_ou_4`` exige operação real observada, e não há
operação real observada -- a Fase 4 nunca rodou. Escrever um medidor que
devolvesse ``0.0`` por não ter encontrado nada seria o pior defeito possível
aqui: o critério "menor melhor" passaria exatamente por não ter sido testado.
Esses continuam sem medidor, e a ausência aparece na tela com o motivo.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Por que cada critério ainda não tem medidor automático. Texto de tela: o
#: usuário precisa saber se falta rodar um teste ou se falta o mundo acontecer.
SEM_MEDIDOR: dict[str, str] = {
    "falsos_positivos_nivel_3_ou_4":
        "exige operação real observada; a Fase 4 nunca rodou, e contar zero "
        "num período sem operação seria aprovar o critério por não tê-lo "
        "testado",
    "tempo_ate_rebaixar_nivel_h":
        "exige um ciclo completo de subida e rebaixamento de nível em "
        "produção; medir em simulação diria o tempo do simulador",
}


def _cenarios_historicos_reproduzidos() -> float | None:
    """Cenários que reproduzem o retorno observado no índice de referência."""
    try:
        from core import stress_tests

        return float(stress_tests.cenarios_reproduzidos())
    except Exception:  # noqa: BLE001 - medição que falha é medição ausente
        logger.exception("falha ao medir cenários históricos")
        return None


#: Nome do critério -> medidor. Só entra aqui o que é medido de verdade.
MEDIDORES: dict[str, Callable[[], float | None]] = {
    "cenarios_historicos_reproduzidos": _cenarios_historicos_reproduzidos,
}


def medir() -> dict[str, float | None]:
    """Roda os medidores disponíveis. Chave ausente é critério sem medidor.

    Devolve só o que tem medidor. ``criterios.avaliar`` já trata chave ausente
    como ``None``, e é ele quem deve decidir -- repetir a decisão aqui daria
    dois lugares para o mesmo julgamento e um dia eles divergiriam.

    Cada medidor é isolado: um que levante exceção vira ``None`` -- não medido
    -- em vez de derrubar a medição dos outros e a tela inteira junto. Medição
    que falha é medição ausente, e ausente já tem tratamento correto.
    """
    saida: dict[str, float | None] = {}
    for nome, fn in MEDIDORES.items():
        try:
            saida[nome] = fn()
        except Exception:  # noqa: BLE001 - medidor quebrado é critério não medido
            logger.exception("medidor de %s falhou", nome)
            saida[nome] = None
    return saida


def situacao(nome: str, medidas: dict[str, float | None] | None = None) -> str:
    """Frase de tela para um critério: o valor medido, ou por que não há."""
    medidas = medidas if medidas is not None else medir()
    if nome in medidas and medidas[nome] is not None:
        return f"medido: {medidas[nome]:g}"
    if nome in SEM_MEDIDOR:
        return f"não medido — {SEM_MEDIDOR[nome]}"
    if nome in MEDIDORES:
        return "não medido — o medidor existe mas falhou nesta execução"
    return "não medido — este critério ainda não tem medidor automático"

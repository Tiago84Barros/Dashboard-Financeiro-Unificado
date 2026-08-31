# -*- coding: utf-8 -*-
"""Que retorno atribuir a acao que saiu da bolsa e nao tem cotacao de saida.

O painel PIT descartava 8.287 de 29.086 linhas (28,5%) por falta de preco de
ticker morto. Descartar parece neutro e nao e: equivale a supor que a empresa
que morreu rendeu a MEDIA das que sobreviveram. Como o top-N do ranking evita
justamente as que morrem e a cesta igualitaria as carrega, a suposicao trabalha
CONTRA o excesso medido -- o numero publicado era pessimista por acidente, o que
nao o torna correto.

A saida nao e escolher um numero melhor. E parar de usar UM numero para
desfechos opostos:

  * `adquirida` -- o acionista recebeu caixa ou papel do comprador, quase sempre
    com premio. A convencao aqui e **0%**, nao o premio real: subestimar de
    proposito o desfecho bom garante que a correcao nao possa inflar o
    resultado. Se ainda assim o excesso subir, subiu apesar da convencao.
  * `sumiu` -- pedido de falencia ou recuperacao judicial. O piso e **-100%**
    (acao zerada) e o teto e a convencao da CRSP para deslistagem por
    desempenho, **-30%**, que reconhece que parte das empresas em Chapter 11
    segue negociando no balcao. As duas rodam, e a distancia entre elas E o
    resultado: numero unico esconderia que a escolha importa.
  * `indefinido` -- sem evidencia de nenhum dos dois. Continua FORA da conta.
    Empurrar o desconhecido para o lado que parece conservador ja inverteu uma
    medicao deste projeto; e aqui o desconhecido e a maioria, nao o residuo.

Ver [[saida-da-bolsa-nao-e-morte]] e [[incerteza-com-tamanho-nao-bloqueia]]:
incerteza com tamanho vira banda publicada, nao portao que trava o numero.

Modulo puro. Quem apura a causa e `core.us_saida_causa`.
"""
from __future__ import annotations

from core.us_saida_causa import ADQUIRIDA, INDEFINIDO, SUMIU

# Retorno atribuido a cada desfecho, por cenario. `None` = a linha sai da conta.
CENARIOS: dict[str, dict[str, float | None]] = {
    # Piso: acao de empresa em falencia vale zero.
    "piso": {ADQUIRIDA: 0.0, SUMIU: -1.00, INDEFINIDO: None},
    # Convencao CRSP para deslistagem por desempenho.
    "crsp": {ADQUIRIDA: 0.0, SUMIU: -0.30, INDEFINIDO: None},
    # O que o painel fazia antes: tudo fora da conta. Fica nomeado para que a
    # comparacao mostre que "descartar" tambem e uma escolha, nao a ausencia de
    # uma.
    "descartar": {ADQUIRIDA: None, SUMIU: None, INDEFINIDO: None},
}
CENARIO_PADRAO = "crsp"


def retorno_de_saida(causa: str | None,
                     cenario: str = CENARIO_PADRAO) -> float | None:
    """Retorno convencionado, ou None quando a linha deve sair da apuracao.

    Causa desconhecida (NULL no banco, causa nao prevista) devolve None pelo
    mesmo motivo que `indefinido`: nao inventamos desfecho.
    """
    tabela = CENARIOS.get(cenario)
    if tabela is None:
        raise ValueError("cenario desconhecido: {!r}".format(cenario))
    return tabela.get(str(causa or ""), None)


def frase_convencao(cenario: str = CENARIO_PADRAO) -> str:
    """Como a convencao deve aparecer na tela, derivada da propria tabela."""
    t = CENARIOS.get(cenario) or {}
    if all(v is None for v in t.values()):
        return ("As empresas que sairam da bolsa e nao tem cotação de saída ficam "
                "**fora** da apuração de retorno -- o que equivale a supor que "
                "renderam a média das sobreviventes.")
    def _pct(v: float | None) -> str:
        return "excluída" if v is None else "{:+.0f}%".format(v * 100).replace("+0%", "0%")
    return ("Saída sem cotação recebe retorno por convenção declarada: "
            "adquirida {}, falência {}, causa indefinida {}.".format(
                _pct(t.get(ADQUIRIDA)), _pct(t.get(SUMIU)), _pct(t.get(INDEFINIDO))))

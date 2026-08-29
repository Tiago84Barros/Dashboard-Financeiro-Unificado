# -*- coding: utf-8 -*-
"""Quem, no índice da SEC, pertence à população que o painel analisa (A-158).

O painel americano diz ao usuário que **70% das empresas desapareceram** entre
2010 e 2025. O número é real e foi medido com cuidado, mas responde outra
pergunta: ele sai de 9.686 CIKs que arquivaram *qualquer* relatório anual em
2010 -- e ali dentro estão trust de leasing, emissor de ABS, subsidiária de
seguradora que só arquiva por dívida registrada, fundo fechado e emissor
estrangeiro de 20-F. O painel analisa **ação operacional americana**, e nada
disso é ação.

A diferença não é acadêmica e nem é neutra no sinal: um trust de leasing termina
por desenho, no vencimento da carteira, e um veículo de ABS existe para ser
liquidado. Contar o encerramento deles como morte de empresa infla a mortalidade
que o usuário lê e, com ela, o desconto que ele aplicaria ao retorno histórico.
É o mesmo defeito de [[medir-a-fonte-que-a-decisao-le]]: o número não está
errado, está medido noutra população.

**A restrição de projeto que manda neste módulo:** o critério só pode usar campo
que a SEC continue servindo depois que a empresa morre. `tickers` e `exchanges`
vêm vazios para quem parou de arquivar -- aferido em 28/08/2026 sobre 40 CIKs
sorteados entre as 12.107 saídas: nenhum devolveu ticker. Filtrar a coorte por
"tem ticker" ou "está numa bolsa" excluiria seletivamente os mortos e produziria
uma mortalidade menor por construção, reintroduzindo o viés que a medição existe
para dimensionar. Ver [[evidencia-de-vida-e-de-morte-sao-assimetricas]]. Nome e
SIC sobrevivem à morte, e por isso são os únicos que este módulo lê.

Três estados, não dois. `None` é "não classificado", e ele não vira nem inclusão
nem exclusão: entra na conta de cobertura e sai do denominador declarado. Somar
o não apurado a qualquer dos lados seria afirmar o que ninguém apurou -- o
defeito de [[gate-que-so-dava-false]] pelo avesso.
"""
from __future__ import annotations

import re

from core.us_instrumento import motivo_exclusao_ativo
from core.us_survivorship import cik_sec_valido

# Veículos que o universo vivo nunca precisou excluir porque nunca chegaram a
# ele -- `companies` foi montada a partir de quem está listado hoje. Na coorte
# da SEC eles são numerosos, e é justamente ali que distorcem a conta.
#
# O código SIC é preferido à descrição porque a descrição varia na grafia da
# própria SEC ("Opeators of Nonresidential Buildings" tem o erro de digitação no
# cadastro oficial). O código é estável.
SIC_VEICULO = {
    "6189": "emissor de ABS: veículo de securitização, não é companhia",
    "6221": "trust de commodity ou moeda, não é ação",
    "6722": "fundo aberto, não é ação",
    "6726": "fundo fechado ou BDC, não é ação operacional",
    "6770": "companhia de cheque em branco (SPAC)",
    "6792": "trust de royalties de petróleo, não é companhia operacional",
    "6795": "trust de royalties de mineração, não é companhia operacional",
    "6798": "REIT: veículo imobiliário, fora do universo de ações",
}

MOTIVO_SEM_SIC = "SIC não informado pela SEC: instrumento não classificado"
MOTIVO_SIC_INVALIDO = "SIC inválido para a SEC: instrumento não classificado"


def _normalizar_sic_sec(sic: object) -> str | None:
    """Aceita somente código SIC SEC de quatro dígitos, sem coerção decimal."""
    if type(sic) is int:
        codigo = str(sic)
    elif isinstance(sic, str):
        codigo = sic.strip()
    else:
        return None
    # "0000" tem quatro caracteres, mas não identifica uma atividade SIC da
    # SEC. Tratá-lo como companhia faria uma lacuna de identidade parecer uma
    # classificação operacional e liberaria uma coorte que o script deve
    # bloquear.
    return codigo if re.fullmatch(r"[0-9]{4}", codigo) and codigo != "0000" else None


def _normalizar_nome_sec(nome: object) -> str:
    """Normaliza caixa e pontuação para reconhecer a mesma razão social."""
    return re.sub(r"[\W_]+", "", str(nome or "").casefold())


def _normalizar_descricao_sic(descricao: object) -> str:
    """A mesma normalização textual evita falso conflito por grafia superficial."""
    return _normalizar_nome_sec(descricao)


def classificar(nome: object = None, sic: object = None,
                sic_descricao: object = None) -> tuple[bool | None, str]:
    """(pertence ao universo do painel?, motivo auditável).

    `True` é companhia operacional; `False` é veículo identificado; `None` é não
    classificado -- e `None` é resposta legítima, não falha.

    A checagem por código vem antes da textual porque é a mais forte: a mesma
    descrição "Real Estate" cobre REIT declarado e incorporadora operacional, e
    o SIC 6500 não desempata (A-156). Aqui, ao contrário da tela, não há eleição
    fiscal apurada para desempatar -- então 6500 fica em `True` e a incerteza é
    do tamanho de uma linha, enquanto 6798, que se declara REIT, sai.
    """
    if sic is None or (isinstance(sic, str) and not sic.strip()):
        return None, MOTIVO_SEM_SIC
    codigo = _normalizar_sic_sec(sic)
    if codigo is None:
        return None, MOTIVO_SIC_INVALIDO
    if codigo in SIC_VEICULO:
        return False, SIC_VEICULO[codigo]
    # A regra do universo vivo, aplicada ao mesmo vocabulário: `companies.sector`
    # guarda a descrição SIC, que é exatamente o que `sic_descricao` traz. Rodar
    # a mesma função nas duas populações é o que torna a comparação legítima --
    # duas regras parecidas dariam dois universos e uma conta sem sentido.
    motivo = motivo_exclusao_ativo(
        None, "common", str(sic_descricao or ""), (),
        industry=None, name=str(nome or ""))
    if motivo:
        return False, motivo
    return True, "companhia operacional"


def particionar(entidades) -> dict[str, set[int]]:
    """Divide CIKs em `operacionais`, `veiculos` e `nao_classificados`.

    `entidades` é um iterável de mapeamentos com cik/nome/sic/sic_descricao --
    tipicamente as linhas de `market_us.sec_entidade`.
    """
    saida: dict[str, set[int]] = {
        "operacionais": set(), "veiculos": set(), "nao_classificados": set()}
    decisoes: dict[int, bool | None] = {}
    documentos: dict[int, tuple[str | None, str, str]] = {}
    conflitos: set[int] = set()
    for e in entidades or ():
        cik = e.get("cik")
        # CIK é identidade SEC, não um número aproximado: coerção de 1.5 para
        # 1 faria uma entidade sem identidade ocupar silenciosamente a coorte.
        # A linha inválida fica fora dos conjuntos para que o CIK do índice
        # permaneça em `sem_identidade_apurada` no chamador.
        if not cik_sec_valido(cik):
            continue
        ok, _ = classificar(e.get("nome"), e.get("sic"), e.get("sic_descricao"))
        documento = (_normalizar_sic_sec(e.get("sic")),
                     _normalizar_nome_sec(e.get("nome")),
                     _normalizar_descricao_sic(e.get("sic_descricao")))
        anterior = decisoes.get(cik)
        if cik in decisoes and (anterior != ok or documentos[cik] != documento):
            conflitos.add(cik)
        if cik in conflitos:
            # Não se pode escolher silenciosamente entre duas identidades SEC
            # incompatíveis. O CIK deixa qualquer conjunto factual e obriga o
            # chamador a interromper a medição com cobertura incompleta.
            saida["operacionais"].discard(cik)
            saida["veiculos"].discard(cik)
            saida["nao_classificados"].add(cik)
            continue
        decisoes[cik] = ok
        documentos[cik] = documento
        chave = ("nao_classificados" if ok is None
                 else "operacionais" if ok else "veiculos")
        saida[chave].add(cik)
    return saida

# -*- coding: utf-8 -*-
"""Duas regras de visibilidade point-in-time para os fundamentos EUA (A-159).

A regra que estava em produção carimba a linha inteira com
`available_at = max(filings)`: o exercício só é conhecível quando o **último**
de seus campos foi arquivado. Parece conservador, e é — mas conservador de um
jeito que depende do **futuro da empresa**.

Se um campo do exercício de 2012 só passou a ser tagueado no 10-K de 2015, a
linha de 2012 inteira fica invisível para qualquer safra anterior a 2015. Quem
continuou arquivando até hoje teve dez anos de chances de estrear uma tag nova;
quem morreu em 2013 não teve nenhuma. O efeito medido na coorte de 2012 é o
oposto do que se esperaria de uma regra conservadora: cobertura média de 36%
para quem sobreviveu contra 51% para quem sumiu — o painel enxerga **menos**
dado justamente de quem chegou até hoje.

Isso não é detalhe de parser. Muda quem tem fundamento suficiente para ser
pontuado em cada data, e portanto contamina toda safra histórica.

A regra `campo` responde à mesma pergunta sem consultar o futuro: cada campo
aparece a partir do próprio arquivamento, e o que ainda não existia na data
fica ausente — que é o que o analista daquele dia via na tela. A linha só
desaparece quando *nenhum* campo era conhecível.

Este módulo vive em `core/` e não num script porque a regra é de decisão, não
de medição: `data_pipeline.us.scoring_history` a consulta ao reconstruir as
safras. As derivações vêm de `data_pipeline.us.edgar_facts` — as mesmas que
montam a linha na ingestão. Quando havia duas cópias, a de mascaramento
derivava menos campos, e `invested_capital` e `free_cash_flow` atravessavam a
máscara carregando o valor calculado sobre o insumo invisível.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable, Iterable, Sequence

REGRA_LINHA = "linha"
REGRA_CAMPO = "campo"


def _dia(valor: Any) -> date | None:
    """Aceita date, datetime, Timestamp ou ISO; devolve `date` ou None."""
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if hasattr(valor, "date"):          # pandas.Timestamp
        try:
            return valor.date()
        except Exception:  # noqa: BLE001
            return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def filed_map(linha: dict) -> dict[str, date]:
    """`filed_at` normalizado: campo -> data de arquivamento.

    O JSONB volta do Postgres já como dict, mas volta como texto de alguns
    drivers e do SQLite dos testes. Aceitar as duas formas aqui evita que a
    regra `campo` degrade em silêncio para a regra `linha` conforme o backend.
    """
    # `_filed` e o nome que a medicao offline usava antes de a regra virar
    # producao. Cache de rodada anterior nao pode degradar em silencio para
    # a regra por linha -- seria a medicao do vies medindo o vies.
    bruto = linha.get("filed_at")
    if bruto is None:
        bruto = linha.get("_filed")
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            return {}
    if not isinstance(bruto, dict):
        return {}
    out: dict[str, date] = {}
    for campo, quando in bruto.items():
        dia = _dia(quando)
        if dia is not None:
            out[str(campo)] = dia
    return out


def visiveis(linhas: Iterable[dict], as_of: date, *,
             regra: str = REGRA_LINHA,
             derivar: Callable[[dict], None] | None = None) -> list[dict]:
    """Filtra (e, na regra `campo`, mascara) o que era conhecível em `as_of`.

    `linha`  — a linha existe inteira ou não existe (comportamento histórico).
    `campo`  — cada campo entra a partir do próprio arquivamento; o que ainda
               não fora publicado vira ausente, e ausente não é zero.

    Sem `available_at` a linha é considerada NÃO conhecível: na dúvida, ficar
    de fora é o único erro que não vira look-ahead. Sem `filed_at` a regra
    `campo` cai para a regra `linha` naquela linha específica — dado antigo,
    ingerido antes da coluna existir, não pode fingir procedência que não tem.
    """
    saida: list[dict] = []
    for linha in linhas:
        filed = filed_map(linha) if regra == REGRA_CAMPO else {}
        if not filed:
            disponivel = _dia(linha.get("available_at"))
            if disponivel is not None and disponivel <= as_of:
                saida.append(dict(linha))
            continue
        conhecidos = {c for c, dia in filed.items() if dia <= as_of}
        if not conhecidos:
            continue
        nova = {k: v for k, v in linha.items()
                if k not in filed or k in conhecidos}
        for campo in filed:
            nova.setdefault(campo, None)
        if derivar is not None:
            derivar(nova)
        saida.append(nova)
    return saida


def cobertura_por_regra(linhas: Sequence[dict], as_of: date,
                        campos: Sequence[str],
                        derivar: Callable[[dict], None] | None = None
                        ) -> dict[str, float]:
    """Fracao de `campos` preenchida sob cada regra -- o numero que mostra o vies.

    Serve para auditar a troca de regra sem reconstruir uma safra inteira: se as
    duas coberturas divergirem por empresa de um jeito que correlaciona com
    sobreviver, e a assinatura descrita no topo do modulo.
    """
    out: dict[str, float] = {}
    for regra in (REGRA_LINHA, REGRA_CAMPO):
        vis = visiveis(linhas, as_of, regra=regra, derivar=derivar)
        total = len(vis) * len(campos)
        cheios = sum(1 for r in vis for c in campos if r.get(c) is not None)
        out[regra] = (cheios / total) if total else 0.0
    return out

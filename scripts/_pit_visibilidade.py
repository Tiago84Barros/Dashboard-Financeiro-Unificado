# -*- coding: utf-8 -*-
"""Adaptador de medição para a regra point-in-time por campo (A-159).

A regra em si mudou de lugar: mora em `core.us_pit`, porque é regra de decisão
— `data_pipeline.us.scoring_history` a consulta ao reconstruir as safras. Este
módulo continua existindo porque a medição offline (`medir_mortalidade_us.py`,
`testar_score_prediz_morte_us.py`) trabalha com o `companyfacts` cru baixado da
SEC, sem passar por banco nenhum, e precisa das linhas anuais em JSON.

Enquanto a regra tinha duas implementações, a deste lado derivava menos campos
que a da ingestão: `invested_capital` e `free_cash_flow` atravessavam a máscara
com o valor calculado sobre o insumo invisível — look-ahead dentro da própria
correção de look-ahead. Agora há uma implementação só, e as derivações são
literalmente as funções que montam a linha na ingestão.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from core.us_pit import REGRA_CAMPO, REGRA_LINHA, visiveis
from data_pipeline.us.edgar_facts import DERIVADORES

__all__ = ["REGRA_CAMPO", "REGRA_LINHA", "linhas_anuais", "aplicar"]

# `linhas_anuais` devolve as três demonstrações sob estas chaves; a derivação de
# cada uma é a mesma da ingestão, buscada pelo nome da tabela correspondente.
_TABELA = {"inc": "income_statements", "bal": "balance_sheets",
           "cash": "cash_flow_statements"}


def linhas_anuais(fatos: dict) -> dict[str, list[dict]]:
    """Linhas anuais cruas, com `available_at` e `filed_at` por campo, em ISO.

    Guardar o `companyfacts` inteiro estourou a memória: há blobs de dezenas de
    MB e o processo retém centenas deles. Aqui ficam só as linhas anuais — duas
    ordens de grandeza menores — e já com a procedência por campo, sem a qual
    não dá para comparar as duas regras de visibilidade.
    """
    from data_pipeline.us.edgar_facts import (
        build_balance_rows,
        build_cashflow_rows,
        build_income_rows,
    )

    out: dict[str, list[dict]] = {}
    for nome, fn in (("inc", build_income_rows), ("bal", build_balance_rows),
                     ("cash", build_cashflow_rows)):
        out[nome] = [_serializar(linha) for linha in fn(fatos)]
    return out


def _serializar(linha: dict) -> dict:
    """Números + as datas, em ISO. `date` e hash não sobrevivem ao JSON."""
    out: dict[str, Any] = {k: v for k, v in linha.items()
                           if v is None or isinstance(v, (int, float))}
    for k in ("available_at", "reference_date"):
        v = linha.get(k)
        if v is not None:
            out[k] = v.isoformat() if hasattr(v, "isoformat") else str(v)
    out["filed_at"] = {c: str(d) for c, d in (linha.get("filed_at") or {}).items()}
    return out


def aplicar(linhas: list[dict], as_of: date, regra: str,
            demonstracao: str) -> list[dict]:
    """Aplica a regra de visibilidade e devolve as linhas como o analista via.

    `demonstracao` e `inc`, `bal` ou `cash`, e e OBRIGATORIA: ela escolhe qual
    derivacao recalcular depois da mascara. Omiti-la nao pode ser um default
    silencioso -- sem recalcular, `net_debt` e `free_cash_flow` atravessam a
    mascara com o valor calculado sobre o insumo que ainda era invisivel, que e
    exatamente o look-ahead que esta regra existe para eliminar. Errar aqui
    devolve numero plausivel, nao erro; por isso a assinatura obriga.
    """
    tabela = _TABELA.get(demonstracao)
    if tabela is None:
        raise ValueError(
            f"demonstracao invalida: {demonstracao!r}; use {sorted(_TABELA)}")
    return visiveis(linhas, as_of, regra=regra, derivar=DERIVADORES.get(tabela))

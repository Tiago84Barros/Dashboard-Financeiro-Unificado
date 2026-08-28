# -*- coding: utf-8 -*-
"""Duas regras de visibilidade point-in-time, para poder compará-las (A-159).

A regra em produção (`_build_rows`) carimba a linha inteira com
`available_at = max(filings)`: o exercício só é conhecível quando o ÚLTIMO de
seus campos foi arquivado. Parece conservador, e é -- mas conservador de um
jeito que depende do FUTURO da empresa.

Se um campo do exercício de 2012 só passou a ser tagueado no 10-K de 2015, a
linha de 2012 inteira vira invisível para qualquer safra anterior a 2015. Quem
continuou arquivando até hoje teve dez anos de chances de estrear uma tag nova;
quem morreu em 2013 não teve nenhuma. O resultado é que **o painel enxerga menos
dado justamente de quem sobreviveu** -- medido na coorte de 2012: cobertura
média 36% para sobreviventes contra 51% para as que sumiram.

Isso não é detalhe de parser: contamina toda safra histórica, porque muda quem
tem fundamento suficiente para ser pontuado em cada data.

A alternativa aqui chamada `campo` responde a mesma pergunta sem consultar o
futuro: cada campo aparece a partir do seu próprio arquivamento, e o que ainda
não existia na data fica ausente -- que é o que o analista de 2013 via na tela.
A linha só some quando NENHUM campo era conhecível.
"""
from __future__ import annotations

from datetime import date
from typing import Any

REGRA_LINHA = "linha"
REGRA_CAMPO = "campo"

# Campos que `_build_rows` acrescenta depois da coleta: nao tem arquivamento
# proprio porque derivam dos que tem, e por isso sao recalculados apos a
# mascara -- manter o derivado quando o insumo ficou invisivel publicaria um
# numero que ninguem tinha na data.


def _patch_build_rows():
    """Faz `_build_rows` anotar o arquivamento de cada campo, sem mudar o hash.

    A anotação entra DEPOIS do `content_hash`: o hash identifica os insumos
    financeiros da linha, e contaminá-lo com metadado de proveniência faria toda
    a base parecer alterada na próxima ingestão.
    """
    from data_pipeline.us import edgar_facts as ef

    if getattr(ef._build_rows, "_anota_filed", False):
        return
    original = ef._build_rows

    def _com_filed(collected: dict, symbol: str | None = None) -> list[dict]:
        linhas = original(collected, symbol)
        for linha in linhas:
            ref = linha.get("reference_date")
            chave = ref.isoformat() if hasattr(ref, "isoformat") else str(ref)
            filed: dict[str, str] = {}
            for campo, por_periodo in collected.items():
                ponto = (por_periodo or {}).get(chave)
                if ponto and ponto.get("filed"):
                    filed[campo] = str(ponto["filed"])
            linha["_filed"] = filed
        return linhas

    _com_filed._anota_filed = True
    ef._build_rows = _com_filed


def linhas_anuais(fatos: dict) -> dict[str, list[dict]]:
    """Linhas anuais cruas, com `available_at` e `_filed` por campo, em ISO."""
    from data_pipeline.us.edgar_facts import (
        build_balance_rows,
        build_cashflow_rows,
        build_income_rows,
    )

    _patch_build_rows()
    out: dict[str, list[dict]] = {}
    for nome, fn in (("inc", build_income_rows), ("bal", build_balance_rows),
                     ("cash", build_cashflow_rows)):
        out[nome] = [_serializar(linha) for linha in fn(fatos)]
    return out


def _serializar(linha: dict) -> dict:
    """Números + as duas datas, em ISO. `date` e hash não sobrevivem ao JSON."""
    out: dict[str, Any] = {k: v for k, v in linha.items()
                           if v is None or isinstance(v, (int, float))}
    for k in ("available_at", "reference_date"):
        v = linha.get(k)
        if v is not None:
            out[k] = v.isoformat() if hasattr(v, "isoformat") else str(v)
    out["_filed"] = dict(linha.get("_filed") or {})
    return out


def aplicar(linhas: list[dict], as_of: date, regra: str) -> list[dict]:
    """Aplica a regra de visibilidade e devolve as linhas como o analista via.

    `linha`: reproduz a produção -- a linha existe inteira ou não existe.
    `campo`: cada campo entra a partir do próprio arquivamento; o que ainda não
    havia sido publicado fica ausente, e ausente não é zero.
    """
    corte = as_of.isoformat()
    if regra == REGRA_LINHA:
        return [dict(r) for r in linhas
                if str(r.get("available_at") or "9999") <= corte]

    visiveis = []
    for r in linhas:
        filed = r.get("_filed") or {}
        conhecidos = {c for c, f in filed.items() if str(f) <= corte}
        if not conhecidos:
            continue
        nova = {k: v for k, v in r.items() if k not in filed or k in conhecidos}
        for campo in filed:
            nova.setdefault(campo, None)
        _recalcular_derivados(nova)
        visiveis.append(nova)
    return visiveis


def _recalcular_derivados(linha: dict) -> None:
    if "ebit" in linha:
        linha["ebit"] = linha.get("operating_income")
    if "ebitda" in linha:
        linha["ebitda"] = None
    if "total_debt" in linha:
        std, ltd = linha.get("short_term_debt"), linha.get("long_term_debt")
        linha["total_debt"] = None if std is None and ltd is None else (std or 0) + (ltd or 0)
    if "net_debt" in linha:
        caixa = linha.get("cash_and_equivalents")
        td = linha.get("total_debt")
        linha["net_debt"] = None if td is None or caixa is None else td - caixa

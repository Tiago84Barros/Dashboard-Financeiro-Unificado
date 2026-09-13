"""Como a ausencia de uma metrica se escreve e se le em ``market.*_observations``.

A tabela exige ``num_nonnulls(value_numeric, value_text, value_json) = 1``
(``supabase_unificado/schema/023_fii_methodology_v4.sql:30``). Gravar a ausencia
como tres nulos nao e uma discordancia de estilo: medido em 13/09/2026 contra o
armazem local, a linha nem chega a ser gravada -- levanta ``CheckViolation``, o
fallback linha-a-linha do repositorio re-levanta e a rodada inteira aborta.

A medida que acompanha isso e a contagem GLOBAL de linhas com os tres campos
nulos na tabela (hoje 0, inclusive depois de uma derivacao de 3204 linhas).
Nao confundir com ``empty_value_rows`` de ``fii_resilient_fallback``: aquela
consulta e escopada em ``source='cvm_informe_mensal'`` e nunca contaria estas
linhas, que vem de ``brapi_fii_v2``. Uma versao anterior deste comentario
citava ``empty_value_rows`` como justificativa -- estava errada.

Entao a ausencia ocupa o campo estruturado: ``value_json`` carrega o MOTIVO.
Isso satisfaz a constraint sem afrouxa-la (afrouxar exigiria migration nos dois
bancos, e o remoto esta fora de alcance) e mantem a ausencia observavel, com o
motivo viajando junto do fato.

O preco e que os leitores resolvem ``value_numeric -> value_text ->
value_json``: sem saber o que essa marca significa, eles devolveriam a marca
COMO VALOR -- uma string onde se espera um numero, que e pior que o defeito
original. Por isso a leitura tem um dono unico aqui, ``valor_observado``, e os
quatro leitores passam por ele. Guarda duplicada nao fica igual.

Em media renormalizada ``None`` e neutro e ``0.0`` e punitivo; a marca devolve
``None``, e o caminho de "metrica critica ausente" ja sabe lidar com ela.
"""
from __future__ import annotations

import json
from typing import Any

#: Chave que identifica a marca. Mora no `value_json` e tambem no
#: `metadata_json`, porque quem audita a tabela filtra por metadados.
CHAVE_AUSENCIA = "absence_reason"


def marca_de_ausencia(motivo: str, **evidencia: Any) -> dict:
    """O conteudo de ``value_json`` de uma observacao de ausencia."""
    return {"observado": False, CHAVE_AUSENCIA: motivo, **evidencia}


def _como_dict(value_json: Any) -> dict | None:
    if isinstance(value_json, dict):
        return value_json
    if isinstance(value_json, str) and value_json.strip().startswith("{"):
        try:
            carregado = json.loads(value_json)
        except ValueError:
            return None
        return carregado if isinstance(carregado, dict) else None
    return None


def eh_ausencia(value_json: Any) -> bool:
    """A linha declara que a metrica NAO existe?"""
    conteudo = _como_dict(value_json)
    return bool(conteudo) and CHAVE_AUSENCIA in conteudo


def motivo_da_ausencia(value_json: Any) -> str | None:
    conteudo = _como_dict(value_json)
    return str(conteudo[CHAVE_AUSENCIA]) if conteudo and CHAVE_AUSENCIA in conteudo else None


def valor_observado(obs) -> Any:
    """O valor de uma observacao: numerico, textual ou estruturado -- e
    ``None`` quando a linha e a declaracao de que nao ha valor.

    Dono unico da resolucao. Todo leitor de ``*_observations`` que caia para
    ``value_json`` precisa passar por aqui, senao entrega a marca de ausencia
    como se fosse o valor.
    """
    valor = obs.get("value_numeric")
    if valor is not None and not _nulo(valor):
        return valor
    texto = obs.get("value_text")
    if texto is not None and not _nulo(texto):
        return texto
    estruturado = obs.get("value_json")
    if _nulo(estruturado) or eh_ausencia(estruturado):
        return None
    return estruturado


def _nulo(valor: Any) -> bool:
    if valor is None:
        return True
    try:  # NaN de pandas chega aqui pelos leitores que montam quadro
        import pandas as pd

        return bool(pd.isna(valor))
    except (ImportError, TypeError, ValueError):
        return False

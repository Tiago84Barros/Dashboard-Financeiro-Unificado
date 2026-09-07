"""Defasagem entre o portfólio salvo e a metodologia corrente.

Existe para que B3 e EUA compartilhem uma única regra e um único texto. Antes
havia duas cópias da comparação e duas cópias da mensagem; a mensagem dizia
apenas "versão antiga", sem informar qual versão estava salva nem qual é a
atual, o que deixava o usuario sem saber o que tinha mudado nem quando.
"""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping


def marcar_defasagem(
    model: MutableMapping[str, Any],
    params: Mapping[str, Any],
    *,
    score_version: str,
    schema_version: int,
) -> MutableMapping[str, Any]:
    """Anota em ``model`` se o portfólio salvo ficou para trás, e por que.

    Grava ``is_stale`` (a regra, inalterada: divergiu score OU schema),
    ``stale_reasons`` (motivos legíveis) e as quatro versões envolvidas, para
    que a tela possa apresentar evidência em vez de um adjetivo.
    """
    salvo_score = params.get("score_version")
    try:
        salvo_schema = int(params.get("model_schema_version") or 0)
    except (TypeError, ValueError):
        salvo_schema = 0

    motivos: list[str] = []
    if salvo_score != score_version:
        motivos.append(
            f"metodologia de score {salvo_score or 'não registrada'} "
            f"→ {score_version}"
        )
    if salvo_schema != schema_version:
        motivos.append(
            f"schema do modelo {salvo_schema or 'não registrado'} → {schema_version}"
        )

    model["is_stale"] = bool(motivos)
    model["stale_reasons"] = motivos
    model["saved_score_version"] = salvo_score
    model["saved_model_schema_version"] = salvo_schema
    model["current_score_version"] = score_version
    model["current_model_schema_version"] = schema_version
    return model


def texto_defasagem(model: Mapping[str, Any]) -> str:
    """Mensagem de bloqueio com as versões concretas e a data do salvamento."""
    motivos = list(model.get("stale_reasons") or [])
    detalhe = "; ".join(motivos) or "versão de metodologia divergente"

    quando = model.get("created_at")
    try:
        quando_txt = f" em {quando:%d/%m/%Y}"
    except (TypeError, ValueError):
        quando_txt = ""

    return (
        f"O portfólio salvo{quando_txt} ficou para trás da metodologia atual "
        f"({detalhe}). Os pesos gravados vieram de uma versão e os critérios "
        f"desta aba são de outra, então a análise fica bloqueada em vez de "
        f"misturar as duas. Recalcule e salve a carteira na aba "
        f"**\U0001F680 Criação de Portfólio**."
    )

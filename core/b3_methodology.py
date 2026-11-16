"""Identidade da metodologia Empresas B3 compartilhada entre UI e persistência."""

SCORE_VERSION = "2.32.0"
MODEL_SCHEMA_VERSION = 3


def media_scores_comparaveis(items, chave: str = "score") -> float | None:
    """Média das notas que comparam alguém; None quando nenhuma compara.

    Ponto único da média de score da carteira B3 (Criação de Portfólio,
    Análise de Portfólio, Dashboard Geral). Fica de fora:

    - nota ausente ou não finita — contar como zero puxava a média para baixo;
    - nota marcada ``score_sem_par``: percentil de um grupo de uma empresa só,
      que sai 0,0 sem medir nada (caso WEGE3, 16/11/2026).

    Zero observado num grupo com par continua entrando.
    """
    import math

    notas: list[float] = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("score_sem_par"):
            continue
        try:
            nota = float(it.get(chave))
        except (TypeError, ValueError):
            continue
        if math.isfinite(nota):
            notas.append(nota)
    return sum(notas) / len(notas) if notas else None

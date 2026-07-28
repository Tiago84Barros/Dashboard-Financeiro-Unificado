"""Restrições determinísticas e auditáveis para carteiras.

Este módulo não depende de Streamlit. Ele concentra invariantes que precisam
ser verdadeiros em qualquer tela ou backtest:

* pesos não negativos;
* soma dos pesos igual a 1;
* peso individual menor ou igual ao cap solicitado;
* configurações matematicamente inviáveis geram erro explícito.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np


class InfeasiblePortfolioConstraint(ValueError):
    """A restrição solicitada não admite uma carteira com soma igual a 1."""


def minimum_assets_for_cap(cap: float) -> int:
    """Número mínimo de ativos necessário para respeitar ``weight <= cap``."""
    cap = float(cap)
    if not 0 < cap <= 1:
        raise ValueError("cap deve estar no intervalo (0, 1].")
    return max(1, int(math.ceil((1.0 - 1e-12) / cap)))


def validate_cap_feasibility(n_assets: int, cap: float) -> None:
    """Falha de forma explícita quando ``n_assets * cap < 1``."""
    if n_assets <= 0:
        raise InfeasiblePortfolioConstraint("A carteira precisa de ao menos um ativo.")
    required = minimum_assets_for_cap(cap)
    if n_assets < required:
        raise InfeasiblePortfolioConstraint(
            f"Cap de {cap:.1%} exige ao menos {required} ativos; "
            f"somente {n_assets} estão disponíveis."
        )


def project_capped_simplex(
    weights: Mapping[str, float],
    cap: float,
    *,
    tolerance: float = 1e-12,
) -> dict[str, float]:
    """Projeta pesos no simplex ``sum(w)=1, 0<=w<=cap``.

    A projeção euclidiana é calculada por bisseção de ``lambda`` em
    ``clip(v-lambda, 0, cap)``. Ao contrário de ``clip + normalize``, a
    renormalização nunca reintroduz pesos acima do cap.
    """
    keys = list(weights)
    if not keys:
        return {}
    validate_cap_feasibility(len(keys), cap)

    values = np.asarray(
        [max(float(weights.get(key, 0.0) or 0.0), 0.0) for key in keys],
        dtype=float,
    )
    values[~np.isfinite(values)] = 0.0
    if float(values.sum()) <= tolerance:
        values = np.full(len(keys), 1.0 / len(keys), dtype=float)
    else:
        values = values / values.sum()

    lo = float(values.min() - cap)
    hi = float(values.max())
    for _ in range(200):
        mid = (lo + hi) / 2.0
        projected = np.clip(values - mid, 0.0, cap)
        if projected.sum() > 1.0:
            lo = mid
        else:
            hi = mid
        if hi - lo <= tolerance:
            break

    result = np.clip(values - (lo + hi) / 2.0, 0.0, cap)
    residual = 1.0 - float(result.sum())
    if abs(residual) > tolerance:
        if residual > 0:
            order = np.argsort(-(cap - result))
            for idx in order:
                add = min(residual, cap - result[idx])
                result[idx] += add
                residual -= add
                if residual <= tolerance:
                    break
        else:
            order = np.argsort(-result)
            for idx in order:
                remove = min(-residual, result[idx])
                result[idx] -= remove
                residual += remove
                if residual >= -tolerance:
                    break

    if abs(float(result.sum()) - 1.0) > 1e-9:
        raise RuntimeError("Falha numérica ao projetar pesos no simplex.")
    if float(result.max()) > float(cap) + 1e-9:
        raise RuntimeError("Falha numérica: projeção excedeu o cap.")
    return {key: float(result[idx]) for idx, key in enumerate(keys)}


# ──────────────────────────────────────────────────────────────────────────
# Teto por GRUPO (setor) — a proteção que a carteira B3 não tinha
# ──────────────────────────────────────────────────────────────────────────
#
# Contexto (27/07/2026): a carteira B3 limitava só o peso por ATIVO. A
# "diversificação por segmento" elegia líderes de segmentos distintos, mas
# segmento não é setor: quatro segmentos podem viver em dois setores e num
# único fator de risco. Uma carteira real saiu 100% cíclica por esse caminho.
# FIIs (max_sector .25 + 6 dimensões) e EUA (max_sector_weight) já tinham o
# equivalente; o B3 era o único desprotegido.

def sector_cap_feasibility(group_sizes: Mapping[str, int], cap: float,
                           group_cap: float) -> tuple[bool, str]:
    """Verifica se existe carteira com ambos os tetos e soma 1.

    A capacidade de um grupo é ``min(group_cap, n_ativos_do_grupo × cap)``.
    Se a soma das capacidades não alcança 1, nenhum algoritmo resolve — e o
    honesto é dizer isso em vez de devolver pesos que violam o teto em silêncio.
    """
    if not group_sizes:
        return False, "Sem grupos para avaliar."
    capacidade = sum(min(float(group_cap), int(n) * float(cap))
                     for n in group_sizes.values())
    if capacidade >= 1.0 - 1e-9:
        return True, ""
    return False, (
        f"Teto setorial de {group_cap:.0%} com {len(group_sizes)} setor(es) "
        f"comporta no máximo {capacidade:.0%} da carteira. Reduza o número de "
        "setores exigido, amplie o teto ou inclua ativos de outros setores."
    )


def project_sector_capped(weights: Mapping[str, float],
                          groups: Mapping[str, str],
                          cap: float,
                          group_cap: float,
                          *,
                          max_iter: int = 500,
                          tolerance: float = 1e-9
                          ) -> tuple[dict[str, float], list[str]]:
    """Projeta pesos respeitando teto por ativo E por grupo (setor).

    Alterna capping de ativo e de grupo redistribuindo o excesso aos não
    saturados (water-filling) — heurística de projeção, não otimizador de
    média-variância; rotulada como tal para não superprometer.

    Args:
        weights: pesos iniciais por ativo (serão normalizados).
        groups: ativo → grupo (setor). Ativo sem grupo vira grupo próprio,
            para não criar um "setor None" artificialmente concentrado.
        cap: teto por ativo.
        group_cap: teto por grupo.

    Returns:
        ``(pesos, avisos)``. Se a configuração for inviável, devolve a melhor
        aproximação possível e um aviso explícito — nunca viola em silêncio.
    """
    keys = list(weights)
    if not keys:
        return {}, []
    validate_cap_feasibility(len(keys), cap)

    grupo_de = {k: (str(groups.get(k) or "").strip() or f"__sem_setor__{k}")
                for k in keys}
    tamanhos: dict[str, int] = {}
    for grupo in grupo_de.values():
        tamanhos[grupo] = tamanhos.get(grupo, 0) + 1

    avisos: list[str] = []
    viavel, motivo = sector_cap_feasibility(tamanhos, cap, group_cap)
    if not viavel:
        avisos.append(motivo)

    valores = np.asarray(
        [max(float(weights.get(k, 0.0) or 0.0), 0.0) for k in keys], dtype=float)
    valores[~np.isfinite(valores)] = 0.0
    valores = (np.full(len(keys), 1.0 / len(keys)) if valores.sum() <= tolerance
               else valores / valores.sum())

    indices_por_grupo = {
        grupo: np.array([i for i, k in enumerate(keys) if grupo_de[k] == grupo])
        for grupo in tamanhos
    }

    for _ in range(max_iter):
        antes = valores.copy()
        # 1) teto por ativo
        excesso = float(np.clip(valores - cap, 0.0, None).sum())
        if excesso > tolerance:
            valores = np.minimum(valores, cap)
            folga = np.where(valores < cap - tolerance, cap - valores, 0.0)
            total = float(folga.sum())
            valores = (valores + excesso * folga / total if total > tolerance
                       else valores + excesso / len(valores))
        # 2) teto por grupo
        excesso_grupo = 0.0
        for grupo, idx in indices_por_grupo.items():
            peso = float(valores[idx].sum())
            if peso > group_cap + tolerance:
                sobra = peso - group_cap
                valores[idx] *= group_cap / peso
                excesso_grupo += sobra
        if excesso_grupo > tolerance:
            folga = np.zeros(len(valores))
            for grupo, idx in indices_por_grupo.items():
                disponivel = group_cap - float(valores[idx].sum())
                if disponivel > tolerance:
                    capacidade_ativo = np.clip(cap - valores[idx], 0.0, None)
                    if capacidade_ativo.sum() > tolerance:
                        folga[idx] = capacidade_ativo
            total = float(folga.sum())
            if total > tolerance:
                valores = valores + excesso_grupo * folga / total
            else:
                break                      # nada a receber: inviável, sai
        if np.abs(valores - antes).max() <= tolerance:
            break

    soma = float(valores.sum())
    if soma > tolerance:
        valores = valores / soma

    # Relata violações remanescentes em vez de escondê-las.
    if float(valores.max()) > cap + 1e-6:
        avisos.append(f"Teto por ativo de {cap:.0%} não pôde ser respeitado.")
    for grupo, idx in indices_por_grupo.items():
        peso = float(valores[idx].sum())
        if peso > group_cap + 1e-6 and not grupo.startswith("__sem_setor__"):
            avisos.append(
                f"Setor '{grupo}' ficou com {peso:.0%} (teto {group_cap:.0%}).")
    return {k: float(valores[i]) for i, k in enumerate(keys)}, avisos


def project_class_capped(weights: Mapping[str, float],
                         is_capped_class: Mapping[str, bool],
                         cap: float,
                         class_cap: float,
                         *, tolerance: float = 1e-9
                         ) -> tuple[dict[str, float], list[str]]:
    """Limita o peso somado de UMA classe (ex.: setores cíclicos).

    Diferente de ``project_sector_capped``, aqui só a classe marcada recebe
    teto — as demais ficam livres. E, crucialmente, **recusa** em vez de
    distorcer: se os ativos de fora da classe não têm capacidade para absorver
    o excesso sem estourar o teto por ativo, os pesos originais são devolvidos
    intactos com um aviso explícito.

    Motivo (28/07/2026): a primeira versão empurrava o único ativo não-cíclico
    de uma carteira real para 36,8% — violando o teto de 35% por ativo — só
    para satisfazer o limite de fator. Trocar concentração de fator por
    concentração num único nome é piorar o risco, não reduzi-lo.
    """
    keys = list(weights)
    if not keys:
        return {}, []

    valores = np.asarray([max(float(weights.get(k, 0.0) or 0.0), 0.0) for k in keys],
                         dtype=float)
    valores[~np.isfinite(valores)] = 0.0
    soma = float(valores.sum())
    valores = (np.full(len(keys), 1.0 / len(keys)) if soma <= tolerance
               else valores / soma)
    original = {k: float(valores[i]) for i, k in enumerate(keys)}

    marcados = np.array([bool(is_capped_class.get(k, False)) for k in keys])
    peso_classe = float(valores[marcados].sum()) if marcados.any() else 0.0
    if peso_classe <= class_cap + 1e-6:
        return original, []

    livres = ~marcados
    n_livres = int(livres.sum())
    capacidade_livre = n_livres * float(cap)
    necessario = 1.0 - float(class_cap)
    if capacidade_livre < necessario - 1e-9:
        minimo = int(math.ceil(necessario / float(cap)))
        return original, [
            f"Teto de {class_cap:.0%} para a classe não pôde ser aplicado: os "
            f"{n_livres} ativo(s) fora dela comportam no máximo "
            f"{capacidade_livre:.0%} com o teto de {cap:.0%} por ativo, e seriam "
            f"necessários {necessario:.0%} (ao menos {minimo} ativos fora da "
            "classe). Os pesos foram mantidos — nenhuma distorção foi aplicada."
        ]

    # Viável: encolhe a classe para o teto e redistribui aos livres.
    ajustados = valores.copy()
    ajustados[marcados] *= class_cap / peso_classe
    excesso = 1.0 - float(ajustados.sum())
    folga = np.where(livres, np.clip(cap - ajustados, 0.0, None), 0.0)
    total_folga = float(folga.sum())
    if total_folga > tolerance:
        ajustados = ajustados + excesso * folga / total_folga
    ajustados = ajustados / float(ajustados.sum())
    return ({k: float(ajustados[i]) for i, k in enumerate(keys)}, [])

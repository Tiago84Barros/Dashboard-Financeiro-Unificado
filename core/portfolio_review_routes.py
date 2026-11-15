"""Rotas de composição para quando as metas originais não comportam carteira.

Reutilizam candidatos e diagnósticos existentes; não consultam bancos nem
mudam o resultado de aprovação dos modelos originais.
"""
from __future__ import annotations

import math

import numpy as np

from core.portfolio_best_effort import allocate_partial


def _finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def fii_review(rows, policy, scenario):
    from core.fii_lookthrough import dimension_is_applicable
    from core.fii_methodology import tactical_type_bands
    from core.fii_portfolio_v4 import LIMITS, _dimension_matrix, _teto_por_ativo
    from core.fii_renda_recorrente import dy_recorrente

    clean = {}
    for source in rows:
        score, confidence = _finite(source.get("type_score")), _finite(source.get("confidence"))
        if not source.get("ticker") or score is None or confidence is None or confidence <= 0:
            continue
        income = _finite(dy_recorrente(source))
        clean.setdefault(str(source["ticker"]), {**source,
            "quality": .45*score/100 + .30*confidence + .25*min((income or 0)/.15, 1)})
    candidates = list(clean.values())
    caps = _teto_por_ativo(candidates, policy)
    constraints = []
    unknown = set()
    for dimension, limit_attr in LIMITS.items():
        matrix, labels, _ = _dimension_matrix(candidates, dimension)
        for column in range(len(labels)):
            constraints.append((matrix[:, column], getattr(policy, limit_attr)))
        if dimension in policy.required_dimensions:
            for i, row in enumerate(candidates):
                if dimension_is_applicable(row, dimension) and not matrix[i].any():
                    caps[i] = 0
                    unknown.add(dimension)
    constraints.append((np.array([
        float((_finite(row.get("liquidez_diaria")) or 0) < policy.min_daily_liquidity)
        for row in candidates]), policy.max_illiquid))
    # Confiança média da parte investida, não diluída pelo saldo não alocado.
    constraints.append((np.array([
        1-np.clip(row["confidence"], 0, 1)-policy.max_weighted_uncertainty
        for row in candidates]), 0))
    for kind in ("tijolo", "papel", "fof", "hibrido"):
        constraints.append((np.array([float(row.get("tipo") == kind) for row in candidates]),
                            policy.max_single_type))
    result = allocate_partial(candidates, caps, upper_constraints=constraints,
        max_assets=policy.max_assets, min_weight=policy.min_asset_weight,
        reasons=["Bandas táticas por categoria tratadas como metas; tetos individuais, "
                 "concentração, liquidez e incerteza preservados.",
                 "A composição requer revisão; aprovação PIT permanece independente."])
    result["category_targets"] = {kind: {"minimum": lo, "maximum": hi,
        "actual": sum(row["weight"] for row in result["items"] if row.get("tipo") == kind)}
        for kind, (lo, hi) in tactical_type_bands(scenario).items()}
    if unknown:
        result["reasons"].append("Sem alocação em fundos com exposição obrigatória desconhecida: "
                                 + ", ".join(sorted(unknown)))
    return result


def us_review(eligible, params):
    from core.us_quality_floor import evaluate

    verdicts = evaluate(eligible)
    rows = []
    if eligible is not None and not eligible.empty:
        for source in eligible.to_dict("records"):
            ticker = str(source.get("symbol") or "").upper()
            quality = _finite(source.get("entry_score"))
            verdict = verdicts.get(ticker)
            if not ticker or quality is None or verdict is None or verdict.reprovado:
                continue
            rows.append({**source, "ticker": ticker, "quality": quality})
    constraints = []
    for column, limit in (("industry_group", params.max_industry_weight),
                          ("sector_group", params.max_sector_weight)):
        for label in sorted({str(row.get(column) or "Não classificado") for row in rows}):
            constraints.append((np.array([
                float(str(row.get(column) or "Não classificado") == label) for row in rows]), limit))
    return allocate_partial(rows, [params.max_weight]*len(rows),
        upper_constraints=constraints, max_assets=params.top_n,
        reasons=["Composição por mérito individual entre empresas elegíveis; "
                 "metas estatísticas por indústria permanecem pendentes.",
                 "Filtros individuais configurados e tetos por ativo, indústria e setor preservados."])


def _chave_segmento(segment):
    return (segment.get("setor"), segment.get("subsetor"), segment.get("segmento"))


def _margem_pct(value, reference):
    value, reference = _finite(value), _finite(reference)
    if value is None or reference is None or reference <= 0:
        return None
    return (value/reference - 1)*100


def evidencia_segmento(segment):
    """Menor margem (%) da estratégia do segmento sobre a Selic e sobre Pesos
    Iguais no histórico cheio -- os dois exames da aprovação. ``None`` quando
    o backtest não mediu nenhum dos dois."""
    margens = [_margem_pct(segment.get("val_est"), segment.get(ref))
               for ref in ("val_selic", "val_ew")]
    margens = [m for m in margens if m is not None]
    return min(margens) if margens else None


def _entrada_exclui(entry_guard, ticker):
    """Mesma exclusão do Score de Entrada que a rota normal aplica
    (``views.portfolio_b3._entry_guard_exclui``): ficha ausente não exclui."""
    entry = entry_guard.get(ticker, {}) if isinstance(entry_guard, dict) else {}
    if not entry:
        return False
    status = str(entry.get("status_entrada", "")).lower()
    return status.startswith("exclu") or (_finite(entry.get("score_entrada")) or 0.0) < 30.0


def b3_review(results, fundamentals, entry_guard, *, cap, sector_cap, cycle_cap, selic,
              vetoed=(), approved=(), barrado=None):
    """Composição para revisão quando a carteira normal da B3 não comporta os limites.

    Candidatos: a melhor empresa elegível de CADA segmento, pelo mesmo
    ``score_proximo`` que escolhe os líderes na rota normal. O score é
    percentil dentro do segmento, então nunca é comparado entre segmentos;
    o que ordena os segmentos entre si é a aprovação no backtest (``approved``)
    e, depois dela, a menor margem do segmento sobre Selic e Pesos Iguais.

    O Score de Entrada, a saúde crítica, os vetos (``vetoed``) e
    ``barrado(ticker, setor)`` (portão da Inteligência dos Ativos) só
    excluem: o vetado cede a vaga ao próximo do mesmo segmento.

    Até 14/11/2026 o conjunto era TODA empresa de todo segmento, ordenada pelo
    Score de Entrada -- um número intra-segmento que não compara setores --,
    e o ``score_proximo`` era lido só para descartar quem não o tinha.
    """
    from core.b3_holdings_health import CRITICO, check_holdings, classify_cycle

    vetoed = {str(t).upper().replace(".SA", "") for t in vetoed or ()}
    aprovados = {_chave_segmento(segment) for segment in approved or ()}
    ranked_por_segmento = []
    todos = []
    for segment in results or []:
        ranked = []
        for ticker, score in (segment.get("score_proximo") or {}).items():
            ticker = str(ticker).upper().replace(".SA", "")
            score = _finite(score)
            if score is not None:
                ranked.append((ticker, score))
        ranked.sort(key=lambda par: (-par[1], par[0]))
        ranked_por_segmento.append((segment, ranked))
        todos.extend(ticker for ticker, _ in ranked)
    todos = list(dict.fromkeys(todos))
    health = ({item.ticker: item for item in check_holdings(fundamentals, todos, selic=selic)}
              if todos else {})

    indisponiveis = []
    julgados = {}

    def _barrado(ticker, setor):
        if barrado is None:
            return False
        if ticker not in julgados:
            try:
                julgados[ticker] = bool(barrado(ticker, setor))
            except Exception as exc:  # fail-open, mas nomeado nos motivos
                indisponiveis.append(f"{ticker} ({type(exc).__name__})")
                julgados[ticker] = False
        return julgados[ticker]

    candidatos = []
    escolhidos = set()
    for segment, ranked in ranked_por_segmento:
        setor = segment.get("setor") or "Não classificado"
        for ticker, score in ranked:
            if (ticker in escolhidos or ticker in vetoed
                    or _entrada_exclui(entry_guard, ticker)
                    or ticker not in health or health[ticker].nivel == CRITICO
                    or _barrado(ticker, segment.get("setor"))):
                continue
            escolhidos.add(ticker)
            candidatos.append({
                "ticker": ticker, "setor": setor,
                "subsetor": segment.get("subsetor"),
                "segmento": segment.get("segmento"),
                "score_segmento": score,
                "lider_segmento": ticker == ranked[0][0],
                "aprovado": _chave_segmento(segment) in aprovados,
                "evidencia_segmento": evidencia_segmento(segment),
            })
            break

    candidatos.sort(key=lambda row: (
        not row["aprovado"],
        -(row["evidencia_segmento"] if row["evidencia_segmento"] is not None else -math.inf),
        -row["score_segmento"], row["ticker"]))
    # Prioridade estritamente decrescente: o otimizador preenche primeiro os
    # segmentos aprovados e, entre os demais, os de maior evidência.
    rows = [{**row, "quality": float(len(candidatos) - i)} for i, row in enumerate(candidatos)]
    constraints = []
    for sector in sorted({row["setor"] for row in rows}):
        constraints.append((np.array([float(row["setor"] == sector) for row in rows]), sector_cap))
    constraints.append((np.array([float(classify_cycle(row["setor"]) == "ciclico")
                                   for row in rows]), cycle_cap))
    reasons = [
        "Candidatas: a melhor empresa elegível de cada segmento, pelo mesmo score que escolhe "
        "os líderes na carteira normal. Scores de segmentos diferentes não são comparados.",
        "Ordem entre segmentos: primeiro os aprovados no backtest contra Selic e Pesos Iguais; "
        "depois os demais, pela menor margem sobre os dois. A aprovação estatística destes "
        "permanece pendente.",
        "Score de Entrada, Inteligência dos Ativos, vetos de risco crítico e tetos por ativo, "
        "setor e ciclo só excluem ou limitam; nenhum deles ordena.",
    ]
    if indisponiveis:
        reasons.append("Inteligência dos Ativos indisponível (não vetou): "
                       + ", ".join(indisponiveis))
    result = allocate_partial(rows, [cap]*len(rows), upper_constraints=constraints,
                              max_assets=max(len(rows), 1), reasons=reasons)
    n_aprov = sum(1 for row in result["items"] if row.get("aprovado"))
    if result["items"]:
        result["reasons"].append(
            f"{n_aprov} de {len(result['items'])} posições vêm de segmentos aprovados no backtest.")
    return result

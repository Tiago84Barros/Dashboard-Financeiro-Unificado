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


def b3_review(results, fundamentals, entry_guard, *, cap, sector_cap, cycle_cap, selic,
              vetoed=()):
    from core.b3_holdings_health import CRITICO, check_holdings, classify_cycle

    pool = {}
    for segment in results:
        for ticker, score in (segment.get("score_proximo") or {}).items():
            ticker = str(ticker).upper().replace(".SA", "")
            if ticker in vetoed:
                continue
            quality = _finite(score)
            entry = entry_guard.get(ticker, {})
            entry_score = _finite(entry.get("score_entrada"))
            if quality is None or entry_score is None:
                continue
            if str(entry.get("status_entrada", "")).lower().startswith("exclu"):
                continue
            pool.setdefault(ticker, {"ticker": ticker, "quality": entry_score,
                                    "setor": segment.get("setor") or "Não classificado"})
    health = {item.ticker: item for item in check_holdings(fundamentals, list(pool), selic=selic)}
    rows = [row for ticker, row in pool.items() if ticker in health and health[ticker].nivel != CRITICO]
    constraints = []
    for sector in sorted({row["setor"] for row in rows}):
        constraints.append((np.array([float(row["setor"] == sector) for row in rows]), sector_cap))
    constraints.append((np.array([float(classify_cycle(row["setor"]) == "ciclico")
                                   for row in rows]), cycle_cap))
    return allocate_partial(rows, [cap]*len(rows), upper_constraints=constraints,
        max_assets=max(len(rows), 1), reasons=[
            "Composição atual por qualidade individual; aprovação estatística dos segmentos permanece pendente.",
            "Vetos de risco crítico e tetos por ativo, setor e ciclo preservados."])

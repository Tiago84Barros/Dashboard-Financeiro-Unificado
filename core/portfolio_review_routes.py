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
    """Revisão parcial da carteira de FIIs quando a carteira com bandas não fecha.

    Segue a seleção da carteira normal, nunca uma conta própria:

    * a qualidade é ``utilidade_fundamental``, a mesma utilidade do otimizador
      (score no tipo, confiança, renda recorrente, evidência, perda em cenário
      adverso e incerteza). Macro, correlação e giro ficam de fora porque a
      revisão não recebe esses insumos;
    * ``type_score`` é percentil dentro do tipo e não compara tipos. Por isso
      as bandas táticas (as mesmas bandas adaptadas da carteira normal) entram
      como metas lexicográficas: depois de maximizar o capital, minimiza-se o
      desvio fora das faixas, e só então a utilidade escolhe os fundos. Sem
      isso, o percentil decidia sozinho quanto ia a cada tipo, até o teto de
      ``max_single_type``.

    Meta não é veto: faixa que não cabe vira desvio medido em
    ``category_targets``. O universo é o da tela: só elegíveis estritos, sem
    os vetados pela Inteligência dos Ativos.
    """
    from core.fii_lookthrough import dimension_is_applicable
    from core.fii_methodology import tactical_type_bands
    from core.fii_portfolio_v4 import (
        LIMITS,
        _adaptive_type_bands,
        _dimension_matrix,
        _teto_por_ativo,
        utilidade_fundamental,
    )

    clean = {}
    for source in rows:
        score, confidence = _finite(source.get("type_score")), _finite(source.get("confidence"))
        if not source.get("ticker") or score is None or confidence is None or confidence <= 0:
            continue
        clean.setdefault(str(source["ticker"]), dict(source))
    candidates = list(clean.values())
    for row, utility in zip(candidates, utilidade_fundamental(candidates, policy)):
        row["quality"] = float(utility)
    original_bands = tactical_type_bands(scenario)
    # As mesmas bandas efetivas da carteira normal: categoria sem candidato
    # tem piso zero e cede capacidade. Com menos tipos que o mínimo, a carteira
    # normal bloqueia; aqui ficam as bandas originais como meta.
    adapted_bands, _ = _adaptive_type_bands(original_bands, candidates, policy)
    bands = adapted_bands or original_bands
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
    targets = [(np.array([float(row.get("tipo") == kind) for row in candidates]), lo, hi)
               for kind, (lo, hi) in bands.items()]
    result = allocate_partial(candidates, caps, upper_constraints=constraints,
        target_bands=targets,
        max_assets=policy.max_assets, min_weight=policy.min_asset_weight,
        reasons=["Bandas táticas por categoria tratadas como metas: depois do capital, "
                 "a composição minimiza o desvio fora das faixas e só então escolhe "
                 "pela mesma utilidade da carteira normal; tetos individuais, "
                 "concentração, liquidez e incerteza preservados.",
                 "A composição requer revisão; aprovação PIT permanece independente."])
    result["category_targets"] = {kind: {"minimum": lo, "maximum": hi,
        "actual": sum(row["weight"] for row in result["items"] if row.get("tipo") == kind)}
        for kind, (lo, hi) in bands.items()}
    if unknown:
        result["reasons"].append("Sem alocação em fundos com exposição obrigatória desconhecida: "
                                 + ", ".join(sorted(unknown)))
    return result


_US_APROVADA, _US_OBSERVACAO, _US_AMOSTRA = 0, 1, 2


def _ordem_industrias_us(audit, params):
    """Fila das indústrias na revisão dos EUA, pela regra de aprovação normal.

    Camadas: aprovadas; depois as que têm a amostra mínima mas falham em algum
    critério; por último as de amostra abaixo do mínimo, onde o score do
    pipeline cai no percentil do universo e não no da indústria. Dentro da
    camada, menos critérios não atendidos vem antes e, empatado, a maior
    margem do score de entrada até o corte (o menor entre ``min_entry_score`` e
    a vantagem sobre a mediana) -- a mesma régua com que a carteira normal
    aprova. Empate final pelo nome, nunca pelo score de um líder."""
    if audit is None or audit.empty or "industry_group" not in audit:
        return []
    fila = []
    for row in audit.to_dict("records"):
        status = str(row.get("status") or "")
        count = _finite(row.get("company_count"))
        avg = _finite(row.get("avg_entry_score"))
        edge = _finite(row.get("score_edge"))
        margens = []
        falhas = 0
        if avg is not None:
            margens.append(avg - float(params.min_entry_score))
            falhas += avg < float(params.min_entry_score)
        if edge is not None:
            margens.append(edge - float(params.min_score_edge))
            falhas += edge < float(params.min_score_edge)
        if params.require_resilience:
            spread = _finite(row.get("resilience_spread_pp"))
            falhas += spread is None or spread < float(params.min_resilience_spread_pp)
        if params.require_historical_signal:
            ic = _finite(row.get("rank_ic"))
            periodos = _finite(row.get("history_periods")) or 0
            falhas += (ic is None or ic < float(params.min_rank_ic)
                       or periodos < int(params.min_history_periods))
        if status == "Aprovada":
            camada = _US_APROVADA
        elif count is not None and count < int(params.min_companies_per_industry):
            camada = _US_AMOSTRA
        else:
            camada = _US_OBSERVACAO
        fila.append({"industry": str(row["industry_group"]), "camada": camada,
                     "falhas": int(falhas),
                     "margem": min(margens) if margens else None,
                     "motivo": str(row.get("reason") or status or "")})
    fila.sort(key=lambda item: (
        item["camada"], item["falhas"],
        -(item["margem"] if item["margem"] is not None else -math.inf),
        item["industry"]))
    return fila


def _situacao_industria_us(item):
    if item["camada"] == _US_APROVADA:
        return "Indústria aprovada"
    if item["camada"] == _US_AMOSTRA:
        return "Indústria com amostra abaixo do mínimo"
    margem = item["margem"]
    texto = "Indústria em observação"
    if margem is not None:
        texto += f" · margem {margem:+.1f} pts"
    return texto


def us_review(eligible, params, audit=None, avaliador=None):
    """Composição para revisão quando a carteira normal dos EUA sai vazia.

    Segue a carteira normal, nunca uma conta própria:

    * dentro de cada indústria, os ``leaders_per_industry`` melhores pela
      MESMA função da carteira normal (``_lideres_por_industria``): score de
      entrada e fundamentalista dentro da indústria, piso de qualidade e
      portão da Inteligência dos Ativos com substituição pelo próximo da
      mesma indústria;
    * entre indústrias: havendo aprovadas (a revisão roda porque os tetos não
      fecham), exatamente as candidatas da carteira normal; sem nenhuma, a
      fila de ``_ordem_industrias_us``, pela distância até a aprovação. O
      ``entry_score`` é percentil dentro da indústria e não decide sozinho a
      vaga de uma indústria contra outra;
    * os pesos têm o mesmo alvo da carteira normal (``_base_target``),
      tratado como meta lexicográfica: depois de maximizar o capital,
      minimiza-se o desvio até o alvo, e a fila só desempata.

    Até 15/11/2026 a revisão juntava toda elegível de toda indústria, sem cota
    por indústria, e maximizava o ``entry_score``: o otimizador encostava os
    pesos no teto por ativo (cerca de 13 nomes a 8% contra 26 a 30 na
    carteira normal) e misturava escalas de percentil de indústrias
    diferentes."""
    import pandas as pd

    from core.us_portfolio_creation import (
        _base_target,
        _lideres_por_industria,
        _ordenar_por_score,
        build_industry_audit,
    )

    if eligible is None or eligible.empty:
        return allocate_partial([], [], reasons=[
            "Nenhuma empresa elegível para compor a revisão."])
    if "fundamental_score" not in eligible:
        eligible = eligible.assign(fundamental_score=np.nan)
    if audit is None or audit.empty:
        audit = build_industry_audit(eligible, params)
    fila = _ordem_industrias_us(audit, params)
    conhecidas = {item["industry"] for item in fila}
    for industry in sorted(set(eligible["industry_group"].astype(str)) - conhecidas):
        fila.append({"industry": industry, "camada": _US_AMOSTRA, "falhas": 0,
                     "margem": None, "motivo": "fora da auditoria"})
    por_industria = {item["industry"]: item for item in fila}
    # A existência de indústria aprovada sai da auditoria, ANTES da seleção:
    # decidir pelos blocos que sobreviveram ao piso deixaria uma indústria
    # reprovada impedir a rede final de restaurar as aprovadas -- e a revisão
    # sairia de indústrias que a carteira normal não usaria.
    aprovadas = [item["industry"] for item in fila if item["camada"] == _US_APROVADA]
    blocos, floor_log, intel_log = _lideres_por_industria(
        eligible, aprovadas or [item["industry"] for item in fila], params, avaliador,
        motivo="Liderança de score na indústria",
        motivo_inteligencia="Entrou pela Inteligência dos Ativos na indústria")
    top_n = max(1, int(params.top_n))
    partes = []
    if aprovadas and blocos:
        # Exatamente a lista da carteira normal (select_industry_leaders): com
        # indústria aprovada, a revisão só existe porque os tetos não fecham, e
        # o que não cabe neles fica como capital não alocado -- não vira vaga
        # para indústria que a carteira normal reprovou.
        # A mesma ordem, com o mesmo desempate pelo ticker: os blocos chegam
        # na ordem da auditoria, não na alfabética da carteira normal.
        partes.append(_ordenar_por_score(
            pd.concat(blocos, ignore_index=True)).head(top_n))
    elif not aprovadas:
        vagas = top_n
        for bloco in blocos:
            if vagas <= 0:
                break
            partes.append(bloco.head(vagas))
            vagas -= len(partes[-1])
    if not partes:
        return allocate_partial([], [], reasons=[
            "Nenhuma empresa passou no piso de qualidade e na Inteligência dos Ativos."])
    candidates = pd.concat(partes, ignore_index=True)
    alvo = _base_target(candidates, params.weighting)
    rows = []
    for i, source in enumerate(candidates.to_dict("records")):
        item = por_industria[str(source.get("industry_group"))]
        rows.append({**source, "ticker": str(source.get("symbol") or "").upper(),
                     # Posição na fila, não score: scores de indústrias
                     # diferentes não são comparáveis e aqui viram prioridade.
                     "quality": float(len(candidates) - i),
                     "aprovado": item["camada"] == _US_APROVADA,
                     "situacao_grupo": _situacao_industria_us(item)})
    constraints = []
    for column, limit in (("industry_group", params.max_industry_weight),
                          ("sector_group", params.max_sector_weight)):
        for label in sorted({str(row.get(column) or "Não classificado") for row in rows}):
            constraints.append((np.array([
                float(str(row.get(column) or "Não classificado") == label) for row in rows]), limit))
    targets = [(np.eye(len(rows))[i], float(t), float(t)) for i, t in enumerate(alvo)]
    reasons = [
        f"Candidatas: até {int(params.leaders_per_industry)} empresas por indústria, pela mesma "
        "regra da carteira normal (score dentro da indústria, piso de qualidade e Inteligência "
        "dos Ativos com substituição pelo próximo da mesma indústria).",
        "Entre indústrias: havendo aprovadas, só elas, como na carteira normal; sem nenhuma, "
        "as demais pelo menor número de critérios não atendidos e pela margem até o corte de "
        "score, e por último as de amostra abaixo do mínimo. A aprovação destas permanece pendente.",
        "Pesos: o mesmo alvo da carteira normal, sob os tetos por ativo, indústria e setor; "
        "o que não cabe fica como capital não alocado.",
    ]
    indisponiveis = intel_log.get("indisponiveis") or []
    if indisponiveis:
        reasons.append("Inteligência dos Ativos indisponível (não vetou): "
                       + ", ".join(sorted({str(item["tk"]) for item in indisponiveis})))
    if floor_log.get("carteira_preservada"):
        reasons.append("O piso de qualidade reprovaria todas as candidatas; a composição "
                       "foi remontada sem o piso para não sair vazia.")
    result = allocate_partial(rows, [params.max_weight]*len(rows),
                              upper_constraints=constraints, target_bands=targets,
                              max_assets=top_n, reasons=reasons)
    n_aprov = sum(1 for row in result["items"] if row.get("aprovado"))
    if result["items"]:
        result["reasons"].append(
            f"{n_aprov} de {len(result['items'])} posições vêm de indústrias aprovadas.")
    result["quality_floor_log"] = floor_log
    result["inteligencia_log"] = intel_log
    return result


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
    """Composição para revisão quando a rota normal da B3 não deixa nenhum líder.

    Até 16/11/2026 ela também entrava quando os líderes não fechavam nos
    limites de concentração; agora a tela fica na rota normal nesse caso
    (`views.portfolio_b3._normalizar_pesos_relaxados`).

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
                # Grupo de uma empresa só: a nota não compara ninguém.
                "score_sem_par": bool(segment.get("score_sem_par")),
                "lider_segmento": ticker == ranked[0][0],
                "aprovado": _chave_segmento(segment) in aprovados,
                "evidencia_segmento": evidencia_segmento(segment),
            })
            break

    candidatos.sort(key=lambda row: (
        not row["aprovado"],
        -(row["evidencia_segmento"] if row["evidencia_segmento"] is not None else -math.inf),
        tuple(str(parte or "") for parte in (row["setor"], row["subsetor"], row["segmento"])),
        row["ticker"]))
    # Prioridade estritamente decrescente: o otimizador preenche primeiro os
    # segmentos aprovados e, entre os demais, os de maior evidência. Empate de
    # evidência cai na chave do segmento, nunca no score: scores de segmentos
    # diferentes não são comparáveis e aqui viram prioridade de capital.
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

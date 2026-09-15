"""Task 8 Step 6: carteira de diligencia nos dois regimes extremos.

Selic alta (high_real_rate) e queda de juros (easing, piso de tijolo em 40%).
Confere 12 ativos, banda de tipo, constraint_violations vazia e reporta
protecao_excedida e viability_notes. Nada e persistido.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    from core import market_read
    from core.config import settings
    from core.database import get_engine, get_session_factory
    from core.fii_carteira_protegida import montar_carteira_com_concessao
    from core.fii_integrated_model import (
        IntegratedEligibilityPolicy,
        apply_integrated_eligibility,
    )
    from core.fii_methodology import (
        MacroScenario,
        classify_macro_regime,
        score_fiis_by_type,
        tactical_type_bands,
    )
    from core.fii_portfolio_v4 import (
        LIVE_PORTFOLIO_STRATEGY_ID,
        PortfolioPolicy,
        optimize_diligence_portfolio,
    )
    from core.fii_validation import validation_supports_strategy
    from scripts.publish_fii_selection_from_local import _warehouse_url

    local_url = _warehouse_url()
    settings.SUPABASE_UNIFICADO_URL = local_url
    settings.DATABASE_URL = local_url
    settings.SUPABASE_DB_URL = local_url
    get_engine.clear()
    get_session_factory.clear()
    market_read._engine.clear()

    from dataclasses import replace
    politica = IntegratedEligibilityPolicy()
    piso = os.environ.get("PISO_RENDA")
    if piso:
        politica = replace(politica, min_recurrent_dy_12m=float(piso))
        print("piso de renda recorrente sobrescrito para", politica.min_recurrent_dy_12m)
    prefer = os.environ.get("USA_SNAPSHOT") == "1"
    inputs = market_read.load_fii_methodology_inputs(prefer_snapshot=prefer)
    eligible, eligibility = apply_integrated_eligibility(
        inputs.to_dict("records") if not inputs.empty else [],
        politica,
    )
    print(f"universo={eligibility['universe_count']} elegiveis={eligibility['eligible_count']}")
    print("composicao elegivel:", dict(Counter(
        str(row.get("tipo")) for row in eligible).most_common()))
    print("razoes de exclusao:", json.dumps(
        eligibility["exclusion_counts"], ensure_ascii=False, indent=2))

    validation = market_read.load_fii_validation_status()
    validation_status = ("passed" if validation_supports_strategy(
        validation, LIVE_PORTFOLIO_STRATEGY_ID) else "unvalidated")
    print("validation_status:", validation_status,
          "| safra:", validation.get("methodology_version"))
    # Pontuacao e relativa ao universo: o orquestrador repontua cada
    # tentativa. Aqui so registramos o tamanho do universo estrito.
    print("pontuados no universo estrito:", len(
        score_fiis_by_type(eligible, validation_status=validation_status)))

    def correlacao_do_pool(pontuadas: list[dict]) -> dict:
        candidates: list[str] = []
        for fii_type in ("tijolo", "papel", "fof", "hibrido"):
            candidates.extend([str(row["ticker"]) for row in pontuadas
                               if row.get("tipo") == fii_type][:12])
        candidates = list(dict.fromkeys(candidates))
        prices = market_read.load_precos_mensais(tuple(sorted(candidates)))
        returns = prices.pct_change(fill_method=None) if not prices.empty else prices
        usable = [t for t in candidates
                  if t in getattr(returns, "columns", [])
                  and int(returns[t].notna().sum()) >= 12]
        return {"correlation_matrix": (returns[usable].corr(min_periods=12).to_dict()
                                      if len(usable) >= 2 else None),
                "correlation_penalty": .12}

    cenarios = {
        "selic_alta": MacroScenario(selic=15.0, ipca=4.5, selic_change_12m=0.0,
                                    vacancy_shock=.08, credit_event_rate=.03),
        "easing": MacroScenario(selic=10.0, ipca=4.5, selic_change_12m=-3.0,
                                vacancy_shock=.08, credit_event_rate=.03),
    }
    falhas: list[str] = []
    for nome, cenario in cenarios.items():
        regime = classify_macro_regime(cenario)
        bandas = tactical_type_bands(cenario)
        resultado = montar_carteira_com_concessao(
            eligible, eligibility.get("concession_candidates") or (), cenario,
            policy=PortfolioPolicy(),
            score=lambda linhas: score_fiis_by_type(
                linhas, validation_status=validation_status),
            optimizer_kwargs=correlacao_do_pool,
            optimizer=optimize_diligence_portfolio,
        )
        itens = resultado.get("items") or []
        pesos_por_tipo: dict[str, float] = {}
        for item in itens:
            tipo = str(item.get("tipo"))
            pesos_por_tipo[tipo] = pesos_por_tipo.get(tipo, 0.0) + float(item.get("weight") or 0)
        print(f"\n=== {nome} (regime={regime}) ===")
        print("status:", resultado.get("status"), "| ativos:", len(itens))
        print("pesos por tipo:", {k: round(v, 4) for k, v in sorted(pesos_por_tipo.items())})
        print("bandas do regime:", {k: v for k, v in bandas.items()})
        print("constraint_violations:", resultado.get("constraint_violations") or [])
        print("protecao_excedida:", resultado.get("protecao_excedida") or [])
        print("viability_notes:", resultado.get("viability_notes")
              or (resultado.get("fallback") or {}).get("viability_notes") or [])
        print("concessao:", resultado.get("concessao_de_elegibilidade") or {})
        print("protecao cedida na elegibilidade:",
              resultado.get("protecao_cedida_na_elegibilidade") or [])
        print("blockers:", resultado.get("blockers") or [])
        print("maior peso:", max((round(float(i.get("weight") or 0), 4) for i in itens), default=None))
        print("tickers:", [i.get("ticker") for i in itens])
        if not itens:
            falhas.append(f"{nome}: carteira vazia")
        # Medicao de 12/09/2026: sob o universo da propria main (26 elegiveis)
        # e este mesmo otimizador, easing devolve 11 ativos, nao 12 — a
        # cardinalidade e fixada pelo teto de peso por ativo (com teto .09 o
        # mesmo pool devolve 12), nao pelo tamanho do universo. O criterio
        # continua em 12 de proposito: baixar o criterio para o observado
        # esconderia a diferenca.
        if len(itens) != 12:
            falhas.append(f"{nome}: {len(itens)} ativos em vez de 12")
        if resultado.get("constraint_violations"):
            falhas.append(f"{nome}: constraint_violations nao vazia")
        for tipo, (piso, teto) in bandas.items():
            peso = pesos_por_tipo.get(tipo, 0.0)
            if peso and not (piso - 1e-6 <= peso <= teto + 1e-6):
                falhas.append(f"{nome}: {tipo} em {peso:.4f} fora da banda [{piso},{teto}]")

    print("\n=== VEREDITO ===")
    print("OK" if not falhas else "FALHAS: " + "; ".join(falhas))
    return 0 if not falhas else 1


if __name__ == "__main__":
    raise SystemExit(main())

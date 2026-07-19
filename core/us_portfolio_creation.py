"""Motor institucional da aba Criação de Portfólio — Empresas Americanas.

O módulo mantém regras de negócio fora da interface Streamlit. A seleção ocorre
em quatro camadas: elegibilidade, score de entrada, aprovação por indústria e
otimização com limites por ativo/indústria/setor. O histórico ponto-no-tempo é
opcional porque a vitrine publicada pode conter apenas o snapshot SEC/GAAP.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from typing import Any

import numpy as np
import pandas as pd

from core.market_companies import translate_us_sector
from core.us_advanced_lab import build_entry_scores


PORTFOLIO_SCHEMA_VERSION = "us_portfolio_creation_v2"


@dataclass(frozen=True)
class USPortfolioCreationParams:
    """Parâmetros transparentes da aplicação em escala no universo americano."""

    benchmark: str = "S&P 500 (SPY)"
    risk_free_rate: float = 0.0425
    capital_usd: float = 10_000.0
    monthly_contribution_usd: float = 500.0
    top_n: int = 30
    leaders_per_industry: int = 2
    min_market_cap: float = 1_000_000_000.0
    min_coverage: float = 50.0
    min_years: int = 5
    min_companies_per_industry: int = 4
    min_fundamental_score: float = 45.0
    min_entry_score: float = 55.0
    min_score_edge: float = 2.0
    require_resilience: bool = False
    min_resilience_spread_pp: float = 0.0
    max_weight: float = 0.08
    max_industry_weight: float = 0.15
    max_sector_weight: float = 0.25
    weighting: str = "score"
    adaptive_caps: bool = True
    require_historical_signal: bool = False
    min_rank_ic: float = 0.0
    min_history_periods: int = 3


def params_to_dict(params: USPortfolioCreationParams) -> dict[str, Any]:
    return {"schema_version": PORTFOLIO_SCHEMA_VERSION, **asdict(params)}


def _numeric(frame: pd.DataFrame, column: str, default=np.nan) -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _macro_sector(row: pd.Series) -> str:
    value = translate_us_sector(row.get("sector"), row.get("industry"))
    return str(value or "Não classificado")


def _safe_median(values: pd.Series) -> float:
    clean = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    return float(clean.median()) if not clean.empty else np.nan


def _sequential_filter(
    frame: pd.DataFrame,
    mask: pd.Series,
    key: str,
    label: str,
    exclusions: list[dict[str, Any]],
) -> pd.DataFrame:
    clean_mask = mask.reindex(frame.index).fillna(False).astype(bool)
    removed = int((~clean_mask).sum())
    exclusions.append({"key": key, "label": label, "count": removed})
    return frame.loc[clean_mask].copy()


def prepare_eligible_universe(
    scored: pd.DataFrame,
    params: USPortfolioCreationParams,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aplica filtros sequenciais e devolve universo elegível + reconciliação.

    A contagem sequencial evita dupla contagem: a soma dos removidos e dos
    elegíveis reconcilia exatamente com o universo recebido.
    """
    columns = ["key", "label", "count"]
    if scored is None or scored.empty:
        return pd.DataFrame(), pd.DataFrame(columns=columns)

    work = scored.copy().reset_index(drop=True)
    if "symbol" not in work:
        return pd.DataFrame(), pd.DataFrame(columns=columns)
    work["symbol"] = work["symbol"].astype(str).str.strip().str.upper()
    work = work.drop_duplicates("symbol", keep="first")
    total = len(work)
    exclusions: list[dict[str, Any]] = []

    if "is_active" in work:
        work = _sequential_filter(
            work, work["is_active"].fillna(True).astype(bool), "inactive",
            "Inativas ou deslistadas", exclusions,
        )

    if "exchange" in work:
        exchange = work["exchange"].fillna("").astype(str).str.upper()
        listed = exchange.str.contains("NYSE|NASDAQ|AMEX|NYSE AMERICAN", regex=True)
        work = _sequential_filter(
            work, listed, "exchange", "Fora de NYSE, Nasdaq ou NYSE American", exclusions,
        )

    market_cap = _numeric(work, "_market_cap")
    work = _sequential_filter(
        work, market_cap >= float(params.min_market_cap), "market_cap",
        "Valor de mercado ausente ou abaixo do piso", exclusions,
    )

    coverage = _numeric(work, "coverage", 0.0)
    work = _sequential_filter(
        work, coverage >= float(params.min_coverage), "coverage",
        "Cobertura fundamentalista abaixo do mínimo", exclusions,
    )

    years = _numeric(work, "_years", 0.0)
    work = _sequential_filter(
        work, years >= int(params.min_years), "history",
        "Histórico SEC/GAAP abaixo do mínimo", exclusions,
    )

    score = _numeric(work, "score")
    work = _sequential_filter(
        work, score >= float(params.min_fundamental_score), "score",
        "Score fundamentalista abaixo do piso", exclusions,
    )

    if work.empty:
        audit = pd.DataFrame(exclusions, columns=columns)
        audit.attrs["total"] = total
        audit.attrs["eligible"] = 0
        return work, audit

    work["sector_raw"] = work.get("sector", "Não classificado")
    work["sector_group"] = work.apply(_macro_sector, axis=1)
    work["industry_group"] = work.get("industry", work.get("sector", "Não classificado"))
    work["industry_group"] = work["industry_group"].fillna("Não classificado").astype(str)
    work = build_entry_scores(work)
    work["fundamental_score"] = _numeric(work, "score")
    work["score"] = _numeric(work, "entry_score")

    audit = pd.DataFrame(exclusions, columns=columns)
    audit.attrs["total"] = total
    audit.attrs["eligible"] = len(work)
    return work.reset_index(drop=True), audit


def _historical_industry_signals(
    panel: pd.DataFrame | None,
    symbol_industry: pd.DataFrame,
    min_group: int,
) -> pd.DataFrame:
    columns = ["industry_group", "rank_ic", "history_periods", "history_hit_rate"]
    if panel is None or panel.empty:
        return pd.DataFrame(columns=columns)
    required = {"date", "symbol", "score", "fwd_return"}
    if not required.issubset(panel.columns):
        return pd.DataFrame(columns=columns)

    from core.us_backtest import rank_ic

    p = panel[list(required)].copy()
    p["symbol"] = p["symbol"].astype(str).str.upper()
    p = p.merge(symbol_industry.drop_duplicates("symbol"), on="symbol", how="inner")
    records: list[dict[str, Any]] = []
    for industry, group in p.groupby("industry_group", dropna=False):
        values = []
        for _, vintage in group.groupby("date"):
            if len(vintage) < min_group:
                continue
            value = rank_ic(vintage["score"], vintage["fwd_return"])
            if value is not None and np.isfinite(value):
                values.append(float(value))
        records.append({
            "industry_group": industry,
            "rank_ic": float(np.mean(values)) if values else np.nan,
            "history_periods": len(values),
            "history_hit_rate": float(np.mean(np.asarray(values) > 0)) if values else np.nan,
        })
    return pd.DataFrame(records, columns=columns)


def build_industry_audit(
    eligible: pd.DataFrame,
    params: USPortfolioCreationParams,
    score_panel: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Classifica indústrias sem confundir evidência atual com backtest PIT."""
    if eligible is None or eligible.empty:
        return pd.DataFrame()

    global_median = float(_numeric(eligible, "entry_score").median())
    historical = _historical_industry_signals(
        score_panel,
        eligible[["symbol", "industry_group"]],
        params.min_companies_per_industry,
    )
    history_map = historical.set_index("industry_group").to_dict("index") \
        if not historical.empty else {}
    records: list[dict[str, Any]] = []

    for industry, group in eligible.groupby("industry_group", dropna=False):
        ranked = group.sort_values(["entry_score", "fundamental_score"], ascending=False)
        leaders = ranked.head(max(1, int(params.leaders_per_industry)))
        sector = str(ranked["sector_group"].mode().iloc[0])
        avg_entry = float(_numeric(leaders, "entry_score").mean())
        avg_fundamental = float(_numeric(leaders, "fundamental_score").mean())
        avg_coverage = float(_numeric(leaders, "coverage").mean())
        score_edge = avg_entry - global_median

        roic = _safe_median(_numeric(leaders, "roic"))
        roe = _safe_median(_numeric(leaders, "roe"))
        fcf_yield = _safe_median(_numeric(leaders, "fcf_yield"))
        if sector == "Serviços Financeiros":
            resilience_metric = "ROE − Treasury"
            resilience_value = roe
        elif sector == "Imobiliário":
            resilience_metric = "FCF yield − Treasury"
            resilience_value = fcf_yield
        else:
            resilience_metric = "ROIC − Treasury"
            resilience_value = roic
        resilience_spread = (
            (float(resilience_value) - params.risk_free_rate) * 100
            if pd.notna(resilience_value) else np.nan
        )

        enough_companies = len(group) >= int(params.min_companies_per_industry)
        score_pass = avg_entry >= float(params.min_entry_score)
        edge_pass = score_edge >= float(params.min_score_edge)
        resilience_pass = (
            not params.require_resilience
            or (np.isfinite(resilience_spread)
                and resilience_spread >= float(params.min_resilience_spread_pp))
        )
        h = history_map.get(industry, {})
        historical_available = int(h.get("history_periods", 0) or 0) > 0
        historical_pass = (
            not params.require_historical_signal
            or (
                int(h.get("history_periods", 0) or 0) >= int(params.min_history_periods)
                and pd.notna(h.get("rank_ic"))
                and float(h["rank_ic"]) >= float(params.min_rank_ic)
            )
        )
        approved = enough_companies and score_pass and edge_pass and resilience_pass and historical_pass
        if not enough_companies:
            status = "Excluída"
            reason = "Amostra insuficiente"
        elif params.require_historical_signal and not historical_available:
            status = "Excluída"
            reason = "Histórico PIT indisponível"
        elif approved:
            status = "Aprovada"
            reason = "Score, vantagem relativa e resiliência atendidos"
        else:
            status = "Observação"
            failed = []
            if not score_pass:
                failed.append("score de entrada")
            if not edge_pass:
                failed.append("vantagem relativa")
            if not resilience_pass:
                failed.append("resiliência")
            if not historical_pass:
                failed.append("sinal histórico")
            reason = "Abaixo do mínimo em " + ", ".join(failed)

        records.append({
            "industry_group": str(industry),
            "sector_group": sector,
            "status": status,
            "reason": reason,
            "company_count": int(len(group)),
            "leader_count": int(len(leaders)),
            "leaders": ", ".join(leaders["symbol"].astype(str)),
            "avg_entry_score": avg_entry,
            "avg_fundamental_score": avg_fundamental,
            "score_edge": score_edge,
            "avg_coverage": avg_coverage,
            "resilience_metric": resilience_metric,
            "resilience_spread_pp": resilience_spread,
            "rank_ic": h.get("rank_ic", np.nan),
            "history_periods": int(h.get("history_periods", 0) or 0),
            "history_hit_rate": h.get("history_hit_rate", np.nan),
        })

    order = pd.CategoricalDtype(["Aprovada", "Observação", "Excluída"], ordered=True)
    audit = pd.DataFrame(records)
    audit["_status_order"] = audit["status"].astype(order)
    return audit.sort_values(
        ["_status_order", "avg_entry_score"], ascending=[True, False]
    ).drop(columns="_status_order").reset_index(drop=True)


def select_industry_leaders(
    eligible: pd.DataFrame,
    industry_audit: pd.DataFrame,
    params: USPortfolioCreationParams,
) -> pd.DataFrame:
    """Seleciona finalistas somente dentro das indústrias aprovadas."""
    if eligible is None or eligible.empty or industry_audit is None or industry_audit.empty:
        return pd.DataFrame()
    approved = set(industry_audit.loc[
        industry_audit["status"].eq("Aprovada"), "industry_group"
    ].astype(str))
    if not approved:
        return pd.DataFrame()

    records = []
    for industry, group in eligible[eligible["industry_group"].isin(approved)].groupby(
        "industry_group", dropna=False
    ):
        leaders = group.sort_values(
            ["entry_score", "fundamental_score"], ascending=False
        ).head(max(1, int(params.leaders_per_industry))).copy()
        leaders["selection_reason"] = "Liderança de score na indústria aprovada"
        records.append(leaders)
    if not records:
        return pd.DataFrame()
    candidates = pd.concat(records, ignore_index=True)
    return candidates.sort_values(
        ["entry_score", "fundamental_score"], ascending=False
    ).head(int(params.top_n)).reset_index(drop=True)


def _base_target(candidates: pd.DataFrame, weighting: str) -> np.ndarray:
    n = len(candidates)
    if n == 0:
        return np.asarray([], dtype=float)
    if weighting == "equal":
        return np.full(n, 1.0 / n)
    if weighting == "inverse_vol" and "volatility" in candidates:
        vol = _numeric(candidates, "volatility")
        if vol.notna().any():
            inv = 1.0 / vol.fillna(vol.median()).clip(lower=1e-6)
            return (inv / inv.sum()).to_numpy(dtype=float)
    scores = _numeric(candidates, "entry_score", 50.0).fillna(50.0)
    base = (scores - scores.min()) + 1.0
    return (base / base.sum()).to_numpy(dtype=float)


def _effective_caps(
    candidates: pd.DataFrame,
    params: USPortfolioCreationParams,
) -> tuple[dict[str, float], list[str]]:
    n = len(candidates)
    requested = {
        "position": float(params.max_weight),
        "sector": float(params.max_sector_weight),
        "industry": float(params.max_industry_weight),
    }
    cap_labels = {"position": "ativo", "sector": "setor", "industry": "indústria"}
    warnings: list[str] = []
    effective = dict(requested)

    position_minimum = 1 / n
    if requested["position"] + 1e-12 < position_minimum:
        if params.adaptive_caps:
            effective["position"] = position_minimum
            warnings.append(
                f"Teto por ativo ajustado de {requested['position']:.1%} para "
                f"{position_minimum:.1%} para manter viabilidade."
            )
        else:
            warnings.append(
                f"Teto por ativo inviável: mínimo matemático {position_minimum:.1%}."
            )

    def minimum_group_cap(column: str) -> float:
        counts = candidates.groupby(column, dropna=False).size().to_numpy(dtype=float)
        position_cap = effective["position"]
        lo, hi = 0.0, 1.0
        for _ in range(80):
            mid = (lo + hi) / 2
            capacity = np.minimum(counts * position_cap, mid).sum()
            if capacity >= 1.0 - 1e-12:
                hi = mid
            else:
                lo = mid
        return hi

    minimum = {
        "sector": minimum_group_cap("sector_group"),
        "industry": minimum_group_cap("industry_group"),
    }
    for key in ("sector", "industry"):
        if requested[key] + 1e-12 < minimum[key]:
            if params.adaptive_caps:
                effective[key] = minimum[key]
                warnings.append(
                    f"Teto por {cap_labels[key]} ajustado de {requested[key]:.1%} para "
                    f"{minimum[key]:.1%} para manter viabilidade."
                )
            else:
                warnings.append(
                    f"Teto por {cap_labels[key]} inviável: mínimo matemático "
                    f"{minimum[key]:.1%}."
                )
    return effective, warnings


def allocate_weights(
    candidates: pd.DataFrame,
    params: USPortfolioCreationParams,
) -> tuple[pd.DataFrame, list[str]]:
    """Projeta pesos próximos ao alvo sob restrições lineares via SLSQP."""
    if candidates is None or candidates.empty:
        return pd.DataFrame(), []
    candidates = candidates.copy().reset_index(drop=True)
    caps, warnings = _effective_caps(candidates, params)
    if not params.adaptive_caps and any("inviável" in item for item in warnings):
        return pd.DataFrame(), warnings

    target = _base_target(candidates, params.weighting)
    n = len(candidates)
    constraints: list[dict[str, Any]] = [{
        "type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)
    }]
    for column, cap_key in (("sector_group", "sector"), ("industry_group", "industry")):
        for value in candidates[column].dropna().unique():
            mask = candidates[column].eq(value).to_numpy(dtype=float)
            cap = caps[cap_key]
            constraints.append({
                "type": "ineq",
                "fun": lambda w, m=mask, c=cap: float(c - np.dot(w, m)),
            })

    try:
        from scipy.optimize import linprog, minimize

        group_constraints = constraints[1:]
        a_ub = []
        b_ub = []
        for item in group_constraints:
            # A função é cap - dot(w, mask); recupera a máscara avaliando a
            # variação em cada base canônica, sem duplicar a montagem acima.
            at_zero = float(item["fun"](np.zeros(n)))
            mask = np.asarray([
                at_zero - float(item["fun"](np.eye(n)[i])) for i in range(n)
            ])
            a_ub.append(mask)
            b_ub.append(at_zero)
        feasible = linprog(
            np.zeros(n), A_ub=np.asarray(a_ub), b_ub=np.asarray(b_ub),
            A_eq=np.ones((1, n)), b_eq=np.asarray([1.0]),
            bounds=[(0.0, caps["position"])] * n, method="highs",
        )
        if not feasible.success:
            warnings.append(f"Restrições incompatíveis: {feasible.message}")
            return pd.DataFrame(), warnings

        result = minimize(
            lambda w: float(np.sum((w - target) ** 2)),
            x0=feasible.x,
            method="SLSQP",
            bounds=[(0.0, caps["position"])] * n,
            constraints=constraints,
            options={"maxiter": 1_000, "ftol": 1e-12},
        )
        if not result.success or not np.isfinite(result.x).all():
            warnings.append(
                f"Projeção por score não convergiu; usados pesos viáveis do "
                f"solver linear ({result.message})."
            )
            weights = np.maximum(feasible.x, 0.0)
        else:
            weights = np.maximum(result.x, 0.0)
        weights = weights / weights.sum()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Otimização indisponível: {exc}")
        return pd.DataFrame(), warnings

    candidates["weight"] = weights
    candidates["allocation_usd"] = weights * float(params.capital_usd)
    candidates["monthly_contribution_usd"] = weights * float(params.monthly_contribution_usd)
    candidates.attrs["effective_caps"] = caps
    return candidates.sort_values("weight", ascending=False).reset_index(drop=True), warnings


def _weighted_mean(frame: pd.DataFrame, column: str) -> float | None:
    values = _numeric(frame, column)
    valid = values.notna() & _numeric(frame, "weight", 0.0).gt(0)
    if not valid.any():
        return None
    weights = _numeric(frame.loc[valid], "weight", 0.0)
    return float(np.average(values.loc[valid], weights=weights))


def portfolio_metrics(holdings: pd.DataFrame, params: USPortfolioCreationParams) -> dict[str, Any]:
    if holdings is None or holdings.empty:
        return {}
    w = _numeric(holdings, "weight", 0.0)
    return {
        "assets": int(len(holdings)),
        "sectors": int(holdings["sector_group"].nunique()),
        "industries": int(holdings["industry_group"].nunique()),
        "effective_assets": float(1.0 / np.square(w).sum()),
        "max_weight": float(w.max()),
        "entry_score": _weighted_mean(holdings, "entry_score"),
        "fundamental_score": _weighted_mean(holdings, "fundamental_score"),
        "coverage": _weighted_mean(holdings, "coverage"),
        "roic": _weighted_mean(holdings, "roic"),
        "market_cap": _weighted_mean(holdings, "_market_cap"),
        "capital_usd": float(params.capital_usd),
        "monthly_contribution_usd": float(params.monthly_contribution_usd),
        "benchmark": params.benchmark,
        "risk_free_rate": float(params.risk_free_rate),
    }


def build_portfolio_creation(
    scored: pd.DataFrame,
    params: USPortfolioCreationParams | None = None,
    score_panel: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Executa a criação completa e retorna payload pronto para a interface."""
    params = params or USPortfolioCreationParams()
    eligible, exclusions = prepare_eligible_universe(scored, params)
    audit = build_industry_audit(eligible, params, score_panel)
    candidates = select_industry_leaders(eligible, audit, params)
    holdings, warnings = allocate_weights(candidates, params)
    history_available = score_panel is not None and not score_panel.empty
    return {
        "ok": not holdings.empty,
        "params": params_to_dict(params),
        "universe_count": int(exclusions.attrs.get("total", len(scored) if scored is not None else 0)),
        "eligible_count": int(len(eligible)),
        "eligible": eligible,
        "exclusions": exclusions,
        "industry_audit": audit,
        "candidates": candidates,
        "holdings": holdings,
        "metrics": portfolio_metrics(holdings, params),
        "warnings": warnings,
        "history_available": bool(history_available),
        "history_required_unavailable": bool(params.require_historical_signal and not history_available),
        "minimum_assets_for_position_cap": int(ceil(1 / max(params.max_weight, 1e-9))),
    }

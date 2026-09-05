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
from core.us_liquidity import VERSION as US_LIQUIDITY_VERSION

# v3 (2026-08-17): ``params`` passou a carregar `liquidity_version`, porque a
# regra de elegibilidade por negociabilidade mudou de comportamento e uma
# carteira salva precisa dizer sob qual regra nasceu. Chave ADITIVA: carteiras
# gravadas com v2 continuam legíveis (a leitura usa .get) e não são migradas.
PORTFOLIO_SCHEMA_VERSION = "us_portfolio_creation_v3"


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
    # Volume financeiro mediano por pregão. Valor de mercado alto não garante
    # que o papel negocie: ADR, spin-off recente e ação encalhada aparecem
    # grandes e saem caro. Ver core.us_liquidity para a calibragem do piso.
    min_daily_turnover_usd: float = 1_000_000.0
    # Piso absoluto de qualidade: reprova quem o laboratório avançado marcou
    # como "Excluída" e chama o próximo da MESMA indústria. Ver
    # core.us_quality_floor para por que ele não define limiares próprios.
    apply_quality_floor: bool = True


def params_to_dict(params: USPortfolioCreationParams) -> dict[str, Any]:
    return {
        "schema_version": PORTFOLIO_SCHEMA_VERSION,
        "liquidity_version": US_LIQUIDITY_VERSION,
        **asdict(params),
    }


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


_INGESTAO_GIRO = (
    "Ingira os preços diários (`python run_us_ingest.py daily --warehouse`) e "
    "republique a vitrine (`python run_us_ingest.py snapshot --warehouse`), ou "
    "zere a negociabilidade mínima para explorar o universo sem montar carteira."
)
_LIQUIDITY_TIMESTAMP_COLUMNS = (
    "giro_diario_usd_at", "giro_diario_usd_as_of", "liquidity_as_of",
)


def _apply_liquidity_gate(
    work: pd.DataFrame,
    params: USPortfolioCreationParams,
    exclusions: list[dict[str, Any]],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Piso de negociabilidade em três estados, fail-closed com piso > 0.

    Duas linhas de exclusão em vez de uma: "medido e abaixo do piso" e "nunca
    medido" são fatos diferentes e um usuário que vê 1.007 saídas precisa saber
    qual dos dois aconteceu. A contagem sequencial continua reconciliando.

    A coluna AUSENTE deixou de significar "todos passam". Antes o gate inteiro
    era pulado em silêncio quando ``giro_diario_usd`` não vinha na vitrine — o
    usuário pedia piso de US$ 1 mi/dia e recebia carteira sem nenhuma verificação
    de liquidez. Coluna ausente com piso > 0 significa "ninguém foi verificado",
    e nesse estado não se publica carteira.
    """
    from core.us_liquidity import (
        PISO_INVALIDO_MESSAGE,
        LiquidityPolicy,
        aplicar_piso,
        formata_usd_curto,
    )

    policy = LiquidityPolicy(piso_diario_usd=params.min_daily_turnover_usd)
    piso = policy.piso_normalizado
    meta: dict[str, Any] = {
        "liquidity_warnings": [],
        "liquidity_unverified": [],
        "liquidity_block": None,
        "liquidity_floor_usd": piso,
        "liquidity_exploratory": policy.exploratorio,
        "liquidity_version": US_LIQUIDITY_VERSION,
    }
    if work.empty:
        return work, meta

    if piso is None:
        meta["liquidity_warnings"] = [PISO_INVALIDO_MESSAGE]
        meta["liquidity_unverified"] = work["symbol"].astype(str).str.upper().tolist()
        meta["liquidity_block"] = PISO_INVALIDO_MESSAGE
        return _sequential_filter(
            work, pd.Series(False, index=work.index), "liquidity_invalid",
            PISO_INVALIDO_MESSAGE, exclusions,
        ), meta

    tem_coluna = "giro_diario_usd" in work
    timestamp_col = next((c for c in _LIQUIDITY_TIMESTAMP_COLUMNS if c in work), None)
    medido = _numeric(work, "giro_diario_usd")     # coluna ausente → tudo NaN
    giro = {str(s).upper(): float(v)
            for s, v in zip(work["symbol"], medido) if pd.notna(v)}
    timestamps = ({str(s).upper(): at for s, at in zip(work["symbol"], work[timestamp_col])}
                  if timestamp_col else {})
    screen = aplicar_piso(work["symbol"].astype(str).tolist(), giro,
                          timestamps=timestamps, policy=policy)
    meta["liquidity_warnings"] = list(screen.avisos)
    meta["liquidity_unverified"] = list(screen.nao_verificados)

    if policy.exploratorio:
        # Sem piso não há o que filtrar; o aviso já declara que a liquidez de
        # quem não tem série NÃO foi validada.
        return work, meta

    reprovados = {r["symbol"] for r in screen.removidos}
    chaves = work["symbol"].astype(str).str.upper()
    work = _sequential_filter(
        work, ~chaves.isin(reprovados), "liquidity",
        f"Volume medido abaixo de US$ {formata_usd_curto(piso)}/dia", exclusions,
    )
    chaves = work["symbol"].astype(str).str.upper()
    work = _sequential_filter(
        work, ~chaves.isin(set(screen.nao_verificados)), "liquidity_unverified",
        "Negociabilidade não verificada (sem série ou data de referência atual)", exclusions,
    )

    if not giro or not screen.aprovados:
        onde = ("a coluna `giro_diario_usd` não existe neste universo"
                if not tem_coluna else
                "a coluna `giro_diario_usd` existe mas nenhuma empresa do "
                "recorte tem giro medido") if not giro else (
                "não há nenhuma medição de giro com data de referência atual")
        meta["liquidity_block"] = (
            f"Negociabilidade não verificada: {onde}, e a negociabilidade mínima "
            f"está em US$ {formata_usd_curto(piso)}/dia. Nenhuma empresa pôde ser "
            f"verificada, então nenhuma carteira é publicada — comprar sem medir "
            f"negociabilidade é assumir risco não verificado. " + _INGESTAO_GIRO
        )
    return work, meta


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

    # Negociabilidade. Fica DEPOIS do score e ANTES da montagem das indústrias:
    # remover papel encalhado antes de contar a amostra evitaria que uma
    # indústria fosse aprovada por empresas que ninguém consegue comprar.
    work, liquidity_meta = _apply_liquidity_gate(work, params, exclusions)

    if work.empty:
        audit = pd.DataFrame(exclusions, columns=columns)
        audit.attrs["total"] = total
        audit.attrs["eligible"] = 0
        audit.attrs.update(liquidity_meta)
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
    audit.attrs.update(liquidity_meta)
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
    def _vazio() -> pd.DataFrame:
        """Frame vazio que ainda carrega o log — quem consome não precisa
        descobrir por qual caminho a seleção saiu vazia."""
        saida = pd.DataFrame()
        saida.attrs["quality_floor_log"] = {}
        return saida

    if eligible is None or eligible.empty or industry_audit is None or industry_audit.empty:
        return _vazio()
    approved = set(industry_audit.loc[
        industry_audit["status"].eq("Aprovada"), "industry_group"
    ].astype(str))
    if not approved:
        return _vazio()

    from core.us_quality_floor import apply_with_substitution

    floor_log: dict[str, list[dict]] = {}
    records = []
    for industry, group in eligible[eligible["industry_group"].isin(approved)].groupby(
        "industry_group", dropna=False
    ):
        ranked = group.sort_values(
            ["entry_score", "fundamental_score"], ascending=False)
        leaders = ranked.head(max(1, int(params.leaders_per_industry))).copy()

        if params.apply_quality_floor:
            # A vaga da indústria é preservada: quem é reprovado sai e o próximo
            # da MESMA indústria entra. Os pesos ainda não existem nesta etapa —
            # o otimizador os projeta depois sobre a lista final —, então o dict
            # vazio é o correto: a diversificação é mantida pela posição na
            # lista, não por herança de peso.
            escolhidos = apply_with_substitution(
                leaders["symbol"].astype(str).tolist(),
                list(zip(ranked["symbol"].astype(str),
                         _numeric(ranked, "entry_score").fillna(0.0))),
                ranked, {}, str(industry), floor_log,
            )
            if not escolhidos:
                continue
            leaders = ranked[ranked["symbol"].astype(str).str.upper().isin(
                {s.upper() for s in escolhidos})].copy()

        leaders["selection_reason"] = "Liderança de score na indústria aprovada"
        records.append(leaders)
    if not records:
        saida = pd.DataFrame()
        saida.attrs["quality_floor_log"] = floor_log
        return saida
    candidates = pd.concat(records, ignore_index=True)
    candidates = candidates.sort_values(
        ["entry_score", "fundamental_score"], ascending=False
    ).head(int(params.top_n)).reset_index(drop=True)
    candidates.attrs["quality_floor_log"] = floor_log
    return candidates


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
    target_weights: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Projeta pesos próximos ao alvo sob restrições lineares via SLSQP."""
    if candidates is None or candidates.empty:
        return pd.DataFrame(), []
    candidates = candidates.copy().reset_index(drop=True)
    caps, warnings = _effective_caps(candidates, params)
    if not params.adaptive_caps and any("inviável" in item for item in warnings):
        return pd.DataFrame(), warnings

    target = _base_target(candidates, params.weighting)
    if target_weights:
        proposed = candidates["symbol"].astype(str).map(target_weights)
        if proposed.notna().all() and float(proposed.sum()) > 0:
            target = proposed.to_numpy(dtype=float)
            target = target / target.sum()
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
    macro_impacts: dict[str, float] | None = None,
    macro_mode: str = "fundamental",
) -> dict[str, Any]:
    """Executa a criação completa e retorna payload pronto para a interface."""
    params = params or USPortfolioCreationParams()
    eligible, exclusions = prepare_eligible_universe(scored, params)
    audit = build_industry_audit(eligible, params, score_panel)
    candidates = select_industry_leaders(eligible, audit, params)
    floor_log = dict(candidates.attrs.get("quality_floor_log") or {})
    holdings, warnings = allocate_weights(candidates, params)
    macro_mode = str(macro_mode or "fundamental")
    if macro_mode not in {"fundamental", "moderate", "scenario"}:
        raise ValueError("modo macro inválido")
    if not holdings.empty:
        from core.macro_data.portfolio_tilt import apply_macro_tilt

        provisional = apply_macro_tilt(
            holdings,
            macro_impacts or {},
            symbol_column="symbol",
            score_column="entry_score",
            mode=macro_mode,
        )
        if macro_mode != "fundamental" and macro_impacts:
            target_by_symbol = dict(zip(
                provisional["symbol"].astype(str), provisional["weight"]
            ))
            constrained, macro_warnings = allocate_weights(
                candidates, params, target_weights=target_by_symbol,
            )
            warnings.extend(macro_warnings)
            if not constrained.empty:
                base_weights = holdings.set_index("symbol")["weight"]
                aligned_base = constrained["symbol"].map(base_weights).astype(float)
                turnover = float(
                    0.5 * (constrained["weight"] - aligned_base).abs().sum()
                )
                if turnover > 0.10:
                    constrained["weight"] = aligned_base + (
                        constrained["weight"] - aligned_base
                    ) * (0.10 / turnover)
                metadata = provisional.set_index("symbol")[[
                    "macro_covered", "macro_impact", "macro_score_adjustment",
                    "contextual_score", "weight_before_macro",
                ]]
                holdings = constrained.join(metadata, on="symbol")
                holdings.attrs["macro_mode"] = macro_mode
                holdings.attrs["macro_coverage"] = float(
                    holdings["macro_covered"].mean()
                )
                holdings.attrs["macro_turnover"] = float(
                    0.5 * (holdings["weight"] - aligned_base).abs().sum()
                )
            else:
                holdings = provisional
        else:
            holdings = provisional
        holdings["allocation_usd"] = (
            holdings["weight"] * float(params.capital_usd)
        )
        holdings["monthly_contribution_usd"] = (
            holdings["weight"] * float(params.monthly_contribution_usd)
        )
    history_available = score_panel is not None and not score_panel.empty

    # Os avisos do piso de negociabilidade nascem em prepare_eligible_universe,
    # antes de existirem indústrias. Sobem aqui para a mesma lista que a tela já
    # exibe, em vez de virarem um canal paralelo que a view precisa lembrar de ler.
    warnings = list(exclusions.attrs.get("liquidity_warnings") or []) + list(warnings)

    # Bloqueio de publicação. Não é aviso: com piso ligado e NENHUMA medição
    # disponível, "carteira vazia" e "carteira sem liquidez verificada" seriam
    # indistinguíveis na tela. Aqui a diferença fica explícita e acionável.
    liquidity_block = exclusions.attrs.get("liquidity_block")
    liquidity_unverified = list(exclusions.attrs.get("liquidity_unverified") or [])
    exploratory_unverified = bool(
        exclusions.attrs.get("liquidity_exploratory") and liquidity_unverified)
    publication_blocking_error = None
    if exploratory_unverified:
        publication_blocking_error = (
            f"{len(liquidity_unverified)} empresa(s) da carteira têm "
            "negociabilidade não verificada. O modo exploratório permite "
            "analisar esta composição, mas não enviá-la à Avaliação de "
            "Portfólio nem salvá-la como carteira padrão. Informe um giro "
            "diário com data de referência atual para publicar."
        )

    reprovados = floor_log.get("reprovados") or []
    if reprovados:
        trocas = len(floor_log.get("substituicoes") or [])
        vazias = len(floor_log.get("sem_substituto") or [])
        detalhe = f"{trocas} substituída(s) pelo próximo da mesma indústria"
        if vazias:
            detalhe += f"; {vazias} sem substituto aprovado na indústria"
        warnings.append(
            f"{len(reprovados)} líder(es) reprovado(s) no piso de qualidade — "
            f"{detalhe}.")

    # Corte inalcançável não pode falhar em silêncio. O piso de entrada é
    # comparado com a MÉDIA da indústria, e o score é recalculado sobre o
    # universo já filtrado — então não há como o usuário saber, olhando a tela,
    # qual valor o mercado de fato alcança naquele recorte. Subir o piso alguns
    # pontos pode zerar a carteira sem explicação.
    #
    # O aviso devolve o teto real do recorte, para calibrar em vez de adivinhar.
    # Com os parâmetros de fábrica ele não dispara: 44 indústrias aprovadas e 30
    # ativos (medido em 03/08/2026).
    if audit is not None and not audit.empty and "status" in audit.columns:
        aprovadas = int(audit["status"].eq("Aprovada").sum())
        if aprovadas == 0 and "avg_entry_score" in audit.columns:
            teto = pd.to_numeric(audit["avg_entry_score"], errors="coerce").max()
            if pd.notna(teto):
                warnings = list(warnings) + [
                    f"Nenhuma indústria aprovada: o piso de score de entrada está "
                    f"em {float(params.min_entry_score):.0f} e a melhor indústria "
                    f"do universo alcança {float(teto):.1f}. Baixe o piso para "
                    "obter carteira."]
    return {
        "ok": bool(not holdings.empty and not liquidity_block),
        "blocked": bool(liquidity_block),
        "blocking_error": liquidity_block,
        # ``blocked`` interrompe a análise; ``can_publish`` é mais estrito.
        # No modo exploratório, holdings sem giro validado continuam visíveis
        # para investigação, mas não podem chegar à avaliação/persistência.
        "can_publish": bool(
            not liquidity_block and not exploratory_unverified and not holdings.empty),
        "publication_blocking_error": publication_blocking_error,
        "liquidity_unverified": liquidity_unverified,
        "liquidity_floor_usd": exclusions.attrs.get("liquidity_floor_usd"),
        "liquidity_version": exclusions.attrs.get(
            "liquidity_version", US_LIQUIDITY_VERSION),
        "params": params_to_dict(params),
        "universe_count": int(exclusions.attrs.get("total", len(scored) if scored is not None else 0)),
        "eligible_count": int(len(eligible)),
        "eligible": eligible,
        "exclusions": exclusions,
        "industry_audit": audit,
        "candidates": candidates,
        "quality_floor_log": floor_log,
        "holdings": holdings,
        "metrics": portfolio_metrics(holdings, params),
        "macro": {
            "mode": macro_mode,
            "coverage": float(holdings.attrs.get("macro_coverage", 0.0)),
            "turnover": float(holdings.attrs.get("macro_turnover", 0.0)),
        },
        "warnings": warnings,
        "history_available": bool(history_available),
        "history_required_unavailable": bool(params.require_historical_signal and not history_available),
        "minimum_assets_for_position_cap": int(ceil(1 / max(params.max_weight, 1e-9))),
    }

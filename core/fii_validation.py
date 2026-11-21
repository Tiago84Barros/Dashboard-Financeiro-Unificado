"""Validação point-in-time e estatística da metodologia de FIIs v4."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

VALIDATION_STRATEGY_ID = "fii_rank_equal_weight_buffered.v1"

# ``market.fii_validation_runs`` guarda dois tipos de run e não tem coluna que
# os distinga: a validação PIT (``data_pipeline/market/fii_pit.py``, com
# ``backtest`` no ``metrics_json``) e o gate de prontidão que a auditoria diária
# grava (``fii_ingest.record_validation_readiness``, só com ``data_audit`` e
# sempre ``blocked``). Toda leitura de "a validação vigente" precisa deste
# filtro: sem ele, a auditoria de cada dia esconde o último run PIT aprovado --
# foi assim que a vitrine de FIIs parou de publicar desde 28/09/2026.
FILTRO_RUN_VALIDACAO_PIT = "metrics_json->'backtest' IS NOT NULL"


def validation_supports_strategy(validation: dict[str, Any], strategy_id: str) -> bool:
    """Só aceita um gate que tenha validado exatamente o motor em execução."""
    metrics = validation.get("metrics") or {}
    backtest = metrics.get("backtest") or {}
    validated_strategy = metrics.get("strategy_id") or backtest.get("strategy_id")
    return validation.get("status") == "passed" and validated_strategy == strategy_id


# Piso do limite inferior do IC 95% do excesso mensal sobre o IFIX. É AVISO,
# não portão: `validate_methodology` exige que a carteira ganhe do IFIX na
# média (`PISO_EXCESSO_MEDIO`, abaixo), não que o ganho seja significativo.
#
# Medido no run 90 (v6.10.0, 70 períodos): excesso médio de +0,136 p.p./mês
# com IC [−0,127; +0,380]. Os runs 88 e 89 dão o mesmo retrato (limite
# inferior −0,112 e −0,141). Com este piso como bloqueio, nenhum run da
# história do app teria passado. Até que o usuário peça mais, o selo
# "Aprovado" sai acompanhado do IC e da marca "excesso não significativo",
# para não ser lido como "bate o índice com folga". Os três leitores (card da
# carteira, aba de validação e `core.validacao_motor`) leem esta constante:
# guarda duplicada diverge.
PISO_IC_EXCESSO = 0.0

# Piso do excesso MÉDIO mensal líquido sobre o IFIX, e este sim é portão.
# Decisão do usuário de 19/11/2026: a validação só sai "passed" se a carteira
# ganhou do IFIX na média do walk-forward. Antes o portão atestava só o
# protocolo (integridade temporal, viabilidade, restrições), e o run 153
# (v6.8, 116 períodos) saiu "passed" com excesso de −0,189 p.p./mês, e a
# vitrine o publicou com o selo de aprovada. Estritamente maior: empatar com o
# índice não é ganhar dele. A média é a da manchete (publicável + caixa a
# Selic), a mesma que o card mostra -- o mês em caixa foi decisão da regra.
PISO_EXCESSO_MEDIO = 0.0


def leitura_do_excesso(metrics: dict[str, Any] | None) -> dict[str, Any]:
    """Excesso sobre o IFIX e seu IC, prontos para a tela, a partir do run.

    Aceita o ``metrics_json`` do run (com ``backtest``) ou o próprio dicionário
    do backtest. ``disponivel`` falso quando o intervalo não existe ou é NaN —
    ausência não vira "não significativo", que é afirmação sobre um número.
    """
    metrics = metrics or {}
    pit = metrics.get("backtest") if isinstance(metrics.get("backtest"), dict) else metrics
    ci = pit.get("excess_bootstrap") or {}

    def _f(valor: Any) -> float | None:
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            return None
        return numero if math.isfinite(numero) else None

    inferior, superior = _f(ci.get("lower")), _f(ci.get("upper"))
    media = _f(pit.get("mean_excess"))
    if media is None:
        media = _f(ci.get("mean"))
    disponivel = inferior is not None and superior is not None
    return {
        "disponivel": disponivel,
        "media": media,
        "inferior": inferior,
        "superior": superior,
        "periodos": int(ci.get("n") or 0),
        "piso": PISO_IC_EXCESSO,
        "significativo": bool(disponivel and inferior > PISO_IC_EXCESSO),
        # Achado D12 (17/11/2026): "não significativo" cobria do mesmo jeito o
        # run que ganha do IFIX sem significância e o que PERDE dele -- o run 66
        # saía "Aprovado · excesso não significativo" com média negativa. As
        # duas marcas abaixo separam o lado do zero. Desde 19/11/2026 o portão
        # também bloqueia média <= 0 (ver PISO_EXCESSO_MEDIO), então um run
        # "passed" não chega mais aqui negativo; a marca fica para runs antigos.
        "negativo": bool(disponivel and media is not None and media < 0.0),
        # IC inteiro abaixo de zero: a carteira perde do índice, e isso É
        # distinguível de acaso. Régua fixa em zero, não no piso: o piso é a
        # exigência para "ganha"; perder é perder.
        "perde_significativo": bool(disponivel and superior < 0.0),
    }


def _pp(valor: float) -> str:
    """Retorno mensal em fração para pontos percentuais com vírgula."""
    return f"{valor * 100:+.3f}".replace(".", ",").replace("-", "−")


def veredito_do_excesso(leitura: dict[str, Any]) -> str | None:
    """Rótulo curto do excesso, o MESMO em todas as telas e no motor.

    ``None`` quando o IC não existe -- ausência não vira veredito. Ordem:
    ganha com significância > perde com significância > média negativa sem
    significância > média positiva (ou ausente) sem significância.
    """
    if not leitura.get("disponivel"):
        return None
    if leitura.get("significativo"):
        return "significativo"
    if leitura.get("perde_significativo"):
        return "perde do IFIX, significativo"
    if leitura.get("negativo"):
        return "excesso negativo, não significativo"
    return "excesso não significativo"


def texto_do_excesso(leitura: dict[str, Any]) -> str:
    """Uma linha: média, IC 95% e o veredito contra o piso."""
    if not leitura.get("disponivel"):
        return "IC do excesso sobre o IFIX indisponível neste run"
    media = leitura.get("media")
    prefixo = (f"excesso médio de {_pp(media)} p.p./mês sobre o IFIX, "
               if media is not None else "excesso sobre o IFIX, ")
    return (f"{prefixo}IC 95% de {_pp(leitura['inferior'])} a "
            f"{_pp(leitura['superior'])} p.p. — {veredito_do_excesso(leitura)}")


@dataclass(frozen=True)
class ValidationThresholds:
    min_periods: int = 36
    min_universe_coverage: float = .80
    min_rank_stability: float = .45
    max_annual_turnover: float = 3.0
    min_regimes: int = 3
    min_verified_snapshot_fraction: float = .80
    min_return_observation_coverage: float = .90
    min_optimizer_feasible_fraction: float = .95
    min_correlation_coverage: float = .80


def bootstrap_mean_ci(values: Iterable[float], *, samples: int = 2000,
                      confidence: float = .95, seed: int = 42,
                      block_size: int | None = None) -> dict[str, float | int]:
    array = np.asarray([float(v) for v in values if pd.notna(v)], dtype=float)
    if array.size < 2:
        return {"n": int(array.size), "mean": float(array.mean()) if array.size else np.nan,
                "lower": np.nan, "upper": np.nan}
    rng = np.random.default_rng(seed)
    size = int(block_size or max(1, round(array.size ** (1 / 3))))
    size = min(size, array.size)
    if size == 1:
        means = rng.choice(array, size=(samples, array.size), replace=True).mean(axis=1)
    else:
        # Moving-block bootstrap circular preserva dependência serial local.
        starts = rng.integers(0, array.size, size=(samples, math.ceil(array.size / size)))
        offsets = np.arange(size)
        sampled = array[(starts[..., None] + offsets) % array.size].reshape(samples, -1)
        means = sampled[:, :array.size].mean(axis=1)
    alpha = (1 - confidence) / 2
    return {"n": int(array.size), "mean": float(array.mean()),
            "lower": float(np.quantile(means, alpha)), "upper": float(np.quantile(means, 1 - alpha))}


def ranking_stability(previous: pd.Series, current: pd.Series, *, top_k: int = 20) -> dict[str, float]:
    common = previous.dropna().index.intersection(current.dropna().index)
    spearman = float(previous.loc[common].corr(current.loc[common], method="spearman")) if len(common) >= 3 else np.nan
    prev_top = set(previous.nlargest(top_k).index)
    curr_top = set(current.nlargest(top_k).index)
    union = prev_top | curr_top
    return {"spearman": spearman, "top_k_jaccard": len(prev_top & curr_top) / len(union) if union else np.nan}


def portfolio_turnover(previous: pd.Series, current: pd.Series) -> float:
    index = previous.index.union(current.index)
    return float(.5 * (previous.reindex(index, fill_value=0) - current.reindex(index, fill_value=0)).abs().sum())


def point_in_time_backtest(
    snapshots: pd.DataFrame, returns: pd.DataFrame, benchmark: pd.Series, *,
    top_n: int = 12, transaction_cost: float = .0015, slippage: float = .0010,
    min_daily_return_coverage: float = .80, rank_buffer: int | None = None,
) -> dict[str, Any]:
    """Backtest sem look-ahead usando apenas snapshots disponíveis na data.

    `snapshots` precisa conter reference_date, available_at, ticker, score e,
    idealmente, active_status. Fundos encerrados/incorporados permanecem no
    universo histórico; active_status só é avaliado na respectiva data.
    `returns` é long-form com date, ticker e total_return.
    """
    required_snap = {"reference_date", "available_at", "ticker", "score"}
    required_ret = {"date", "ticker", "total_return"}
    if not required_snap.issubset(snapshots.columns) or not required_ret.issubset(returns.columns):
        return {"status": "blocked", "blockers": ["colunas point-in-time obrigatórias ausentes"]}
    s = snapshots.copy()
    r = returns.copy()
    benchmark = benchmark.copy()
    benchmark.index = pd.to_datetime(benchmark.index).normalize()
    s["reference_date"] = pd.to_datetime(s["reference_date"]).dt.normalize()
    s["available_at"] = pd.to_datetime(s["available_at"], utc=True).dt.tz_localize(None)
    r["date"] = pd.to_datetime(r["date"]).dt.normalize()
    dates = sorted(set(s["reference_date"]))
    return_dates = sorted(set(r["date"]))
    previous = pd.Series(dtype=float)
    observations: list[dict] = []
    ranks: list[pd.Series] = []
    turnovers: list[float] = []
    verified_snapshots = 0
    used_snapshots = 0
    weighted_return_coverages: list[float] = []
    for position, dt in enumerate(dates):
        decision_cutoff = dt + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        possible_execution = [value for value in return_dates if value > dt]
        if not possible_execution:
            continue
        execution_date = possible_execution[0]
        next_decision = dates[position + 1] if position + 1 < len(dates) else None
        next_execution = None
        if next_decision is not None:
            choices = [value for value in return_dates if value > next_decision]
            next_execution = choices[0] if choices else None
        universe = s[(s["reference_date"] <= dt) & (s["available_at"] <= decision_cutoff)]
        if "availability_quality" in universe:
            universe = universe[universe["availability_quality"] != "migration_baseline"]
        if "active_status" in universe:
            universe = universe[universe["active_status"].fillna("active").isin(["active", "listed"])]
        latest = universe.sort_values(["reference_date", "available_at"]).drop_duplicates(
            "ticker", keep="last")
        if latest.empty:
            continue
        scores = latest.set_index("ticker")["score"].astype(float)
        if "confidence" in latest:
            confidence = pd.to_numeric(
                latest.set_index("ticker")["confidence"], errors="coerce"
            )
            scores = scores * (.75 + .25 * confidence.reindex(scores.index).fillna(0.0))
        ordered = scores.sort_values(ascending=False)
        buffer_size = max(int(rank_buffer if rank_buffer is not None else math.ceil(top_n * .25)), 0)
        if previous.empty or buffer_size == 0:
            chosen = ordered.head(top_n).index
        else:
            # Banda de permanência: um ativo existente só sai se cair além de
            # top_n + buffer. Isso aproxima o rebalanceamento orientado por
            # eventos e reduz trocas causadas por ruído marginal do ranking.
            eligible_hold = set(ordered.head(top_n + buffer_size).index)
            retained = [ticker for ticker in previous.index
                        if ticker in eligible_hold and ticker in ordered.index]
            additions = [ticker for ticker in ordered.index if ticker not in retained]
            chosen = pd.Index((retained + additions)[:top_n])
        weights = pd.Series(1 / len(chosen), index=chosen, dtype=float)
        used_snapshots += len(chosen)
        if "availability_quality" in latest:
            qualities = latest.set_index("ticker")["availability_quality"].reindex(chosen)
            verified_snapshots += int(qualities.isin(
                ["verified_publication", "market_close_observable"]
            ).sum())
        else:
            verified_snapshots += len(chosen)
        period_rows = r[r["date"] >= execution_date]
        if next_execution is not None:
            period_rows = period_rows[period_rows["date"] < next_execution]
        period_matrix = period_rows.pivot_table(index="date", columns="ticker",
                                                values="total_return", aggfunc="last")
        valid = weights.index.intersection(period_matrix.columns)
        coverage = len(valid) / len(weights) if len(weights) else 0
        if not len(valid):
            continue
        # A-118: um fundo escolhido que NAO tem nenhum retorno no periodo saiu
        # de campo -- liquidacao, incorporacao ou fim do dado. Renormalizar os
        # pesos aqui redistribuia a fatia dele entre os sobreviventes, ou seja,
        # fazia o dinheiro do fundo que sumiu render o que os OUTROS renderam.
        # Medido em 24/08/2026: uma cesta de tres fundos com um caindo 2% ao dia
        # rendia -6,46% com ele presente e +10,49% quando ele liquidava e sumia.
        # Inversao de sinal. `coverage` ja caia para 67% e era reportado, mas
        # como media agregada que ninguem traduz para "o retorno exclui um terco
        # da carteira". A fatia ausente passa a render ZERO no periodo: nao
        # inventa a perda (pode ser buraco de dado), e nao deixa o ganho dos
        # sobreviventes ocupar o lugar dela.
        peso_ausente = float(max(0.0, 1.0 - float(weights.loc[valid].sum())))
        weights = weights.loc[valid] / weights.loc[valid].sum()
        turn = portfolio_turnover(previous, weights) if not previous.empty else 1.0
        matrix = period_matrix[valid]
        present_weight = matrix.notna().mul(weights, axis=1).sum(axis=1)
        weighted_return_coverages.extend(present_weight.tolist())
        eligible_days = present_weight >= float(min_daily_return_coverage)
        matrix = matrix.loc[eligible_days]
        present_weight = present_weight.loc[eligible_days]
        if matrix.empty:
            continue
        daily = matrix.mul(weights, axis=1).sum(axis=1, skipna=True) / present_weight
        gross = float((1.0 + daily).prod() - 1.0)
        gross = (1.0 - peso_ausente) * gross   # A-118: a fatia que sumiu rende 0
        net = gross - turn * (transaction_cost + slippage)
        benchmark_period = benchmark[(benchmark.index >= execution_date)]
        if next_execution is not None:
            benchmark_period = benchmark_period[benchmark_period.index < next_execution]
        benchmark_return = (float((1.0 + benchmark_period.dropna()).prod() - 1.0)
                            if len(benchmark_period.dropna()) else np.nan)
        observations.append({"date": execution_date, "decision_date": dt,
                             "portfolio_return": net,
                             "benchmark_return": benchmark_return,
                             "coverage": coverage, "turnover": turn,
                             "peso_ausente": peso_ausente,
                             "holdings": {str(ticker): float(weight)
                                          for ticker, weight in weights.items()}})
        turnovers.append(turn)
        ranks.append(scores)
        previous = weights
    result = pd.DataFrame(observations)
    if result.empty:
        return {"status": "blocked", "blockers": ["nenhum período PIT elegível"]}
    excess = result["portfolio_return"] - result["benchmark_return"]
    stabilities = [ranking_stability(a, b)["spearman"] for a, b in zip(ranks, ranks[1:])]
    valid_stabilities = [value for value in stabilities if not np.isnan(value)]
    portfolio_curve = (1.0 + result["portfolio_return"]).cumprod()
    drawdown = portfolio_curve / portfolio_curve.cummax() - 1.0
    valid_excess = excess.dropna()
    information_ratio = (
        float(valid_excess.mean() / valid_excess.std(ddof=1) * math.sqrt(12))
        if len(valid_excess) > 1 and valid_excess.std(ddof=1) > 0 else np.nan
    )
    return {
        "status": "calculated", "periods": len(result),
        "strategy_id": VALIDATION_STRATEGY_ID,
        "mean_return": float(result["portfolio_return"].mean()),
        "mean_benchmark": float(result["benchmark_return"].mean()),
        "mean_excess": float(excess.mean()), "excess_bootstrap": bootstrap_mean_ci(excess),
        "mean_coverage": float(result["coverage"].mean()),
        "saida_de_campo": {
            "peso_ausente_medio": float(result["peso_ausente"].mean()),
            "peso_ausente_maximo": float(result["peso_ausente"].max()),
            "periodos_com_ausencia": int((result["peso_ausente"] > 1e-9).sum()),
            "periodos": int(len(result)),
        },
        "annualized_turnover": float(np.mean(turnovers) * 12),
        "rank_stability": float(np.mean(valid_stabilities)) if valid_stabilities else np.nan,
        "verified_snapshot_fraction": verified_snapshots / used_snapshots if used_snapshots else 0.0,
        "return_observation_coverage": (float(np.mean(weighted_return_coverages))
                                        if weighted_return_coverages else 0.0),
        "max_drawdown": float(drawdown.min()) if len(drawdown) else np.nan,
        "information_ratio": information_ratio,
        "rank_buffer": int(rank_buffer if rank_buffer is not None else math.ceil(top_n * .25)),
        "observations": result.to_dict("records"),
    }


def _metricas_da_coorte(bloco: pd.DataFrame) -> dict[str, Any]:
    """Desempenho de um subconjunto de periodos, com a curva declarada.

    Os periodos de uma coorte nao sao contiguos. Compor ``1 + r`` sobre eles
    responde "como se comportaram estas carteiras", nao "quanto eu teria
    ganho": o mes que ficou de fora nao vira caixa nem benchmark, ele
    simplesmente nao existe nesta curva. Por isso o drawdown sai com nome
    proprio -- ``max_drawdown_concatenado`` --, para nao ser lido como o
    drawdown de uma estrategia que alguem poderia ter seguido.
    """
    if bloco.empty:
        return {"periods": 0}
    excess = bloco["portfolio_return"] - bloco["benchmark_return"]
    validos = excess.dropna()
    curva = (1.0 + bloco["portfolio_return"]).cumprod()
    drawdown = curva / curva.cummax() - 1.0
    return {
        "periods": int(len(bloco)),
        "mean_return": float(bloco["portfolio_return"].mean()),
        "mean_benchmark": float(bloco["benchmark_return"].mean()),
        "mean_excess": float(excess.mean()),
        "excess_bootstrap": bootstrap_mean_ci(excess),
        "annualized_turnover": float(bloco["turnover"].mean() * 12),
        "max_drawdown_concatenado": float(drawdown.min()),
        "information_ratio": (
            float(validos.mean() / validos.std(ddof=1) * math.sqrt(12))
            if len(validos) > 1 and validos.std(ddof=1) > 0 else float("nan")
        ),
    }


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def ultimo_snapshot_na_data(snapshots: pd.DataFrame, decision: pd.Timestamp) -> pd.DataFrame:
    """Último snapshot de cada fundo público em ``decision``.

    ``snapshots`` com ``reference_date`` normalizada e ``available_at`` sem
    fuso (o preparo de `robust_optimizer_point_in_time_backtest`): referência
    e disponibilidade até a data, sem a linha de base da migração.
    """
    decision = pd.Timestamp(decision)
    decision_cutoff = decision + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    s = snapshots
    universe = s[
        (s["reference_date"] <= decision) & (s["available_at"] <= decision_cutoff)
    ]
    if "availability_quality" in universe:
        universe = universe[
            universe["availability_quality"] != "migration_baseline"
        ]
    return (
        universe.sort_values(["reference_date", "available_at"])
        .drop_duplicates("ticker", keep="last")
    )


def preparar_snapshots(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Datas no formato que `ultimo_snapshot_na_data` compara."""
    s = snapshots.copy()
    s["reference_date"] = pd.to_datetime(s["reference_date"]).dt.normalize()
    s["available_at"] = pd.to_datetime(s["available_at"], utc=True).dt.tz_localize(None)
    return s


def linhas_pit_na_data(latest: pd.DataFrame) -> list[dict[str, Any]]:
    """Linhas de entrada da elegibilidade a partir de `ultimo_snapshot_na_data`.

    Ponto único: o backtest robusto e a carteira de safras
    (``scripts/medir_safras_fii.py``) montam as linhas daqui.
    """
    rows: list[dict[str, Any]] = []
    for record in latest.to_dict("records"):
        row = _payload(record.get("portfolio_input_json"))
        if not row:
            continue
        row.update({
            "ticker": str(record["ticker"]),
            "tipo": row.get("tipo") or record.get("fii_type"),
            "type_score": float(record["score"]),
            "confidence": float(record["confidence"]),
            "coverage": float(record.get("coverage") or 0.0),
            # Neutraliza somente o gate recursivo de validação. Não altera pesos.
            "publication_status": "validated",
        })
        rows.append(row)
    return rows


def robust_optimizer_point_in_time_backtest(
    snapshots: pd.DataFrame, returns: pd.DataFrame, benchmark: pd.Series,
    macro_scenarios: dict[str, dict[str, float]], *,
    top_n: int = 12, transaction_cost: float = .0015, slippage: float = .0010,
    correlation_penalty: float = .12, min_return_coverage: float = .80,
    correlation_lookback_months: int = 36, correlation_min_months: int = 12,
    medir_rascunho: bool = False,
) -> dict[str, Any]:
    """Walk-forward do mesmo otimizador robusto usado pela carteira-modelo.

    Cada rebalanceamento aplica a elegibilidade padrão vigente, o score reconstruído
    naquela data, o cenário macro então observável e correlações calculadas
    exclusivamente com retornos anteriores à decisão.

    Retorno em três camadas (auditoria app4, FII-10): bruto, após custo de
    giro e líquido de IR. ``portfolio_return`` -- o que alimenta excesso,
    bootstrap e regimes -- é o LÍQUIDO: com giro de 263% ao ano, vender cota
    com ganho paga 20% (o rendimento distribuído é isento), e um backtest que
    não cobra isso mede um investidor que não existe. O IR exige separar preço
    de provento; sem a coluna ``price_return`` nos retornos ele não é
    calculado e ``ir_calculado`` sai ``False`` -- nunca um zero silencioso.
    O IFIX de comparação segue bruto: é o índice, não um investidor.

    Período sem carteira publicável é CAIXA À SELIC (unificação com a
    medição fora da amostra da Empresas B3, 18/11/2026). A tela só deixa
    publicar a carteira que passa no portão de transparência; o rascunho
    recusado aparece, mas ninguém o recebe como carteira-modelo. Quem seguia
    a estratégia ficou em caixa naquele mês -- e também no mês em que o
    otimizador não montou carteira nenhuma. Os dois casos rendem a Selic da
    decisão (a do cenário macro, observável na data), pagam o giro e o IR de
    zerar a posição anterior, e a recompra no período seguinte paga giro
    cheio. Ficam FORA da curva, como antes: o aquecimento (períodos antes da
    primeira carteira montada), a safra sem insumo (colunas ausentes, cenário
    macro ausente, estágio ``data_prerequisites``) e o período sem datas de
    retorno -- falta de dado não é decisão de ficar em caixa.

    ``medir_rascunho=True`` mede o rascunho recusado com os próprios pesos,
    como fazia a 1ª versão da correção. É SENSIBILIDADE: a chamada padrão a
    roda sozinha quando há recusa e devolve o resumo em
    ``sensibilidade_rascunho``. Mesma regra de
    ``core.b3_oos_carteira.carteira_salvavel``.
    """
    from core.backtest_liquido import IR_FII_GANHO, RastreadorIR, premissas_fii
    from core.fii_carteira_protegida import montar_carteira_com_concessao
    from core.fii_integrated_model import (
        ColunasDeElegibilidadeAusentes,
        IntegratedEligibilityPolicy,
        apply_integrated_eligibility,
    )
    from core.fii_methodology import MacroScenario
    from core.fii_portfolio_v4 import (
        LIVE_PORTFOLIO_STRATEGY_ID,
        PortfolioPolicy,
        optimize_diligence_portfolio,
    )

    required_snap = {
        "reference_date", "available_at", "ticker", "score", "confidence",
        "portfolio_input_json",
    }
    required_ret = {"date", "ticker", "total_return"}
    if not required_snap.issubset(snapshots.columns) or not required_ret.issubset(returns.columns):
        return {
            "status": "blocked",
            "strategy_id": LIVE_PORTFOLIO_STRATEGY_ID,
            "blockers": ["inputs PIT do otimizador robusto ausentes"],
        }

    s = preparar_snapshots(snapshots)
    r = returns.copy()
    b = benchmark.copy()
    r["date"] = pd.to_datetime(r["date"]).dt.normalize()
    b.index = pd.to_datetime(b.index).normalize()
    dates = sorted(set(s["reference_date"]))
    return_dates = sorted(set(r["date"]))
    ir_calculado = "price_return" in r.columns
    rastreador_ir = RastreadorIR(IR_FII_GANHO)
    previous = pd.Series(dtype=float)
    observations: list[dict[str, Any]] = []
    ranks: list[pd.Series] = []
    turnovers: list[float] = []
    weighted_return_coverages: list[float] = []
    correlation_coverages: list[float] = []
    eligible_counts: list[int] = []
    verified_snapshots = 0
    used_snapshots = 0
    attempted_optimizer = 0
    successful_optimizer = 0
    constraint_violation_periods = 0
    constraint_violation_details: list[dict[str, Any]] = []
    concession_periods = 0
    concession_details: list[dict[str, Any]] = []
    missing_macro_periods = 0
    optimizer_input_skips = 0
    # Um periodo em que o otimizador ENTREGOU carteira e a publicacao foi
    # recusada nao e a mesma coisa que um periodo em que ele nao entregou
    # nada. Os dois contam contra a fracao viavel -- ela mede quantas vezes
    # havia carteira PUBLICAVEL --, mas a causa e outra, e o bloqueador
    # precisa dizer qual delas predomina. Na curva os dois viram caixa a
    # Selic; so com `medir_rascunho` o recusado e medido com os pesos.
    publicacao_recusada: list[str] = []
    optimizer_failures: list[dict[str, Any]] = []
    # Aquecimento: antes da primeira carteira montada a estrategia ainda nao
    # existe, e caixa ali seria um periodo inventado. Igual nas duas cadeias
    # (com e sem `medir_rascunho`), porque depende so de haver itens.
    comecou = False

    def _retorno_do_benchmark(inicio: pd.Timestamp, fim: pd.Timestamp | None) -> float:
        periodo = b[b.index >= inicio]
        if fim is not None:
            periodo = periodo[periodo.index < fim]
        periodo = periodo.dropna()
        return float((1.0 + periodo).prod() - 1.0) if len(periodo) else np.nan

    def _periodo_em_caixa(decision: pd.Timestamp, execution_date: pd.Timestamp,
                          next_execution: pd.Timestamp | None, *,
                          motivo: str, recusado: bool) -> None:
        """Registra o periodo como caixa a Selic, com giro e IR de zerar."""
        nonlocal previous
        if not comecou:
            return
        valores = macro_scenarios.get(decision.date().isoformat()) or {}
        selic = valores.get("selic")
        if selic is None or not np.isfinite(float(selic)):
            return
        meses = [d for d in return_dates if d >= execution_date
                 and (next_execution is None or d < next_execution)]
        if not meses:
            return
        gross = float((1.0 + float(selic) / 100.0) ** (len(meses) / 12.0) - 1.0)
        vazia = pd.Series(dtype=float)
        turn = portfolio_turnover(previous, vazia) if not previous.empty else 0.0
        after_costs = gross - turn * (transaction_cost + slippage)
        ir_periodo = rastreador_ir.rebalancear({}) if ir_calculado else 0.0
        observations.append({
            "date": execution_date, "decision_date": decision,
            "portfolio_return": after_costs - ir_periodo,
            "benchmark_return": _retorno_do_benchmark(execution_date, next_execution),
            "portfolio_return_bruto": gross,
            "portfolio_return_apos_custos": after_costs,
            "ir_periodo": ir_periodo,
            # Sem carteira nao ha universo a cobrir: NaN fica fora da media de
            # cobertura em vez de puxa-la para zero ou para um.
            "coverage": np.nan, "turnover": turn,
            "peso_ausente": 0.0,
            "holdings": {},
            "protecao_cedida": [],
            "viability_notes": [],
            "carteira_de_excecao": False,
            "publicacao_recusada": bool(recusado),
            "caixa": True,
            "motivo_caixa": motivo,
        })
        turnovers.append(turn)
        previous = vazia

    for position, decision in enumerate(dates):
        possible_execution = [value for value in return_dates if value > decision]
        if not possible_execution:
            continue
        execution_date = possible_execution[0]
        next_decision = dates[position + 1] if position + 1 < len(dates) else None
        next_execution = None
        if next_decision is not None:
            choices = [value for value in return_dates if value > next_decision]
            next_execution = choices[0] if choices else None

        latest = ultimo_snapshot_na_data(s, decision)
        rows = linhas_pit_na_data(latest)
        try:
            eligible, eligibility = apply_integrated_eligibility(
                rows, IntegratedEligibilityPolicy(),
            )
        except ColunasDeElegibilidadeAusentes as erro:
            # Safra sem as colunas que a política lê é falha de insumo, não
            # universo vazio: registrar como skip nomeado impede que o
            # backtest conte um período inteiro como "ninguém elegível".
            optimizer_input_skips += 1
            optimizer_failures.append({
                "decision_date": decision.date().isoformat(),
                "reason": str(erro),
                "missing_columns": list(erro.missing_columns),
                "failure_stage": "data_prerequisites",
            })
            continue
        eligible_counts.append(int(eligibility["eligible_count"]))
        candidatos_da_concessao = eligibility.get("concession_candidates") or ()
        if not eligible and not candidatos_da_concessao:
            optimizer_input_skips += 1
            optimizer_failures.append({
                "decision_date": decision.date().isoformat(),
                "reason": "universo elegível vazio e sem candidatos à concessão",
                "failure_stage": "data_prerequisites",
            })
            if rows:
                # Havia fundos com dado e nenhum passou na elegibilidade: a
                # estrategia decidiu nao investir. Sem `rows` e falta de dado.
                _periodo_em_caixa(decision, execution_date, next_execution,
                                  motivo="sem_carteira", recusado=False)
            continue
        # Estrito vazio COM candidatos não é safra perdida: é exatamente o caso
        # em que a concessão existe. Pular aqui era desligar o mecanismo na
        # única situação em que ele é necessário.

        scenario_values = macro_scenarios.get(decision.date().isoformat())
        if not scenario_values:
            missing_macro_periods += 1
            continue
        scenario = MacroScenario(**scenario_values)

        history = r[r["date"] <= decision]
        history_matrix = history.pivot_table(
            index="date", columns="ticker", values="total_return", aggfunc="last",
        ).tail(max(int(correlation_lookback_months), int(correlation_min_months)))
        def _optimizer_kwargs(pool: list[dict], _matrix=history_matrix) -> dict:
            # A correlação acompanha o pool da tentativa: readmitir um fundo
            # sem incluí-lo na matriz rebaixaria a cobertura em silêncio.
            tickers = [str(row["ticker"]) for row in pool]
            usable = [
                ticker for ticker in tickers if ticker in _matrix.columns
                and int(_matrix[ticker].notna().sum()) >= correlation_min_months
            ]
            return {
                "correlation_matrix": (
                    _matrix[usable].corr(min_periods=correlation_min_months).to_dict()
                    if len(usable) >= 2 else None
                ),
                "correlation_penalty": float(correlation_penalty),
                "previous_weights": previous.to_dict(),
            }

        # Mesmo orquestrador da tela: o backtest PIT não pode ter uma regra de
        # concessão própria, ou passa a medir uma estratégia que não existe.
        optimized = montar_carteira_com_concessao(
            eligible, candidatos_da_concessao, scenario,
            policy=PortfolioPolicy(max_assets=int(top_n)),
            optimizer_kwargs=_optimizer_kwargs,
            optimizer=optimize_diligence_portfolio,
        )
        def _registro_de_falha(stage: str) -> dict[str, Any]:
            return {
                "decision_date": decision.date().isoformat(),
                "reason": " · ".join(
                    optimized.get("blockers") or ["otimização não publicável"]
                ),
                "failure_stage": stage,
                "eligible_assets": len(eligible),
                "dimension_coverage": optimized.get("dimension_coverage") or {},
                "candidate_pool": optimized.get("candidate_pool")
                or (optimized.get("feasibility_diagnostics") or {}).get("candidate_pool")
                or {},
                # Sem isto o motivo fica legivel e a causa nao: "menos de N
                # categorias elegiveis" nao diz QUAIS tipos existiam naquela
                # data, e diagnosticar exigia reconstruir a safra inteira
                # fora do motor. A contagem ja esta calculada em
                # `_adaptive_type_bands`; so estava sendo descartada.
                "available_by_type": (
                    optimized.get("feasibility_diagnostics") or {}
                ).get("available_by_type") or {},
            }

        if not optimized.get("items"):
            failure_stage = str(optimized.get("failure_stage") or "optimizer")
            if failure_stage == "data_prerequisites":
                optimizer_input_skips += 1
            else:
                attempted_optimizer += 1
            optimizer_failures.append(_registro_de_falha(failure_stage))
            if failure_stage != "data_prerequisites":
                # O otimizador tentou e nao montou carteira: caixa.
                _periodo_em_caixa(decision, execution_date, next_execution,
                                  motivo="sem_carteira", recusado=False)
            continue
        attempted_optimizer += 1
        comecou = True
        # Carteira montada e publicacao recusada: a tela mostra o rascunho
        # (`views/fiis.py`, "Rascunho nao publicavel", com pesos e motivos),
        # mas nao deixa publica-lo -- quem seguia a carteira-modelo ficou em
        # caixa. A manchete conta o periodo como caixa a Selic; os pesos do
        # rascunho entram so na sensibilidade (`medir_rascunho`). A fracao
        # viavel nao muda: o periodo continua fora de `successful_optimizer`,
        # porque a fracao mede quantas vezes havia carteira PUBLICAVEL.
        publicavel = bool(optimized.get("can_publish"))
        if publicavel:
            successful_optimizer += 1
        else:
            publicacao_recusada.append(decision.date().isoformat())
            optimizer_failures.append(_registro_de_falha(
                str(optimized.get("failure_stage") or "optimizer")))
            if not medir_rascunho:
                # Antes de registrar cessao, violacao e cobertura de
                # correlacao: elas descrevem a carteira, e esta nao foi
                # recebida por ninguem.
                _periodo_em_caixa(decision, execution_date, next_execution,
                                  motivo="publicacao_recusada", recusado=True)
                continue
        if optimized.get("constraint_violations"):
            constraint_violation_periods += 1
            constraint_violation_details.append({
                "decision_date": decision.date().isoformat(),
                "violations": list(optimized["constraint_violations"]),
            })
        # A cessão de proteção tem de sobreviver à persistência. Uma safra cuja
        # carteira só existe porque fundos tiveram o portão cedido gravava
        # histórico limpo em metrics_json — proteção cedida apresentada como
        # ausência de risco no artefato que sustenta a estratégia.
        cessao = list(optimized.get("protecao_cedida_na_elegibilidade") or [])
        notas_de_viabilidade = list(optimized.get("viability_notes") or [])
        if cessao:
            # A CONTAGEM da fração só entra depois que o período vira
            # observação — ver adiante. Aqui só se registra a evidência: os
            # `continue` abaixo descartam períodos sem retorno, e contar a
            # cessão antes deles colocava no numerador um período que o
            # denominador (`len(result)`) nunca veria. A fração passava de
            # 1,0 comparando populações diferentes.
            concession_details.append({
                "decision_date": decision.date().isoformat(),
                "protecao_cedida": cessao,
                "viability_notes": notas_de_viabilidade,
            })
        correlation_coverages.append(float(
            (optimized.get("correlation_info") or {}).get("coverage") or 0.0
        ))
        weights = pd.Series({
            str(item["ticker"]): float(item["weight"])
            for item in optimized["items"]
        }, dtype=float)
        used_snapshots += len(weights)
        if "availability_quality" in latest:
            qualities = latest.set_index("ticker")["availability_quality"].reindex(weights.index)
            verified_snapshots += int(qualities.isin(
                ["verified_publication", "market_close_observable"]
            ).sum())
        else:
            verified_snapshots += len(weights)

        period_rows = r[r["date"] >= execution_date]
        if next_execution is not None:
            period_rows = period_rows[period_rows["date"] < next_execution]
        period_matrix = period_rows.pivot_table(
            index="date", columns="ticker", values="total_return", aggfunc="last",
        )
        valid = weights.index.intersection(period_matrix.columns)
        coverage = len(valid) / len(weights) if len(weights) else 0.0
        if not len(valid):
            continue
        # A-118, aplicado tambem ao backtest que certifica a carteira ao vivo.
        # A correcao existia so em `point_in_time_backtest`; este walk-forward
        # -- o unico que roda em producao (`data_pipeline/market/fii_pit.py`)
        # -- seguia renormalizando, e o fundo que liquidava rendia o que os
        # sobreviventes renderam. A fatia ausente rende ZERO no periodo, pela
        # mesma regra da versao simples. Medida contra o peso total da carteira
        # entregue, nao contra 1,0: o otimizador nao promete soma exata.
        total_entregue = float(weights.sum())
        peso_ausente = (
            float(max(0.0, 1.0 - float(weights.loc[valid].sum()) / total_entregue))
            if total_entregue > 0 else 0.0
        )
        weights = weights.loc[valid] / weights.loc[valid].sum()
        turn = portfolio_turnover(previous, weights) if not previous.empty else 1.0
        matrix = period_matrix[valid]
        present_weight = matrix.notna().mul(weights, axis=1).sum(axis=1)
        weighted_return_coverages.extend(present_weight.tolist())
        eligible_periods = present_weight >= float(min_return_coverage)
        matrix = matrix.loc[eligible_periods]
        present_weight = present_weight.loc[eligible_periods]
        if matrix.empty:
            continue
        periodic = matrix.mul(weights, axis=1).sum(axis=1, skipna=True) / present_weight
        gross = float((1.0 + periodic).prod() - 1.0)
        gross = (1.0 - peso_ausente) * gross   # A-118: a fatia que sumiu rende 0
        after_costs = gross - turn * (transaction_cost + slippage)
        ir_periodo = 0.0
        if ir_calculado:
            # O IR do rebalanceamento cai no período que ele abre, como o
            # custo de giro. Mês sem retorno conta como posição parada, a
            # mesma convenção do bruto acima.
            ir_periodo = rastreador_ir.rebalancear(weights.to_dict())
            price_matrix = period_rows.pivot_table(
                index="date", columns="ticker", values="price_return", aggfunc="last",
            ).reindex(index=matrix.index, columns=matrix.columns)
            total_por_ativo = (1.0 + matrix.fillna(0.0)).prod() - 1.0
            preco_por_ativo = (1.0 + price_matrix.fillna(0.0)).prod() - 1.0
            rastreador_ir.evoluir(
                total_por_ativo.to_dict(),
                (total_por_ativo - preco_por_ativo).to_dict(),
            )
        net = after_costs - ir_periodo
        benchmark_return = _retorno_do_benchmark(execution_date, next_execution)
        observations.append({
            "date": execution_date, "decision_date": decision,
            "portfolio_return": net, "benchmark_return": benchmark_return,
            "portfolio_return_bruto": gross,
            "portfolio_return_apos_custos": after_costs,
            "ir_periodo": ir_periodo,
            "coverage": coverage, "turnover": turn,
            "peso_ausente": peso_ausente,
            "holdings": {
                str(ticker): float(weight) for ticker, weight in weights.items()
            },
            "protecao_cedida": cessao,
            "viability_notes": notas_de_viabilidade,
            # Sob o padrao da casa ou nao: a cessao de forma leve mantem a
            # carteira dentro do padrao de diversificacao; a de excecao nao.
            # Guardado por periodo porque a distincao so e util se a curva
            # puder ser refeita sem ela.
            "carteira_de_excecao": str(
                (optimized.get("shape_cession") or {}).get("nivel") or ""
            ) == "excecao",
            # So chega aqui recusada com `medir_rascunho` (sensibilidade).
            "publicacao_recusada": not publicavel,
            "caixa": False,
            "motivo_caixa": None,
        })
        if cessao:
            concession_periods += 1
        turnovers.append(turn)
        # Rank e carteira precisam falar do mesmo universo: com readmissão, o
        # `eligible` estrito não contém os fundos que a carteira comprou, e a
        # estabilidade de ranking saía medida sobre uma lista que não decidiu a
        # carteira. Score ausente não recebe zero — zero seria punição inventada.
        universo_do_rank = {
            str(row["ticker"]): float(row["type_score"]) for row in eligible
        }
        for item in optimized["items"]:
            ticker = str(item.get("ticker") or "")
            if ticker and ticker not in universo_do_rank and (
                    item.get("type_score") is not None):
                universo_do_rank[ticker] = float(item["type_score"])
        ranks.append(pd.Series(universo_do_rank, dtype=float))
        previous = weights

    failure_summary: dict[str, int] = {}
    for failure in optimizer_failures:
        key = f"{failure.get('failure_stage')}|{failure.get('reason')}"
        failure_summary[key] = failure_summary.get(key, 0) + 1
    result = pd.DataFrame(observations)
    if result.empty:
        return {
            "status": "blocked",
            "strategy_id": LIVE_PORTFOLIO_STRATEGY_ID,
            "blockers": ["nenhum período PIT elegível para o otimizador robusto"],
            "optimizer_failures": optimizer_failures[:100],
            "optimizer_failure_summary": failure_summary,
            "missing_macro_periods": missing_macro_periods,
        }
    em_caixa = result["caixa"].astype(bool)
    recusada = result["publicacao_recusada"].astype(bool)
    excess = result["portfolio_return"] - result["benchmark_return"]
    excess_bruto = result["portfolio_return_bruto"] - result["benchmark_return"]
    excess_apos_custos = result["portfolio_return_apos_custos"] - result["benchmark_return"]
    stabilities = [
        ranking_stability(left, right)["spearman"]
        for left, right in zip(ranks, ranks[1:])
    ]
    valid_stabilities = [value for value in stabilities if not np.isnan(value)]
    curve = (1.0 + result["portfolio_return"]).cumprod()
    drawdown = curve / curve.cummax() - 1.0
    valid_excess = excess.dropna()
    information_ratio = (
        float(valid_excess.mean() / valid_excess.std(ddof=1) * math.sqrt(12))
        if len(valid_excess) > 1 and valid_excess.std(ddof=1) > 0 else np.nan
    )
    cobertura_media = result["coverage"].astype(float).mean()
    investidos = int((~em_caixa).sum())
    saida = {
        "status": "calculated", "periods": len(result),
        "strategy_id": LIVE_PORTFOLIO_STRATEGY_ID,
        "mean_return": float(result["portfolio_return"].mean()),
        "mean_benchmark": float(result["benchmark_return"].mean()),
        "mean_excess": float(excess.mean()),
        "excess_bootstrap": bootstrap_mean_ci(excess),
        # As três camadas lado a lado: quanto do excesso o custo de giro come
        # e quanto o IR come. O líquido acima é o padrão; estes são a régua.
        "mean_return_bruto": float(result["portfolio_return_bruto"].mean()),
        "mean_return_apos_custos": float(result["portfolio_return_apos_custos"].mean()),
        "mean_excess_bruto": float(excess_bruto.mean()),
        "mean_excess_apos_custos": float(excess_apos_custos.mean()),
        "excess_bootstrap_bruto": bootstrap_mean_ci(excess_bruto),
        "mean_ir_periodo": float(result["ir_periodo"].mean()),
        "ir_calculado": bool(ir_calculado),
        "premissas_liquido": premissas_fii(transaction_cost, slippage),
        # Media so dos periodos investidos (caixa tem cobertura NaN). Todo em
        # caixa nao e cobertura perfeita: sai zero, e o portao bloqueia.
        "mean_coverage": (float(cobertura_media)
                          if np.isfinite(cobertura_media) else 0.0),
        # Mesmo bloco da versao simples. A tela (`views/fiis.py`, aviso
        # `fii.backtest_saida_de_campo`) ja o lia, mas so a versao simples o
        # produzia -- e ela nao roda em producao, entao o aviso nunca aparecia.
        "saida_de_campo": {
            "peso_ausente_medio": float(result["peso_ausente"].mean()),
            "peso_ausente_maximo": float(result["peso_ausente"].max()),
            "periodos_com_ausencia": int((result["peso_ausente"] > 1e-9).sum()),
            "periodos": int(len(result)),
        },
        "annualized_turnover": float(np.mean(turnovers) * 12),
        "rank_stability": float(np.mean(valid_stabilities)) if valid_stabilities else np.nan,
        "verified_snapshot_fraction": (
            verified_snapshots / used_snapshots if used_snapshots else 0.0
        ),
        "return_observation_coverage": (
            float(np.mean(weighted_return_coverages))
            if weighted_return_coverages else 0.0
        ),
        "max_drawdown": float(drawdown.min()),
        "information_ratio": information_ratio,
        # A carteira de excecao entra na curva de manchete acima -- ela foi
        # entregue, e omiti-la seria medir uma estrategia que ninguem correu.
        # Mas ela nao esta sob o padrao de diversificacao da casa, entao o
        # desempenho das duas coortes sai separado. Se a de excecao rende
        # mais, o premio esta vindo da concentracao, e isso precisa aparecer.
        "curvas_por_padrao": {
            "padrao_da_casa": _metricas_da_coorte(
                result[~result["carteira_de_excecao"].astype(bool) & ~em_caixa]),
            "excecao": _metricas_da_coorte(
                result[result["carteira_de_excecao"].astype(bool)]),
        },
        # Portao de transparencia: a manchete e publicavel + caixa. As coortes
        # separam o que rendeu a carteira publicada do que rendeu o caixa; o
        # rascunho medido so existe com `medir_rascunho` (sensibilidade).
        "curvas_por_publicacao": {
            "publicavel": _metricas_da_coorte(result[~em_caixa & ~recusada]),
            "caixa": _metricas_da_coorte(result[em_caixa]),
            "rascunho_medido": _metricas_da_coorte(result[~em_caixa & recusada]),
        },
        "cash_periods": int(em_caixa.sum()),
        "cash_periods_publication_refused": int((em_caixa & recusada).sum()),
        "medir_rascunho": bool(medir_rascunho),
        "optimizer_attempted_periods": attempted_optimizer,
        "optimizer_successful_periods": successful_optimizer,
        "optimizer_feasible_fraction": (
            successful_optimizer / attempted_optimizer if attempted_optimizer else 0.0
        ),
        "optimizer_input_ready_fraction": (
            attempted_optimizer / (attempted_optimizer + optimizer_input_skips)
            if attempted_optimizer + optimizer_input_skips else 0.0
        ),
        "optimizer_input_skipped_periods": optimizer_input_skips,
        # Diagnostico, nao portao: a fracao viavel ja cobra esses periodos. O
        # que isto acrescenta e a causa, para a limitacao publicada nao culpar
        # o otimizador por uma recusa que foi de transparencia.
        "publication_refused_periods": list(publicacao_recusada),
        "constraint_violation_periods": constraint_violation_periods,
        "constraint_violation_details": constraint_violation_details,
        "concession_periods": concession_periods,
        # Sobre os periodos investidos: caixa nao tem carteira para ceder.
        "concession_period_fraction": (
            concession_periods / investidos if investidos else 0.0
        ),
        # Períodos que cederam proteção e foram descartados por falta de
        # retorno. Ficam fora da fração porque não estão no denominador, mas
        # não somem: cessão que desaparece do relatório é proteção cedida
        # apresentada como ausência de risco.
        "concession_periods_discarded": max(
            len(concession_details) - concession_periods, 0
        ),
        "concession_details": concession_details[:100],
        "mean_correlation_coverage": (
            float(np.mean(correlation_coverages)) if correlation_coverages else 0.0
        ),
        "mean_eligible_assets": float(np.mean(eligible_counts)) if eligible_counts else 0.0,
        "missing_macro_periods": missing_macro_periods,
        # Limite alto o suficiente para não ocultar falhas do solver atrás dos
        # períodos iniciais sem universo elegível, mas ainda bounded no JSON.
        "optimizer_failures": optimizer_failures[:100],
        "optimizer_failure_summary": failure_summary,
        "correlation_penalty": float(correlation_penalty),
        "correlation_lookback_months": int(correlation_lookback_months),
        "observations": result.to_dict("records"),
    }
    if not medir_rascunho and publicacao_recusada:
        # Sensibilidade: a mesma cadeia, com o rascunho recusado medido pelos
        # proprios pesos. Nao e manchete -- ninguem recebeu essa carteira --,
        # mas mostra quanto o portao de transparencia custou ou poupou.
        rascunho = robust_optimizer_point_in_time_backtest(
            snapshots, returns, benchmark, macro_scenarios,
            top_n=top_n, transaction_cost=transaction_cost, slippage=slippage,
            correlation_penalty=correlation_penalty,
            min_return_coverage=min_return_coverage,
            correlation_lookback_months=correlation_lookback_months,
            correlation_min_months=correlation_min_months,
            medir_rascunho=True,
        )
        if rascunho.get("status") == "calculated":
            saida["sensibilidade_rascunho"] = {
                "periods": int(rascunho["periods"]),
                "mean_return": rascunho["mean_return"],
                "mean_excess": rascunho["mean_excess"],
                "excess_bootstrap": rascunho["excess_bootstrap"],
                "max_drawdown": rascunho["max_drawdown"],
                "periodos_com_rascunho": list(publicacao_recusada),
            }
    return saida


def evaluate_regime_performance(observations: Iterable[dict], macro_regimes: pd.DataFrame) -> dict[str, Any]:
    """Separa excesso de retorno por regimes brasileiros previamente classificados.

    `macro_regimes` contém `date` e `regime` (por exemplo high_real_rate,
    easing, inflation_stress e stress). A classificação deve usar somente
    informações disponíveis na própria data.
    """
    returns = pd.DataFrame(list(observations))
    if returns.empty or not {"date", "portfolio_return", "benchmark_return"}.issubset(returns.columns):
        return {}
    if macro_regimes.empty or not {"date", "regime"}.issubset(macro_regimes.columns):
        return {}
    returns["date"] = pd.to_datetime(returns["date"])
    regimes = macro_regimes[["date", "regime"]].copy()
    regimes["date"] = pd.to_datetime(regimes["date"])
    merged = returns.merge(regimes, on="date", how="inner")
    output: dict[str, Any] = {}
    for regime, group in merged.groupby("regime"):
        excess = group["portfolio_return"] - group["benchmark_return"]
        output[str(regime)] = {
            "periods": len(group), "mean_return": float(group["portfolio_return"].mean()),
            "mean_excess": float(excess.mean()), "excess_bootstrap": bootstrap_mean_ci(excess),
            "worst_period": float(group["portfolio_return"].min()),
        }
    return output


def bloqueio_por_excesso(backtest: dict[str, Any]) -> list[str]:
    """O bloqueador do portão quando a carteira não ganhou do IFIX na média.

    Lista vazia quando o excesso médio líquido passa de ``PISO_EXCESSO_MEDIO``.
    Sem média medida também bloqueia: ausência de número não é ganho. A
    leitura é a mesma do card e do motor (`leitura_do_excesso`), para o
    portão e a tela não discordarem sobre o número.
    """
    if (backtest or {}).get("status") != "calculated":
        # Backtest que não calculou já bloqueia pelo próprio motivo; somar
        # "excesso indisponível" só repetiria a causa com outro nome.
        return []
    media = leitura_do_excesso(backtest)["media"]
    if media is None:
        return ["excesso médio sobre o IFIX indisponível: sem ele não há como "
                "afirmar que a carteira ganha do índice"]
    if media <= PISO_EXCESSO_MEDIO:
        return [f"carteira não ganha do IFIX: excesso médio líquido de "
                f"{_pp(media)} p.p./mês no walk-forward"]
    return []


def validate_methodology(backtest: dict[str, Any], regime_results: dict[str, Any], *,
                         thresholds: ValidationThresholds | None = None) -> dict[str, Any]:
    thresholds = thresholds or ValidationThresholds()
    blockers: list[str] = []
    if backtest.get("status") != "calculated":
        blockers.extend(backtest.get("blockers") or ["backtest PIT não calculado"])
    if int(backtest.get("periods") or 0) < thresholds.min_periods:
        blockers.append(f"histórico inferior a {thresholds.min_periods} períodos")
    if float(backtest.get("mean_coverage") or 0) < thresholds.min_universe_coverage:
        blockers.append("cobertura histórica insuficiente")
    stability = float(backtest.get("rank_stability") or 0)
    if pd.isna(stability) or stability < thresholds.min_rank_stability:
        blockers.append("ranking instável entre rebalanceamentos")
    if float(backtest.get("annualized_turnover") or np.inf) > thresholds.max_annual_turnover:
        blockers.append("turnover anual excessivo após custos")
    if float(backtest.get("verified_snapshot_fraction") or 0) < thresholds.min_verified_snapshot_fraction:
        blockers.append("verified snapshot fraction below threshold")
    if float(backtest.get("return_observation_coverage") or 0) < thresholds.min_return_observation_coverage:
        blockers.append("return-series observation coverage below threshold")
    if "optimizer_feasible_fraction" in backtest and (
        float(backtest.get("optimizer_feasible_fraction") or 0)
        < thresholds.min_optimizer_feasible_fraction
    ):
        recusados = list(backtest.get("publication_refused_periods") or ())
        vazios = (int(backtest.get("optimizer_attempted_periods") or 0)
                  - int(backtest.get("optimizer_successful_periods") or 0)
                  - len(recusados))
        if recusados and len(recusados) >= max(vazios, 1):
            # A causa dominante nao e o otimizador: ele montou carteira e o
            # portao de transparencia recusou publicar. Dizer "inviavel" aqui
            # manda procurar defeito no motor errado.
            blockers.append(
                "períodos históricos demais sem carteira publicável — "
                f"{len(recusados)} deles com carteira montada e publicação "
                "recusada por cobertura de dimensão obrigatória ausente "
                f"({recusados[0]} a {recusados[-1]})"
            )
        else:
            blockers.append(
                "otimizador robusto inviável em períodos históricos demais")
    if int(backtest.get("constraint_violation_periods") or 0) > 0:
        blockers.append("otimizador produziu violações de restrições no walk-forward")
    if "mean_correlation_coverage" in backtest and (
        float(backtest.get("mean_correlation_coverage") or 0)
        < thresholds.min_correlation_coverage
    ):
        blockers.append("cobertura histórica de correlação insuficiente")
    valid_regimes = sum(bool(value and value.get("periods")) for value in regime_results.values())
    if valid_regimes < thresholds.min_regimes:
        blockers.append("cobertura insuficiente de regimes macroeconômicos")
    ci = backtest.get("excess_bootstrap") or {}
    if pd.isna(ci.get("lower", np.nan)):
        blockers.append("intervalo bootstrap indisponível")
    blockers.extend(bloqueio_por_excesso(backtest))
    return {"status": "passed" if not blockers else "blocked", "blockers": blockers,
            "thresholds": thresholds.__dict__, "backtest": backtest, "regimes": regime_results}

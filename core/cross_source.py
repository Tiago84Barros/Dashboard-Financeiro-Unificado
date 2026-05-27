"""
core/cross_source.py — validação cruzada de indicadores fundamentalistas.

Implementa a recomendação A6 (parcial — MVP) do parecer da banca
examinadora (2026-05-23): detectar divergências entre múltiplas fontes
do mesmo indicador (Fundamentus vs Status Invest vs DRE) para flagear
dados potencialmente contaminados ANTES do scoring.

MVP (esta versão):
  • Função compare_indicators() que recebe dict {fonte: valor} e
    retorna flag de divergência + métrica de magnitude
  • Função batch_validate() que aplica em lote sobre múltiplos tickers
  • Política de resolução: mediana das fontes válidas (robusta a outlier)

Pendente (versão completa, ~25h adicionais):
  • Integração com cache de scrapers (status_invest.py + fundamentus.py)
  • UI dedicada de auditoria cross-source
  • Histórico de divergências por ticker (timeline)
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


# Thresholds de divergência por tipo de indicador (em fração relativa)
# Calibrados empiricamente — indicadores de magnitude grande (P/L) toleram
# mais divergência absoluta que percentuais (ROE).
DIVERGENCE_THRESHOLDS: dict[str, float] = {
    "ROE":                0.15,  # 15% de diferença = flag
    "ROIC":               0.15,
    "Margem_Liquida":     0.20,
    "Margem_Operacional": 0.20,
    "P/L":                0.25,
    "P/VP":               0.20,
    "DY":                 0.25,
    "EV_EBIT":            0.25,
    "Endividamento_Total": 0.30,
    "Liquidez_Corrente":  0.20,
    "_default":           0.30,
}

HISTORY_FILE_DEFAULT = "data/cache/cross_source_history.jsonl"


@dataclass
class CrossSourceFlag:
    """Resultado da comparação cross-source para um indicador."""
    ticker:        str
    indicador:     str
    valores:       dict[str, float]   # {fonte: valor}
    n_fontes:      int
    mediana:       float | None
    spread_abs:    float
    spread_rel:    float
    divergente:    bool
    severidade:    str   # 'ok' | 'warn' | 'critical'


def _spread_relativo(valores: list[float]) -> float:
    """Calcula spread relativo = (max - min) / |mediana|."""
    if not valores or len(valores) < 2:
        return 0.0
    import statistics
    med = statistics.median(valores)
    if abs(med) < 1e-9:
        return 0.0
    return (max(valores) - min(valores)) / abs(med)


def compare_indicators(
    ticker:     str,
    indicador:  str,
    valores:    dict[str, float | None],
    threshold:  float | None = None,
) -> CrossSourceFlag:
    """Compara valores do mesmo indicador entre fontes diferentes.

    Args:
      ticker: símbolo da empresa
      indicador: nome canônico do indicador (ex: 'ROE', 'P/L')
      valores: {fonte: valor} — fontes podem ser 'fundamentus',
               'status_invest', 'dre', 'manual', etc.
      threshold: spread relativo acima do qual flagear como divergente.
                 None → usa DIVERGENCE_THRESHOLDS[indicador].

    Returns:
      CrossSourceFlag com severidade categorizada:
        ok        — < threshold (ou < 2 fontes válidas, undetermined)
        warn      — [threshold, 2×threshold)
        critical  — ≥ 2×threshold
    """
    # Filtra valores válidos
    val_clean = {f: float(v) for f, v in valores.items()
                 if v is not None and v == v}  # NaN-safe

    if len(val_clean) < 2:
        # Não dá pra comparar com 1 fonte só — undetermined
        med = next(iter(val_clean.values())) if val_clean else None
        return CrossSourceFlag(
            ticker=ticker, indicador=indicador, valores=val_clean,
            n_fontes=len(val_clean), mediana=med,
            spread_abs=0.0, spread_rel=0.0,
            divergente=False, severidade="ok",
        )

    import statistics
    vals_list = list(val_clean.values())
    med = statistics.median(vals_list)
    spread_abs = max(vals_list) - min(vals_list)
    spread_rel = _spread_relativo(vals_list)

    thr = threshold if threshold is not None else \
          DIVERGENCE_THRESHOLDS.get(indicador, DIVERGENCE_THRESHOLDS["_default"])

    if spread_rel < thr:
        sev = "ok"
        div = False
    elif spread_rel < 2 * thr:
        sev = "warn"
        div = True
    else:
        sev = "critical"
        div = True

    return CrossSourceFlag(
        ticker=ticker, indicador=indicador, valores=val_clean,
        n_fontes=len(val_clean), mediana=med,
        spread_abs=spread_abs, spread_rel=spread_rel,
        divergente=div, severidade=sev,
    )


def batch_validate(
    dados_por_ticker: dict[str, dict[str, dict[str, float]]],
    indicadores:      Iterable[str] | None = None,
) -> list[CrossSourceFlag]:
    """Aplica compare_indicators em lote.

    Args:
      dados_por_ticker: {ticker: {indicador: {fonte: valor}}}
      indicadores: lista para filtrar; None = todos encontrados.

    Returns:
      Lista de CrossSourceFlag (apenas divergentes warn/critical).
    """
    flags: list[CrossSourceFlag] = []
    for tk, por_ind in dados_por_ticker.items():
        for ind, por_fonte in por_ind.items():
            if indicadores is not None and ind not in indicadores:
                continue
            flag = compare_indicators(tk, ind, por_fonte)
            if flag.divergente:
                flags.append(flag)
    # Ordena por severidade (critical primeiro) e spread relativo
    flags.sort(key=lambda f: (f.severidade != "critical", -f.spread_rel))
    return flags


def consensus_value(valores: dict[str, float | None]) -> float | None:
    """Política de resolução: mediana das fontes válidas (robusta a outliers).

    Se há discrepância > 50%, retorna None (preferível ignorar a usar
    valor potencialmente contaminado).
    """
    val_clean = [float(v) for v in valores.values()
                 if v is not None and v == v]
    if not val_clean:
        return None
    if len(val_clean) == 1:
        return val_clean[0]
    spread = _spread_relativo(val_clean)
    if spread > 0.50:
        return None  # divergência grande — prefere ignorar
    import statistics
    return statistics.median(val_clean)


def resumo_validacao(flags: list[CrossSourceFlag]) -> dict:
    """Sumariza um batch de flags para exibir em dashboard."""
    if not flags:
        return {"total": 0, "critical": 0, "warn": 0, "tickers_afetados": 0}
    tickers = {f.ticker for f in flags}
    n_crit = sum(1 for f in flags if f.severidade == "critical")
    n_warn = sum(1 for f in flags if f.severidade == "warn")
    return {
        "total":             len(flags),
        "critical":          n_crit,
        "warn":              n_warn,
        "tickers_afetados":  len(tickers),
        "tickers_critical":  sorted({f.ticker for f in flags
                                      if f.severidade == "critical"}),
    }


def _jsonable(value):
    """Converts pandas/numpy-ish scalars into JSON-safe values."""
    if value is None:
        return None
    try:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
    except TypeError:
        pass
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def flag_to_dict(flag: CrossSourceFlag) -> dict:
    """Serializes a CrossSourceFlag for history/cache storage."""
    return {
        "Ticker": flag.ticker,
        "Indicador": flag.indicador,
        "Valores": dict(flag.valores),
        "N Fontes": flag.n_fontes,
        "Consenso": flag.mediana,
        "Spread Abs": flag.spread_abs,
        "Spread %": flag.spread_rel * 100.0,
        "Divergente": flag.divergente,
        "Severidade": flag.severidade,
    }


def append_validation_history(
    rows: Iterable[dict | CrossSourceFlag],
    path: Path | str = HISTORY_FILE_DEFAULT,
    run_meta: dict | None = None,
) -> int:
    """Appends cross-source validation rows to a JSONL history file."""
    records = list(rows)
    if not records:
        return 0
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = _jsonable(run_meta or {})
    count = 0
    with p.open("a", encoding="utf-8") as f:
        for item in records:
            record = flag_to_dict(item) if isinstance(item, CrossSourceFlag) else dict(item)
            record["run_ts"] = ts
            if meta:
                record["run_meta"] = meta
            f.write(json.dumps(_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def load_validation_history(
    path: Path | str = HISTORY_FILE_DEFAULT,
    limit: int = 500,
) -> list[dict]:
    """Loads recent cross-source validation history, newest first."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    selected = lines[-max(int(limit), 1):]
    records: list[dict] = []
    for line in reversed(selected):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def summarize_validation_history(records: list[dict]) -> dict:
    """Summarizes cached validation rows."""
    if not records:
        return {"total": 0, "critical": 0, "warn": 0, "tickers_afetados": 0}
    tickers = {str(r.get("Ticker", "")) for r in records if r.get("Ticker")}
    return {
        "total": len(records),
        "critical": sum(1 for r in records if r.get("Severidade") == "critical"),
        "warn": sum(1 for r in records if r.get("Severidade") == "warn"),
        "tickers_afetados": len(tickers),
    }

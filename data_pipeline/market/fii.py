"""
data_pipeline/market/fii.py
Núcleo PURO de FIIs (fundos imobiliários): normalização do payload BRAPI,
métricas de seleção (DY 12m, P/VP, liquidez) e ranking de "bons FIIs".

FII não tem DRE/ROE — a seleção usa rendimento, valor patrimonial e liquidez.
Sem rede e sem banco (100% testável). O sector "Fundos Imobiliários" distingue
FII de ETF (ambos vêm como type='fund' na lista da BRAPI).
"""
from __future__ import annotations

import datetime as _dt

FII_SECTOR = "Fundos Imobiliários"


def _f(v):
    try:
        x = float(v)
        return x if x == x and x not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _as_date(v):
    if v is None:
        return None
    s = str(v)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s[:24], fmt).date()
        except ValueError:
            continue
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except Exception:
        return None


def is_fii(quote: dict) -> bool:
    """True se o ativo é FII (e não ETF/outro fundo). Usa o setor do perfil."""
    sector = ((quote or {}).get("summaryProfile") or {}).get("sector") or ""
    return str(sector).strip().lower() == FII_SECTOR.lower()


def dy_12m(cash_dividends: list, price: float, ref_date: _dt.date) -> float | None:
    """Dividend yield 12m = soma dos rendimentos pagos nos últimos 366 dias ÷ preço."""
    p = _f(price)
    if not p or p <= 0 or not cash_dividends:
        return None
    total = 0.0
    for it in cash_dividends:
        d = _as_date(it.get("paymentDate") or it.get("lastDatePrior"))
        r = _f(it.get("rate"))
        if d is not None and r is not None and 0 <= (ref_date - d).days <= 366:
            total += r
    return (total / p) if total > 0 else None


def liquidez_diaria(historical: list, n: int = 60) -> float | None:
    """Mediana de (close × volume) dos últimos n pregões — R$/dia negociados."""
    vals = []
    for it in (historical or []):
        c, v = _f(it.get("close")), _f(it.get("volume"))
        if c and v:
            vals.append(c * v)
    if not vals:
        return None
    vals = sorted(vals[-n:])
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2.0


def segmento(quote: dict) -> str:
    prof = (quote or {}).get("summaryProfile") or {}
    return str(prof.get("industry") or prof.get("category") or "Multicategoria").strip()


def compute_fii(quote: dict, ref_date: _dt.date) -> dict | None:
    """Extrai as métricas de seleção de um FII. None se não for FII."""
    if not is_fii(quote):
        return None
    tk = str((quote or {}).get("symbol") or "").upper().replace(".SA", "")
    if not tk:
        return None
    price = _f(quote.get("regularMarketPrice"))
    dks = (quote or {}).get("defaultKeyStatistics") or {}
    divs = ((quote or {}).get("dividendsData") or {}).get("cashDividends") or []
    return {
        "ticker": tk,
        "name": str(quote.get("longName") or quote.get("shortName") or tk)[:200],
        "segmento": segmento(quote),
        "price": price,
        "pvp": _f(dks.get("priceToBook")),
        "dy_12m": dy_12m(divs, price, ref_date),
        "liquidez_diaria": liquidez_diaria(quote.get("historicalDataPrice") or []),
    }


# ── Ranking ──────────────────────────────────────────────────────────────────

def _percentile(values: list[float], higher_better: bool) -> dict[int, float]:
    """Percentil 0..1 por posição (índice → score). Empdoes recebem média."""
    idx = [i for i, v in enumerate(values) if v is not None]
    if not idx:
        return {}
    order = sorted(idx, key=lambda i: values[i], reverse=not higher_better)
    out: dict[int, float] = {}
    n = len(order)
    for rank, i in enumerate(order):
        out[i] = 1.0 if n == 1 else rank / (n - 1)
    return out


# pesos default do score "bons FIIs" (somam 1.0)
DEFAULT_WEIGHTS = {"dy_12m": 0.45, "pvp": 0.30, "liquidez_diaria": 0.25}


def rank_fiis(rows: list[dict], *, weights: dict | None = None,
              pvp_max: float | None = 1.30, liq_min: float | None = 200_000.0,
              dy_max: float = 0.30) -> list[dict]:
    """
    Filtra e rankeia FIIs. DY↑ e liquidez↑ (maior melhor), P/VP↓ (menor melhor).
    Filtros: P/VP<=pvp_max, liquidez>=liq_min, DY<=dy_max (descarta DY absurdo).
    Retorna lista ordenada por score desc, com 'score' (0..100) e percentis.
    """
    weights = weights or DEFAULT_WEIGHTS
    elig = []
    for r in rows:
        if r is None or r.get("price") is None:
            continue
        dy, pvp, liq = r.get("dy_12m"), r.get("pvp"), r.get("liquidez_diaria")
        if dy is not None and dy > dy_max:
            continue
        if pvp_max is not None and (pvp is None or pvp > pvp_max):
            continue
        if liq_min is not None and (liq is None or liq < liq_min):
            continue
        elig.append(r)
    if not elig:
        return []

    pct_dy = _percentile([r.get("dy_12m") for r in elig], higher_better=True)
    pct_pvp = _percentile([r.get("pvp") for r in elig], higher_better=False)
    pct_liq = _percentile([r.get("liquidez_diaria") for r in elig], higher_better=True)

    out = []
    for i, r in enumerate(elig):
        parts = {"dy_12m": pct_dy.get(i), "pvp": pct_pvp.get(i),
                 "liquidez_diaria": pct_liq.get(i)}
        num = sum(weights[k] * v for k, v in parts.items() if v is not None)
        den = sum(weights[k] for k, v in parts.items() if v is not None)
        score = (num / den) if den else 0.0
        out.append({**r, "score": round(score * 100, 1),
                    "pct_dy": parts["dy_12m"], "pct_pvp": parts["pvp"],
                    "pct_liq": parts["liquidez_diaria"]})
    out.sort(key=lambda r: r["score"], reverse=True)
    return out

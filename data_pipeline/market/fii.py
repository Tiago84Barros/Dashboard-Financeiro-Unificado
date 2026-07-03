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
    import re as _re
    cnpj = _re.sub(r"\D", "", str((quote.get("summaryProfile") or {}).get("cnpj") or "")) or None
    return {
        "ticker": tk,
        "cnpj": cnpj,
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


# ── Métricas de série (crescimento da cota + drawdown) ────────────────────────

def price_metrics(prices: list) -> dict:
    """
    A partir de uma série [(date, close)] (retorno total, adjusted_close):
      cagr          — crescimento anualizado da cota (None se janela < ~6m);
      max_drawdown  — pior queda pico→vale (negativo, ex.: -0.32 = -32%);
      anos          — janela em anos.
    Puro e testável (sem rede/banco).
    """
    import pandas as pd
    if not prices or len(prices) < 6:
        return {"cagr": None, "max_drawdown": None, "anos": 0.0}
    s = pd.Series({pd.to_datetime(d): float(c) for d, c in prices if c is not None})
    s = s[s > 0].sort_index()
    if len(s) < 6:
        return {"cagr": None, "max_drawdown": None, "anos": 0.0}
    anos = (s.index[-1] - s.index[0]).days / 365.25
    total = s.iloc[-1] / s.iloc[0] - 1.0
    cagr = ((1 + total) ** (1 / anos) - 1) if anos > 0.5 else None
    dd = float((s / s.cummax() - 1.0).min())          # pior queda (negativo)
    return {"cagr": round(cagr, 4) if cagr is not None else None,
            "max_drawdown": round(dd, 4), "anos": round(anos, 1)}


# ── Carteira-modelo (diversificada) ───────────────────────────────────────────

def _cap_weights(w: list[float], cap: float) -> list[float]:
    """Limita cada peso a `cap`, redistribuindo o excedente proporcionalmente aos
    demais; renormaliza no fim."""
    w = list(w)
    for _ in range(20):
        over = [i for i, x in enumerate(w) if x > cap + 1e-12]
        if not over:
            break
        excess = sum(w[i] - cap for i in over)
        for i in over:
            w[i] = cap
        free = [i for i in range(len(w)) if i not in over]
        fsum = sum(w[i] for i in free)
        if fsum <= 0:
            break
        for i in free:
            w[i] += excess * w[i] / fsum
    s = sum(w)
    return [x / s for x in w] if s else w


def build_portfolio(rows: list[dict], *, n_max: int = 10, max_weight: float = 0.20,
                    max_tipo_frac: float = 0.50, liq_min: float = 200_000.0,
                    min_por_tipo: int = 0) -> list[dict]:
    """
    Monta a carteira-modelo diversificada por tipo: teto de max_tipo_frac do nº de
    FIIs por tipo e, opcionalmente, um PISO de `min_por_tipo` FIIs de cada tipo
    presente (garante mix — ex.: tijolo + papel + FoF descorrelacionados). Pesa
    proporcional ao score com teto max_weight por FII.
    """
    elig = [r for r in rows if r.get("score") is not None
            and (r.get("liquidez_diaria") or 0) >= liq_min]
    elig.sort(key=lambda r: float(r["score"]), reverse=True)   # melhores primeiro
    cap_tipo = max(1, int(round(n_max * max_tipo_frac)))
    sel, tipo_count, chosen = [], {}, set()

    def _take(r):
        sel.append(r)
        chosen.add(id(r))
        tp = r.get("tipo") or "?"
        tipo_count[tp] = tipo_count.get(tp, 0) + 1

    # 1) piso por tipo: os melhores de cada tipo presente entram primeiro
    if min_por_tipo > 0:
        by_tipo: dict = {}
        for r in elig:
            by_tipo.setdefault(r.get("tipo") or "?", []).append(r)
        for lst in by_tipo.values():
            for r in lst[:min_por_tipo]:
                if len(sel) < n_max and id(r) not in chosen:
                    _take(r)
    # 2) completa por score, respeitando o teto por tipo
    for r in elig:
        if len(sel) >= n_max:
            break
        if id(r) in chosen:
            continue
        tp = r.get("tipo") or "?"
        if tipo_count.get(tp, 0) >= cap_tipo:
            continue
        _take(r)
    if not sel:
        return []
    sel.sort(key=lambda r: float(r["score"]), reverse=True)
    scores = [max(float(r["score"]), 1e-9) for r in sel]
    tot = sum(scores)
    w = _cap_weights([s / tot for s in scores], max_weight)
    return [{"ticker": r["ticker"], "peso": round(wi, 4), "score": r["score"],
             "tipo": r.get("tipo"), "segmento": r.get("segmento"),
             "dy_12m": r.get("dy_12m"), "pvp": r.get("pvp")}
            for r, wi in zip(sel, w)]


# ── Diversificação (nº efetivo + curva risco × nº de fundos) ──────────────────

def effective_n(weights) -> float | None:
    """
    Número EFETIVO de fundos = 1 / Σ(pesoᵢ²) (inverso do índice HHI). Mede a
    diversificação real: 10 fundos com um pesando 40% dão N_ef bem < 10. None se
    não houver pesos. Puro/testável.
    """
    vals = list(weights.values()) if isinstance(weights, dict) else list(weights)
    ws = [float(w) for w in vals if w is not None]
    s = sum(w * w for w in ws)
    return round(1.0 / s, 2) if s > 0 else None


def risk_curve(returns, weights: dict) -> list[dict]:
    """
    Curva risco × nº de fundos: adiciona fundos por peso desc, renormaliza e mede
    a volatilidade ANUALIZADA (std mensal × √12) da carteira parcial. Mostra onde
    a diversificação para de reduzir risco (a curva achata).

    returns: DataFrame (linhas=meses, colunas=tickers) de retornos mensais;
    weights: {ticker: peso}. Retorna [{n, vol}] com n=1..N.
    """
    cols = list(getattr(returns, "columns", []))
    order = [t for t in sorted(weights, key=lambda t: -weights[t]) if t in cols]
    out: list[dict] = []
    for k in range(1, len(order) + 1):
        sub = order[:k]
        r = returns[sub].dropna(how="any")     # janela comum DESSE subconjunto
        if len(r) < 3:
            continue
        tot = sum(weights[t] for t in sub) or 1.0
        w = [weights[t] / tot for t in sub]
        port = sum(r[t] * wi for t, wi in zip(sub, w))
        vol = float(port.std(ddof=0) * (12 ** 0.5))
        out.append({"n": k, "vol": round(vol, 4), "meses": int(len(r))})
    return out


# ── Backtest (retorno total: preço + proventos reinvestidos) ──────────────────

def backtest(weights: dict, price_hist: dict, div_hist: dict | None = None,
             benchmark: list | None = None, benchmark_nome: str = "IFIX"):
    """
    Retorno total (preço + proventos reinvestidos), buy-and-hold com pesos fixos.
    weights: {ticker: peso}; price_hist: {ticker: [(date, close)]};
    div_hist: {ticker: [(date, amount)]}; benchmark: [(date, valor)] (índice de
    retorno total, ex.: IFIX) — sobreposto na MESMA janela, base 100.
    Retorna (serie, metricas):
      serie  = DataFrame [Data, Carteira, (benchmark_nome)] (índice base 100);
      metricas = {retorno_total, cagr, anos, n_ativos, bench_retorno}.
    """
    import pandas as pd
    div_hist = div_hist or {}
    tr_cols = {}
    for tk in weights:
        ph = price_hist.get(tk) or []
        if len(ph) < 2:
            continue
        p = pd.DataFrame(ph, columns=["Data", "close"]).dropna()
        p["Data"] = pd.to_datetime(p["Data"])
        p = p.sort_values("Data").set_index("Data")["close"]
        p = pd.to_numeric(p, errors="coerce").dropna()
        # mensal: preço de fim de mês (ffill p/ meses sem pregão na amostra) +
        # proventos somados no mês (alinha por mês, não por data exata).
        p_m = p.resample("ME").last().ffill()
        dd = pd.DataFrame(div_hist.get(tk) or [], columns=["Data", "amount"])
        if not dd.empty:
            dd["Data"] = pd.to_datetime(dd["Data"])
            d_m = (pd.to_numeric(dd.set_index("Data")["amount"], errors="coerce")
                   .resample("ME").sum().reindex(p_m.index).fillna(0.0))
        else:
            d_m = pd.Series(0.0, index=p_m.index)
        tr = ((p_m + d_m) / p_m.shift(1)).fillna(1.0).cumprod()
        if tr.iloc[0]:
            tr_cols[tk] = tr / tr.iloc[0]
    if not tr_cols:
        return pd.DataFrame(columns=["Data", "Carteira"]), {
            "retorno_total": None, "cagr": None, "anos": 0, "n_ativos": 0}
    mat = pd.DataFrame(tr_cols).ffill().dropna()
    if mat.empty:
        return pd.DataFrame(columns=["Data", "Carteira"]), {
            "retorno_total": None, "cagr": None, "anos": 0, "n_ativos": 0}
    pesos = {tk: weights[tk] for tk in mat.columns}
    wsum = sum(pesos.values()) or 1.0
    carteira = sum(mat[tk] * (pesos[tk] / wsum) for tk in mat.columns)
    # rebase p/ 100 no início da JANELA COMUM (todos os ativos presentes)
    carteira = carteira / carteira.iloc[0] * 100.0
    out = carteira.rename("Carteira").to_frame()

    # benchmark (índice de retorno total, ex.: IFIX): alinha à janela e base 100
    bench_ret = None
    if benchmark:
        bdf = pd.DataFrame(benchmark, columns=["Data", "v"]).dropna()
        if not bdf.empty:
            bdf["Data"] = pd.to_datetime(bdf["Data"])
            bser = (pd.to_numeric(bdf.set_index("Data")["v"], errors="coerce")
                    .resample("ME").last().ffill().reindex(out.index).ffill())
            if bser.notna().any() and bser.dropna().iloc[0]:
                bser = bser / bser.dropna().iloc[0] * 100.0
                out[benchmark_nome] = bser
                bench_ret = round(bser.iloc[-1] / 100.0 - 1.0, 4)

    serie = out.reset_index()
    anos = (serie["Data"].iloc[-1] - serie["Data"].iloc[0]).days / 365.25 if len(serie) > 1 else 0
    ret = (carteira.iloc[-1] / 100.0) - 1.0
    cagr = ((1 + ret) ** (1 / anos) - 1) if anos > 0.5 else None
    return serie, {"retorno_total": round(ret, 4),
                   "cagr": round(cagr, 4) if cagr is not None else None,
                   "anos": round(anos, 1), "n_ativos": len(mat.columns),
                   "bench_retorno": bench_ret}

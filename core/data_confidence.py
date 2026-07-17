"""
core/data_confidence.py
Índice de confiança dos dados — honesto e por ticker.

Substitui, para efeito de EXIBIÇÃO, o `confidence_score` de market.calculated_metrics,
que é constante por método (85 exato, 80 brapi, 60 aproximado) e NÃO discrimina um
ticker bem coberto de um mal coberto. Aqui o score é derivado de sinais reais e
verificáveis, sem tocar naquela coluna (ela tem semântica de filtro >=80 no caminho
point-in-time de core.market_read — repurposá-la excluiria tickers do backtest).

Três pilares, cada um em [0,1], combinados por pesos fixos e transparentes:
  • Cobertura   — quanto das métricas-chave/demonstrações/preço o ticker tem;
  • Frescor     — quão recente é o último preço e a última demonstração anual;
  • Integridade — ausência de flags abertas (warn/error) em market.data_quality_logs.

Funções puras (score_ticker, confidence_label, *_factor) são testáveis sem banco;
o IO fica em compute_confidence / confidence_summary.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Métricas-chave do snapshot ttm (mesmo conjunto de core.market_health).
KEY_METRICS = ["ROE", "ROA", "Margem_Liquida", "Margem_Operacional",
               "Endividamento_Total", "Liquidez_Corrente", "DY", "P/L", "P/VP"]

# Pesos dos pilares (somam 1). Explícitos de propósito — é a "metodologia" do score.
W_COBERTURA = 0.45
W_FRESCOR = 0.30
W_INTEGRIDADE = 0.25

# Composição interna da cobertura (soma 1).
_COB_TTM = 0.55       # completude do snapshot ttm (n_key / total)
_COB_ANUAL = 0.30     # recência da demonstração anual
_COB_PRECO = 0.15     # existência de preço recente

# Composição interna do frescor (soma 1).
_FR_PRECO = 0.60
_FR_ANUAL = 0.40

# Faixas do rótulo.
LIMIAR_ALTA = 75.0
LIMIAR_MEDIA = 55.0

# Frescor de preço: fresco até 3 dias, decai linearmente até 0 em 30 dias.
_PRECO_FRESCO_DIAS = 3
_PRECO_VELHO_DIAS = 30

# Penalidade por flag aberta (warn/error) distinta em data_quality_logs.
_PENALIDADE_POR_FLAG = 0.34


def annual_recency_factor(ymax: int | None, current_year: int) -> float:
    """Recência da última demonstração anual em [0,1]. None → 0."""
    if ymax is None:
        return 0.0
    atraso = current_year - int(ymax)
    if atraso <= 1:          # ano corrente ou anterior (fechamento normal ainda pendente)
        return 1.0
    if atraso == 2:
        return 0.6
    if atraso == 3:
        return 0.3
    return 0.1               # demonstração antiga — dado estrutural velho


def price_freshness_factor(dias_preco: int | None) -> float:
    """Frescor do último preço em [0,1]. None → 0; <=3d → 1; 0 em >=30d."""
    if dias_preco is None:
        return 0.0
    d = float(dias_preco)
    if d <= _PRECO_FRESCO_DIAS:
        return 1.0
    if d >= _PRECO_VELHO_DIAS:
        return 0.0
    return max(0.0, 1.0 - (d - _PRECO_FRESCO_DIAS) / (_PRECO_VELHO_DIAS - _PRECO_FRESCO_DIAS))


def confidence_label(score: float) -> str:
    """Rótulo textual da faixa."""
    if score >= LIMIAR_ALTA:
        return "Alta"
    if score >= LIMIAR_MEDIA:
        return "Média"
    return "Baixa"


def score_ticker(signals: dict, current_year: int,
                 key_total: int = len(KEY_METRICS)) -> dict:
    """
    Puro/testável. `signals` por ticker:
      n_key_ttm   — nº de métricas-chave presentes no snapshot ttm
      ymax        — maior ano com demonstração anual (ou None)
      dias_preco  — dias desde o último preço (ou None)
      n_flags     — nº de issue_types warn/error abertas (janela recente)
    Retorna {score(0-100), label, cobertura, frescor, integridade} — pilares em %.
    """
    key_total = max(1, int(key_total))
    n_key = min(int(signals.get("n_key_ttm") or 0), key_total)
    ymax = signals.get("ymax")
    dias = signals.get("dias_preco")
    n_flags = int(signals.get("n_flags") or 0)

    fator_anual = annual_recency_factor(ymax, current_year)
    fator_preco = price_freshness_factor(dias)

    cobertura = (_COB_TTM * (n_key / key_total)
                 + _COB_ANUAL * fator_anual
                 + _COB_PRECO * (1.0 if fator_preco > 0 else 0.0))
    frescor = _FR_PRECO * fator_preco + _FR_ANUAL * fator_anual
    integridade = max(0.0, 1.0 - _PENALIDADE_POR_FLAG * n_flags)

    score = 100.0 * (W_COBERTURA * cobertura
                     + W_FRESCOR * frescor
                     + W_INTEGRIDADE * integridade)
    score = round(max(0.0, min(100.0, score)), 1)
    return {
        "score": score,
        "label": confidence_label(score),
        "cobertura": round(cobertura * 100, 1),
        "frescor": round(frescor * 100, 1),
        "integridade": round(integridade * 100, 1),
    }


_SQL_SIGNALS = """
WITH ativos AS (
    SELECT ticker FROM market.assets
    -- Universo de acoes analisaveis: evita misturar ETFs/BDRs e ativos sem
    -- empresa vinculada, que inflavam o denominador do painel de confianca.
    WHERE is_active
      AND asset_type IN ('stock', 'unit')
      AND company_id IS NOT NULL {tk_filter}
),
ttm AS (
    SELECT ticker, count(DISTINCT metric_name)
           FILTER (WHERE metric_name = ANY(:keys)) AS n_key
    FROM market.calculated_metrics WHERE period = 'ttm' GROUP BY ticker
),
ann AS (
    SELECT ticker, max(year) AS ymax FROM market.income_statements
    WHERE period = 'annual' GROUP BY ticker
),
px AS (
    SELECT ticker, (CURRENT_DATE - max(date))::int AS dias_preco
    FROM market.historical_prices WHERE close IS NOT NULL GROUP BY ticker
),
flags AS (
    SELECT ticker, count(DISTINCT issue_type) AS n_flags
    FROM market.data_quality_logs
    WHERE severity IN ('warn', 'error')
      AND created_at >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY ticker
)
SELECT a.ticker,
       COALESCE(t.n_key, 0)   AS n_key_ttm,
       an.ymax                AS ymax,
       p.dias_preco           AS dias_preco,
       COALESCE(f.n_flags, 0) AS n_flags
FROM ativos a
LEFT JOIN ttm   t  ON t.ticker  = a.ticker
LEFT JOIN ann   an ON an.ticker = a.ticker
LEFT JOIN px    p  ON p.ticker  = a.ticker
LEFT JOIN flags f  ON f.ticker  = a.ticker
ORDER BY a.ticker
"""


def _engine():
    try:
        from core.database import get_engine
        return get_engine()
    except Exception:
        return None


def _current_year(conn) -> int:
    try:
        return int(conn.execute(text("SELECT EXTRACT(year FROM CURRENT_DATE)")).scalar())
    except Exception:
        return 2026


def compute_confidence(engine=None, tickers: list[str] | None = None) -> list[dict]:
    """
    Score de confiança por ticker (ações ativas). Retorna lista de dicts
    {ticker, score, label, cobertura, frescor, integridade, ...sinais}, ordenada
    por score asc (piores primeiro). [] se o schema/engine não existir.
    """
    eng = engine or _engine()
    if eng is None:
        return []
    tk_filter = ""
    params: dict = {"keys": KEY_METRICS}
    if tickers:
        tk_filter = "AND ticker = ANY(:tks)"
        params["tks"] = [t.upper().replace(".SA", "") for t in tickers]
    try:
        with eng.connect() as conn:
            cy = _current_year(conn)
            rows = conn.execute(
                text(_SQL_SIGNALS.replace("{tk_filter}", tk_filter)), params).fetchall()
    except Exception as exc:
        logger.warning("compute_confidence: %s", exc)
        return []
    out: list[dict] = []
    for r in rows:
        sig = {"n_key_ttm": r.n_key_ttm, "ymax": r.ymax,
               "dias_preco": r.dias_preco, "n_flags": r.n_flags}
        out.append({"ticker": r.ticker, **score_ticker(sig, cy), **sig})
    out.sort(key=lambda d: d["score"])
    return out


def summarize_confidence(scored: list[dict]) -> dict:
    """Agrega a lista de compute_confidence em distribuição + média p/ o painel."""
    n = len(scored)
    if not n:
        return {"n": 0, "media": 0.0, "alta": 0, "media_faixa": 0, "baixa": 0}
    dist = {"Alta": 0, "Média": 0, "Baixa": 0}
    for d in scored:
        dist[d["label"]] = dist.get(d["label"], 0) + 1
    return {
        "n": n,
        "media": round(sum(d["score"] for d in scored) / n, 1),
        "alta": dist["Alta"],
        "media_faixa": dist["Média"],
        "baixa": dist["Baixa"],
        "piores": scored[:10],
    }

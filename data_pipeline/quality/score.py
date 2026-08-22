"""
data_pipeline/quality/score.py
Score de confiabilidade por (ticker, campo) — 0 a 100.

Considera: nº de fontes concordantes, idade do dado, consistência histórica,
nº de validações aprovadas e nº de divergências. Núcleo `compute_field_score`
é PURO e determinístico (testável). Persiste em `data_quality_scores`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SCORES_TABLE = "data_quality_scores"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def compute_field_score(
    n_sources_agree: int,
    age_days: float | None = 0.0,
    hist_cv: float | None = None,
    n_validations: int = 0,
    n_divergences: int = 0,
) -> float:
    """
    Retorna 0–100. Determinístico.
      • fontes concordantes: 0→0.35, 1→0.65, 2→0.95 (alta), ≥3→1.00
        (2 fontes concordantes já indicam alta confiança; o teto real é 3 —
        banco + Fundamentus + Status Invest — e vários indicadores só têm 2.)
      • idade: decai até 0.40 em ~3 anos
      • consistência: 1/(1+cv) limitado a [0.50, 1.00]; sem histórico → 0.95
      • validações aprovadas: bônus até +5%
      • divergências: −5% cada, até −40%
    """
    src = {0: 0.35, 1: 0.65, 2: 0.95}.get(int(max(0, n_sources_agree)), 1.00)

    age = float(age_days or 0.0)
    age_factor = _clamp(1.0 - age / (365.0 * 3.0), 0.40, 1.0)

    if hist_cv is None:
        consist = 0.95  # sem histórico → neutro-alto (não penaliza ausência de CV)
    else:
        consist = _clamp(1.0 / (1.0 + abs(float(hist_cv))), 0.50, 1.0)

    val_bonus = 1.0 + _clamp(int(max(0, n_validations)) * 0.01, 0.0, 0.05)
    div_penalty = 1.0 - _clamp(int(max(0, n_divergences)) * 0.05, 0.0, 0.40)

    score = 100.0 * src * age_factor * consist * val_bonus * div_penalty
    return round(_clamp(score, 0.0, 100.0), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Persistência
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_table(conn) -> None:
    from sqlalchemy import text
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {_SCORES_TABLE} (
            ticker TEXT NOT NULL,
            indicador TEXT NOT NULL,
            score DOUBLE PRECISION,
            n_fontes INTEGER, idade_dias DOUBLE PRECISION,
            consistencia DOUBLE PRECISION, n_validacoes INTEGER,
            n_divergencias INTEGER,
            last_audited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (ticker, indicador)
        )
    """))


def upsert_scores(rows: list[dict]) -> int:
    """rows: [{ticker, indicador, score, n_fontes, idade_dias, consistencia,
    n_validacoes, n_divergencias}]. Retorna nº gravado."""
    if not rows:
        return 0
    from sqlalchemy import text

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        return 0
    try:
        with engine.begin() as conn:
            _ensure_table(conn)
            for r in rows:
                conn.execute(text(f"""
                    INSERT INTO {_SCORES_TABLE}
                      (ticker, indicador, score, n_fontes, idade_dias, consistencia,
                       n_validacoes, n_divergencias, last_audited_at, updated_at)
                    VALUES (:tk, :ind, :sc, :nf, :age, :cons, :nv, :nd, NOW(), NOW())
                    ON CONFLICT (ticker, indicador) DO UPDATE SET
                      score = EXCLUDED.score, n_fontes = EXCLUDED.n_fontes,
                      idade_dias = EXCLUDED.idade_dias, consistencia = EXCLUDED.consistencia,
                      n_validacoes = EXCLUDED.n_validacoes, n_divergencias = EXCLUDED.n_divergencias,
                      last_audited_at = NOW(), updated_at = NOW()
                """), {
                    "tk": str(r.get("ticker", "")).upper(), "ind": str(r.get("indicador", "")),
                    "sc": float(r.get("score") or 0.0), "nf": int(r.get("n_fontes") or 0),
                    "age": float(r.get("idade_dias") or 0.0), "cons": float(r.get("consistencia") or 0.0),
                    "nv": int(r.get("n_validacoes") or 0), "nd": int(r.get("n_divergencias") or 0),
                })
        return len(rows)
    except Exception as exc:
        logger.warning("upsert_scores: %s", exc)
        return 0


def bank_average_score() -> float | None:
    from sqlalchemy import text

    from core.database import get_engine
    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            exists = conn.execute(text("""
                SELECT EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name=:t)
            """), {"t": _SCORES_TABLE}).scalar()
            if not exists:
                return None
            val = conn.execute(text(f"SELECT AVG(score) FROM {_SCORES_TABLE}")).scalar()
            return round(float(val), 1) if val is not None else None
    except Exception as exc:
        logger.warning("bank_average_score: %s", exc)
        return None

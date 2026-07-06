"""
Persistencia do portfolio B3 modelo criado pelo usuario.

Este modulo grava no banco unificado o resultado da Criacao de Portfolio B3.
Ele nao representa posicao comprada; representa a carteira-alvo/modelo que o
usuario decidiu adotar como padrao de investimento.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date
from typing import Any

import streamlit as st
from sqlalchemy import text

from core.config import settings
from core.database import get_engine
from core.b3_methodology import MODEL_SCHEMA_VERSION, SCORE_VERSION


DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS b3_portfolio_models (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name         VARCHAR(160) NOT NULL,
        status       VARCHAR(20) NOT NULL DEFAULT 'active',
        ano_compra   INTEGER,
        source       VARCHAR(80) NOT NULL DEFAULT 'criacao_portfolio_b3',
        plan_hash    TEXT NOT NULL,
        params_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
        metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
        notes        TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (status IN ('active', 'archived'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS b3_portfolio_model_items (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        model_id      UUID NOT NULL REFERENCES b3_portfolio_models(id) ON DELETE CASCADE,
        ticker        VARCHAR(16) NOT NULL,
        nome          VARCHAR(200),
        setor         TEXT,
        subsetor      TEXT,
        segmento      TEXT,
        weight        NUMERIC(12,8),
        score         NUMERIC(18,8),
        alpha_selic   NUMERIC(12,4),
        alpha_ew      NUMERIC(12,4),
        rank_score    INTEGER,
        ano_lider     INTEGER,
        motivos_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
        meta_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (model_id, ticker)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_b3_portfolio_models_user_status
    ON b3_portfolio_models (user_id, status, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_b3_portfolio_model_items_model_weight
    ON b3_portfolio_model_items (model_id, weight DESC)
    """,
    # Auditoria 2026-07: RLS (protege acesso via Supabase API/anon key; a
    # conexao do app, role postgres, bypassa) + no maximo 1 modelo ativo
    # por usuario. Espelha supabase_unificado/schema/018_rls_portfolio_models.sql.
    "ALTER TABLE b3_portfolio_models ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE b3_portfolio_model_items ENABLE ROW LEVEL SECURITY",
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='b3_portfolio_models'
              AND policyname='b3_portfolio_models_owner_all'
        ) THEN
            CREATE POLICY b3_portfolio_models_owner_all ON b3_portfolio_models
                USING (user_id = auth.uid())
                WITH CHECK (user_id = auth.uid());
        END IF;
    END; $$
    """,
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='b3_portfolio_model_items'
              AND policyname='b3_portfolio_model_items_owner_all'
        ) THEN
            CREATE POLICY b3_portfolio_model_items_owner_all ON b3_portfolio_model_items
                USING (EXISTS (
                    SELECT 1 FROM b3_portfolio_models m
                    WHERE m.id = model_id AND m.user_id = auth.uid()
                ))
                WITH CHECK (EXISTS (
                    SELECT 1 FROM b3_portfolio_models m
                    WHERE m.id = model_id AND m.user_id = auth.uid()
                ));
        END IF;
    END; $$
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_b3_portfolio_models_active_per_user
    ON b3_portfolio_models (user_id)
    WHERE status = 'active'
    """,
]


def _clean_nan(obj: Any) -> Any:
    """Converte NaN/Infinity (float ou numpy) em None — JSON/Postgres não os aceitam."""
    import math
    import numbers
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_nan(v) for v in obj]
    if isinstance(obj, bool):          # bool é subtipo de int — preservar
        return obj
    if isinstance(obj, numbers.Integral):   # int / np.int* — preservar como int
        return int(obj)
    if isinstance(obj, numbers.Real):        # float / np.float* — sanear não-finitos
        try:
            f = float(obj)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None
    return obj


def _safe_json(value: Any, default: Any) -> str:
    try:
        cleaned = _clean_nan(value if value is not None else default)
        return json.dumps(cleaned, ensure_ascii=False, allow_nan=False, default=str)
    except Exception:
        return json.dumps(default, ensure_ascii=False)


def _plan_hash(items: list[dict], params: dict) -> str:
    normalized_items = []
    for item in items:
        ticker = str(item.get("tk") or item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        normalized_items.append({
            "ticker": ticker,
            "weight": round(float(item.get("peso") or item.get("weight") or 0.0), 10),
            "score": round(float(item.get("score") or 0.0), 8),
            "setor": str(item.get("setor") or ""),
            "segmento": str(item.get("segmento") or ""),
        })
    payload = {
        "items": sorted(normalized_items, key=lambda item: item["ticker"]),
        "params": params,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _ensure_tables(conn) -> None:
    for ddl in DDL_SQL:
        conn.execute(text(ddl))


def _owner_id() -> str:
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError("OWNER_USER_ID nao configurado; nao e possivel salvar o portfolio modelo.")
    return str(owner)


def _normalize_weight(items: list[dict]) -> dict[str, float]:
    raw = {str(i.get("tk") or i.get("ticker") or "").upper(): float(i.get("peso") or i.get("weight") or 0) for i in items}
    raw = {k: v for k, v in raw.items() if k}
    total = sum(v for v in raw.values() if v > 0)
    if total <= 0 and raw:
        ew = 1.0 / len(raw)
        return {k: ew for k in raw}
    return {k: max(v, 0.0) / total for k, v in raw.items()}


def save_b3_portfolio_model(
    items: list[dict],
    params: dict,
    metrics: dict | None = None,
    name: str = "Portfolio B3 Modelo",
) -> str:
    """Salva/substitui o portfolio B3 ativo do usuario e retorna o id."""
    if not items:
        raise ValueError("Nenhuma empresa selecionada para salvar.")

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Banco unificado nao configurado.")

    params = dict(params or {})
    params.setdefault("score_version", SCORE_VERSION)
    params.setdefault("model_schema_version", MODEL_SCHEMA_VERSION)
    owner = _owner_id()
    ano_compra = int(params.get("ano_compra") or date.today().year)
    weights = _normalize_weight(items)
    plan_hash = _plan_hash(items, params)
    model_id = str(uuid.uuid4())

    with engine.begin() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("""
                UPDATE b3_portfolio_models
                SET status = 'archived', updated_at = NOW()
                WHERE user_id = :uid AND status = 'active'
            """),
            {"uid": owner},
        )
        conn.execute(
            text("""
                INSERT INTO b3_portfolio_models (
                    id, user_id, name, status, ano_compra, source, plan_hash,
                    params_json, metrics_json, notes
                )
                VALUES (
                    :id, :uid, :name, 'active', :ano_compra, 'criacao_portfolio_b3',
                    :plan_hash, CAST(:params_json AS jsonb), CAST(:metrics_json AS jsonb), :notes
                )
            """),
            {
                "id": model_id,
                "uid": owner,
                "name": name,
                "ano_compra": ano_compra,
                "plan_hash": plan_hash,
                "params_json": _safe_json(params, {}),
                "metrics_json": _safe_json(metrics, {}),
                "notes": "Gerado pela Criacao de Portfolio B3 e definido como portfolio padrao.",
            },
        )
        for idx, item in enumerate(items, start=1):
            ticker = str(item.get("tk") or item.get("ticker") or "").upper().strip()
            if not ticker:
                continue
            meta = {
                "rank_visual": idx,
                "is_lider_score": "Lider" in " ".join(item.get("motivos") or []),
                "params_hash": plan_hash,
            }
            conn.execute(
                text("""
                    INSERT INTO b3_portfolio_model_items (
                        model_id, ticker, nome, setor, subsetor, segmento, weight,
                        score, alpha_selic, alpha_ew, rank_score, ano_lider,
                        motivos_json, meta_json
                    )
                    VALUES (
                        :model_id, :ticker, :nome, :setor, :subsetor, :segmento, :weight,
                        :score, :alpha_selic, :alpha_ew, :rank_score, :ano_lider,
                        CAST(:motivos_json AS jsonb), CAST(:meta_json AS jsonb)
                    )
                """),
                {
                    "model_id": model_id,
                    "ticker": ticker,
                    "nome": item.get("nome") or ticker,
                    "setor": item.get("setor"),
                    "subsetor": item.get("subsetor"),
                    "segmento": item.get("segmento"),
                    "weight": weights.get(ticker, 0.0),
                    "score": float(item.get("score") or 0),
                    "alpha_selic": float(item.get("alpha_selic") or 0),
                    "alpha_ew": float(item.get("alpha_ew") or 0),
                    "rank_score": item.get("rank_score"),
                    "ano_lider": item.get("ano_lider"),
                    "motivos_json": _safe_json(item.get("motivos") or [], []),
                    "meta_json": _safe_json(meta, {}),
                },
            )

    load_active_b3_portfolio_model.clear()
    return model_id


@st.cache_data(ttl=300, show_spinner=False)
def load_active_b3_portfolio_model() -> dict:
    """Carrega o portfolio B3 modelo ativo do usuario. Retorna {} se nao existir."""
    engine = get_engine()
    if engine is None or not settings.OWNER_USER_ID:
        return {}

    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'b3_portfolio_models'
            )
        """)).scalar()
        if not exists:
            return {}

        header = conn.execute(
            text("""
                SELECT id, name, ano_compra, plan_hash, params_json, metrics_json, created_at
                FROM b3_portfolio_models
                WHERE user_id = :uid AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"uid": str(settings.OWNER_USER_ID)},
        ).mappings().fetchone()
        if not header:
            return {}

        rows = conn.execute(
            text("""
                SELECT ticker, nome, setor, subsetor, segmento, weight, score,
                       alpha_selic, alpha_ew, rank_score, ano_lider, motivos_json
                FROM b3_portfolio_model_items
                WHERE model_id = :mid
                ORDER BY weight DESC, score DESC, ticker
            """),
            {"mid": str(header["id"])},
        ).mappings().all()

    items = []
    for r in rows:
        d = dict(r)
        for k in ("weight", "score", "alpha_selic", "alpha_ew"):
            d[k] = float(d[k] or 0)
        items.append(d)

    model = dict(header)
    model["items"] = items
    model["num_items"] = len(items)
    params = model.get("params_json") or {}
    model["is_stale"] = (
        params.get("score_version") != SCORE_VERSION
        or int(params.get("model_schema_version") or 0) != MODEL_SCHEMA_VERSION
    )
    model["current_score_version"] = SCORE_VERSION
    return model

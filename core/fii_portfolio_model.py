"""
Persistência da carteira-modelo de FIIs configurada pelo usuário.

Espelha core.b3_portfolio_model (mesmo padrão de header + items, um modelo ativo
por usuário). Grava a seleção diversificada montada na página Seleção de FIIs para
que o Dashboard Geral mostre EXATAMENTE os FIIs e pesos que o usuário definiu.
"""
from __future__ import annotations

import hashlib
import json
import uuid

import streamlit as st
from sqlalchemy import text

from core.config import settings
from core.database import get_engine
# Reaproveita os helpers já testados do modelo B3 (mesma semântica).
from core.b3_portfolio_model import _owner_id, _safe_json, _normalize_weight


DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS fii_portfolio_models (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name         VARCHAR(160) NOT NULL,
        status       VARCHAR(20) NOT NULL DEFAULT 'active',
        source       VARCHAR(80) NOT NULL DEFAULT 'selecao_fiis',
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
    CREATE TABLE IF NOT EXISTS fii_portfolio_model_items (
        id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        model_id   UUID NOT NULL REFERENCES fii_portfolio_models(id) ON DELETE CASCADE,
        ticker     VARCHAR(16) NOT NULL,
        nome       VARCHAR(200),
        tipo       TEXT,
        segmento   TEXT,
        weight     NUMERIC(12,8) NOT NULL CHECK (weight >= 0 AND weight <= 1),
        dy_12m     NUMERIC(18,8),
        pvp        NUMERIC(18,8),
        score      NUMERIC(18,8),
        meta_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (model_id, ticker)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fii_portfolio_models_user_status
    ON fii_portfolio_models (user_id, status, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fii_portfolio_model_items_model_weight
    ON fii_portfolio_model_items (model_id, weight DESC)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_fii_portfolio_models_one_active
    ON fii_portfolio_models (user_id) WHERE status = 'active'
    """,
    # Auditoria FII 2026-07: RLS no DDL de runtime — antes as tabelas criadas
    # aqui nasciam SEM RLS (a proteção só vinha se a 018_fiis_hardening fosse
    # executada). Espelha as policies da 018 (mesmos nomes, idempotente).
    # A conexão do app (role postgres) bypassa; protege acesso via anon key.
    "ALTER TABLE fii_portfolio_models ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE fii_portfolio_model_items ENABLE ROW LEVEL SECURITY",
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='fii_portfolio_models'
              AND policyname='fii_models_owner_all'
        ) THEN
            CREATE POLICY fii_models_owner_all ON fii_portfolio_models
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
              AND tablename='fii_portfolio_model_items'
              AND policyname='fii_model_items_owner_all'
        ) THEN
            CREATE POLICY fii_model_items_owner_all ON fii_portfolio_model_items
                USING (EXISTS (
                    SELECT 1 FROM fii_portfolio_models m
                    WHERE m.id = model_id AND m.user_id = auth.uid()
                ))
                WITH CHECK (EXISTS (
                    SELECT 1 FROM fii_portfolio_models m
                    WHERE m.id = model_id AND m.user_id = auth.uid()
                ));
        END IF;
    END; $$
    """,
]


def _ensure_tables(conn) -> None:
    for ddl in DDL_SQL:
        conn.execute(text(ddl))


def _num(v):
    try:
        f = float(v)
        import math
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _fii_plan_hash(items: list[dict], params: dict,
                   weights: dict[str, float]) -> str:
    """Identifica a composição real, incluindo pesos e versão metodológica."""
    payload = {
        "items": sorted(
            (
                str(item.get("ticker") or item.get("tk") or "").upper(),
                round(weights.get(
                    str(item.get("ticker") or item.get("tk") or "").upper(), 0.0
                ), 8),
                _num(item.get("score")),
            )
            for item in items
            if item.get("ticker") or item.get("tk")
        ),
        "params": params,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def save_fii_portfolio_model(
    items: list[dict],
    params: dict,
    metrics: dict | None = None,
    name: str = "Carteira-modelo FIIs",
) -> str:
    """Salva/substitui a carteira-modelo de FIIs ativa do usuário. Retorna o id."""
    if not items:
        raise ValueError("Nenhum FII selecionado para salvar.")

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Banco unificado não configurado.")

    owner = _owner_id()
    weights = _normalize_weight(items)          # aceita 'peso'/'weight' + 'ticker'/'tk'
    plan_hash = _fii_plan_hash(items, params, weights)
    model_id = str(uuid.uuid4())

    with engine.begin() as conn:
        _ensure_tables(conn)
        conn.execute(
            text("""
                UPDATE fii_portfolio_models SET status = 'archived', updated_at = NOW()
                WHERE user_id = :uid AND status = 'active'
            """),
            {"uid": owner},
        )
        conn.execute(
            text("""
                INSERT INTO fii_portfolio_models
                    (id, user_id, name, status, source, plan_hash, params_json, metrics_json, notes)
                VALUES
                    (:id, :uid, :name, 'active', 'selecao_fiis', :plan_hash,
                     CAST(:params_json AS jsonb), CAST(:metrics_json AS jsonb), :notes)
            """),
            {
                "id": model_id, "uid": owner, "name": name, "plan_hash": plan_hash,
                "params_json": _safe_json(params, {}),
                "metrics_json": _safe_json(metrics, {}),
                "notes": "Carteira-modelo de FIIs definida pelo usuário na Seleção de FIIs.",
            },
        )
        for item in items:
            ticker = str(item.get("ticker") or item.get("tk") or "").upper().strip()
            if not ticker:
                continue
            conn.execute(
                text("""
                    INSERT INTO fii_portfolio_model_items
                        (model_id, ticker, nome, tipo, segmento, weight, dy_12m, pvp, score, meta_json)
                    VALUES
                        (:model_id, :ticker, :nome, :tipo, :segmento, :weight, :dy_12m, :pvp, :score,
                         CAST(:meta_json AS jsonb))
                """),
                {
                    "model_id": model_id, "ticker": ticker,
                    "nome": item.get("nome") or ticker,
                    "tipo": item.get("tipo"),
                    "segmento": item.get("segmento"),
                    "weight": weights.get(ticker, 0.0),
                    "dy_12m": _num(item.get("dy_12m")),
                    "pvp": _num(item.get("pvp")),
                    "score": _num(item.get("score")),
                    "meta_json": _safe_json({}, {}),
                },
            )

    load_active_fii_portfolio_model.clear()
    return model_id


@st.cache_data(ttl=300, show_spinner=False)
def load_active_fii_portfolio_model() -> dict:
    """Carrega a carteira-modelo de FIIs ativa do usuário. {} se não existir."""
    engine = get_engine()
    if engine is None or not settings.OWNER_USER_ID:
        return {}

    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name='fii_portfolio_models')
        """)).scalar()
        if not exists:
            return {}
        header = conn.execute(
            text("""
                SELECT id, name, params_json, metrics_json, created_at
                FROM fii_portfolio_models
                WHERE user_id = :uid AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
            """),
            {"uid": str(settings.OWNER_USER_ID)},
        ).mappings().fetchone()
        if not header:
            return {}
        rows = conn.execute(
            text("""
                SELECT ticker, nome, tipo, segmento, weight, dy_12m, pvp, score
                FROM fii_portfolio_model_items
                WHERE model_id = :mid
                ORDER BY weight DESC, score DESC, ticker
            """),
            {"mid": str(header["id"])},
        ).mappings().all()

    items = []
    for r in rows:
        d = dict(r)
        for k in ("weight", "dy_12m", "pvp", "score"):
            d[k] = float(d[k]) if d[k] is not None else None
        # normaliza para o mesmo formato do build_portfolio (peso)
        d["peso"] = d.get("weight") or 0.0
        items.append(d)

    model = dict(header)
    model["items"] = items
    model["num_items"] = len(items)
    return model

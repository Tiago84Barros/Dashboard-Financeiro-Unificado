"""
Persistência da carteira-modelo de FIIs configurada pelo usuário.

Espelha core.b3_portfolio_model (mesmo padrão de header + items, um modelo ativo
por usuário). Grava a seleção diversificada montada na página Seleção de FIIs para
que o Dashboard Geral mostre EXATAMENTE os FIIs e pesos que o usuário definiu.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid

import streamlit as st
from sqlalchemy import text

# Reaproveita os helpers já testados do modelo B3 (mesma semântica).
from core.b3_portfolio_model import _normalize_weight, _owner_id, _safe_json
from core.config import settings
from core.database import get_engine

_REQUIRED_TABLES = ("fii_portfolio_models", "fii_portfolio_model_items")
_SCHEMA_MIGRATIONS = ("018_fiis_hardening.sql",)
_OWNER_POLICIES = {
    "fii_portfolio_models": "fii_models_owner_all",
    "fii_portfolio_model_items": "fii_model_items_owner_all",
}


def _preflight_schema(conn) -> None:
    """Confirma tabelas, RLS e policies do dono, apenas por catálogo."""
    columns = []
    for table, policy in _OWNER_POLICIES.items():
        relation = f"to_regclass('public.{table}')"
        columns.extend((
            f"{relation} AS {table}",
            f"COALESCE((SELECT relrowsecurity FROM pg_class WHERE oid = {relation}), FALSE) AS {table}_rls",
            "EXISTS (SELECT 1 FROM pg_policies "
            f"WHERE schemaname = 'public' AND tablename = '{table}' "
            f"AND policyname = '{policy}') AS {table}_policy",
        ))
    available = conn.execute(text("SELECT " + ", ".join(columns))).mappings().one()
    missing = [table for table in _REQUIRED_TABLES if available[table] is None]
    rls_disabled = [table for table in _REQUIRED_TABLES if not available[f"{table}_rls"]]
    policies_missing = [
        f"{table} ({_OWNER_POLICIES[table]})"
        for table in _REQUIRED_TABLES if not available[f"{table}_policy"]
    ]
    if missing or rls_disabled or policies_missing:
        problems = []
        if missing:
            problems.append("tabelas ausentes: " + ", ".join(missing))
        if rls_disabled:
            problems.append("RLS desabilitado em: " + ", ".join(rls_disabled))
        if policies_missing:
            problems.append("policy de dono ausente: " + ", ".join(policies_missing))
        raise RuntimeError(
            "Preflight da carteira FIIs falhou (" + "; ".join(problems) + "). "
            "Aplique a migration " + ", ".join(_SCHEMA_MIGRATIONS)
            + " antes de salvar ou restaurar; o aplicativo não executa DDL em runtime."
        )


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


def _model_from_connection(conn, owner: str, *,
                           model_id: str | None = None,
                           active_only: bool = False) -> dict:
    where = ["user_id = :uid"]
    parameters = {"uid": owner}
    if model_id:
        where.append("id = :mid")
        parameters["mid"] = model_id
    if active_only:
        where.append("status = 'active'")
    header = conn.execute(
        text(f"""
            SELECT id, name, status, plan_hash, params_json, metrics_json,
                   created_at, updated_at
            FROM fii_portfolio_models
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT 1
        """),
        parameters,
    ).mappings().fetchone()
    if not header:
        return {}
    rows = conn.execute(
        text("""
            SELECT ticker, nome, tipo, segmento, weight, dy_12m, pvp, score,
                   meta_json
            FROM fii_portfolio_model_items
            WHERE model_id = :mid
            ORDER BY weight DESC, score DESC, ticker
        """),
        {"mid": str(header["id"])},
    ).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        for key in ("weight", "dy_12m", "pvp", "score"):
            item[key] = float(item[key]) if item[key] is not None else None
        item["peso"] = item.get("weight") or 0.0
        meta = item.pop("meta_json", {}) or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if isinstance(meta, dict):
            item.update({
                key: meta.get(key)
                for key in ("macro_impact", "macro_score_adjustment")
                if key in meta
            })
        items.append(item)
    model = dict(header)
    model["items"] = items
    model["num_items"] = len(items)
    return model


def validate_fii_portfolio_model(model: dict, *,
                                 weight_tolerance: float = 1e-6) -> dict:
    """Valida integridade determinística da versão persistida.

    Pesos usam fração decimal e devem totalizar 1. O hash cobre composição,
    pesos, scores e parâmetros metodológicos; divergência bloqueia restauração.
    """
    items = list(model.get("items") or [])
    reasons: list[str] = []
    tickers = [
        str(item.get("ticker") or item.get("tk") or "").strip().upper()
        for item in items
    ]
    if not items:
        reasons.append("modelo sem itens")
    if any(not ticker for ticker in tickers):
        reasons.append("ticker vazio")
    if len(set(tickers)) != len(tickers):
        reasons.append("tickers duplicados")
    weights = {
        ticker: float(item.get("weight") or item.get("peso") or 0.0)
        for ticker, item in zip(tickers, items)
        if ticker
    }
    if any(weight <= 0 or weight > 1 for weight in weights.values()):
        reasons.append("peso fora do intervalo (0, 1]")
    weight_sum = sum(weights.values())
    if items and abs(weight_sum - 1.0) > weight_tolerance:
        reasons.append(f"soma dos pesos divergente: {weight_sum:.8f}")
    params = model.get("params_json") or {}
    expected_hash = _fii_plan_hash(items, params, weights)
    stored_hash = str(model.get("plan_hash") or "")
    if stored_hash and stored_hash != expected_hash:
        reasons.append("hash do plano divergente")
    if not stored_hash:
        reasons.append("hash do plano ausente")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "item_count": len(items),
        "weight_sum": weight_sum,
        "expected_plan_hash": expected_hash,
        "stored_plan_hash": stored_hash,
    }


def _clear_portfolio_caches() -> None:
    load_active_fii_portfolio_model.clear()
    list_fii_portfolio_model_versions.clear()


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
    tickers = [
        str(item.get("ticker") or item.get("tk") or "").upper().strip()
        for item in items
    ]
    valid_tickers = [ticker for ticker in tickers if ticker]
    if not valid_tickers:
        raise ValueError("Nenhum ticker válido para salvar.")
    if len(valid_tickers) != len(set(valid_tickers)):
        raise ValueError("A carteira contém tickers duplicados.")
    weights = _normalize_weight(items)          # aceita 'peso'/'weight' + 'ticker'/'tk'
    plan_hash = _fii_plan_hash(items, params, weights)
    model_id = str(uuid.uuid4())

    with engine.begin() as conn:
        _preflight_schema(conn)
        # Serializa substituições concorrentes do modelo ativo do mesmo dono.
        conn.execute(
            text("""
                SELECT id FROM fii_portfolio_models
                WHERE user_id = :uid
                FOR UPDATE
            """),
            {"uid": owner},
        ).all()
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
                    "meta_json": _safe_json({
                        "macro_impact": item.get("macro_impact"),
                        "macro_score_adjustment": item.get("macro_score_adjustment"),
                    }, {}),
                },
            )

        persisted = _model_from_connection(conn, owner, model_id=model_id)
        integrity = validate_fii_portfolio_model(persisted)
        if not integrity["ok"]:
            raise RuntimeError(
                "Verificação pós-gravação falhou: "
                + " · ".join(integrity["reasons"])
            )

    # Ver nota em core/b3_portfolio_model.py: captura aditiva, nunca bloqueante.
    # Import local: no topo criaria ciclo com core/portfolio/snapshots.py,
    # que importa _clean_nan de core/b3_portfolio_model.py.
    # O import fica protegido porque a garantia de capture_snapshots cobre o
    # corpo da funcao, nao o ato de importa-la: uma quebra no pacote portfolio
    # nunca pode impedir o salvamento da carteira.
    try:
        from core.portfolio.capture import capture_snapshots
    except ImportError:
        logging.getLogger(__name__).error(
            "Falha ao importar core.portfolio.capture; snapshot nao capturado. "
            "A carteira foi salva normalmente.", exc_info=True,
        )
    else:
        capture_snapshots("fii", model_id, items, params, owner_id=owner)

    _clear_portfolio_caches()
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
        model = _model_from_connection(
            conn, str(settings.OWNER_USER_ID), active_only=True
        )
    if model:
        model["integrity"] = validate_fii_portfolio_model(model)
    return model


@st.cache_data(ttl=120, show_spinner=False)
def list_fii_portfolio_model_versions(limit: int = 10) -> list[dict]:
    """Lista versões do proprietário sem expor posições ou dados pessoais."""
    engine = get_engine()
    if engine is None or not settings.OWNER_USER_ID:
        return []
    safe_limit = max(1, min(int(limit), 50))
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT m.id, m.name, m.status, m.plan_hash, m.params_json,
                       m.created_at, m.updated_at, count(i.id) AS item_count,
                       COALESCE(sum(i.weight), 0) AS weight_sum
                FROM fii_portfolio_models m
                LEFT JOIN fii_portfolio_model_items i ON i.model_id = m.id
                WHERE m.user_id = :uid
                GROUP BY m.id
                ORDER BY m.created_at DESC
                LIMIT :limit
            """),
            {"uid": str(settings.OWNER_USER_ID), "limit": safe_limit},
        ).mappings().all()
    return [
        {
            **dict(row),
            "item_count": int(row["item_count"] or 0),
            "weight_sum": float(row["weight_sum"] or 0.0),
        }
        for row in rows
    ]


def restore_fii_portfolio_model(model_id: str) -> str:
    """Restaura uma versão íntegra do proprietário em uma única transação."""
    try:
        target_id = str(uuid.UUID(str(model_id)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Identificador de versão inválido.") from exc
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Banco unificado não configurado.")
    owner = _owner_id()
    with engine.begin() as conn:
        _preflight_schema(conn)
        owned_ids = {
            str(value) for value in conn.execute(
                text("""
                    SELECT id FROM fii_portfolio_models
                    WHERE user_id = :uid
                    FOR UPDATE
                """),
                {"uid": owner},
            ).scalars()
        }
        if target_id not in owned_ids:
            raise ValueError("Versão inexistente ou pertencente a outro usuário.")
        target = _model_from_connection(conn, owner, model_id=target_id)
        integrity = validate_fii_portfolio_model(target)
        if not integrity["ok"]:
            raise RuntimeError(
                "Restauração bloqueada por falha de integridade: "
                + " · ".join(integrity["reasons"])
            )
        if target.get("status") != "active":
            conn.execute(
                text("""
                    UPDATE fii_portfolio_models
                    SET status = 'archived', updated_at = NOW()
                    WHERE user_id = :uid AND status = 'active'
                """),
                {"uid": owner},
            )
            conn.execute(
                text("""
                    UPDATE fii_portfolio_models
                    SET status = 'active', updated_at = NOW()
                    WHERE id = :mid AND user_id = :uid
                """),
                {"mid": target_id, "uid": owner},
            )
        restored = _model_from_connection(conn, owner, model_id=target_id)
        if restored.get("status") != "active":
            raise RuntimeError("A versão restaurada não ficou ativa.")
    _clear_portfolio_caches()
    return target_id

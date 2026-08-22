"""
Persistencia do portfolio B3 modelo criado pelo usuario.

Este modulo grava no banco unificado o resultado da Criacao de Portfolio B3.
Ele nao representa posicao comprada; representa a carteira-alvo/modelo que o
usuario decidiu adotar como padrao de investimento.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date
from typing import Any

import streamlit as st
from sqlalchemy import text

from core.b3_methodology import MODEL_SCHEMA_VERSION, SCORE_VERSION
from core.config import settings
from core.database import get_engine

_REQUIRED_TABLES = ("b3_portfolio_models", "b3_portfolio_model_items")
_SCHEMA_MIGRATIONS = (
    "011_b3_portfolio_models.sql",
    "018_rls_portfolio_models.sql",
    "020_b3_portfolio_hardening.sql",
)
_OWNER_POLICIES = {
    "b3_portfolio_models": "b3_portfolio_models_owner_all",
    "b3_portfolio_model_items": "b3_portfolio_model_items_owner_all",
}


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
            "Preflight da carteira B3 falhou (" + "; ".join(problems) + "). "
            "Aplique as migrations " + ", ".join(_SCHEMA_MIGRATIONS)
            + " antes de salvar; o aplicativo não executa DDL em runtime."
        )


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


def _model_from_connection(conn, owner: str, *,
                           model_id: str | None = None,
                           active_only: bool = False) -> dict:
    """Le um header + itens do portfolio B3, por id ou pelo modelo ativo.

    Espelha core.fii_portfolio_model._model_from_connection (mesmo padrao de
    header + items, mesma semantica de filtros).
    """
    where = ["user_id = :uid"]
    parameters: dict[str, Any] = {"uid": owner}
    if model_id:
        where.append("id = :mid")
        parameters["mid"] = model_id
    if active_only:
        where.append("status = 'active'")
    header = conn.execute(
        text(f"""
            SELECT id, name, status, ano_compra, plan_hash, params_json,
                   metrics_json, created_at, updated_at
            FROM b3_portfolio_models
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
            SELECT ticker, nome, setor, subsetor, segmento, weight, score,
                   alpha_selic, alpha_ew, rank_score, ano_lider, motivos_json
            FROM b3_portfolio_model_items
            WHERE model_id = :mid
            ORDER BY weight DESC, score DESC, ticker
        """),
        {"mid": str(header["id"])},
    ).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        for key in ("weight", "score", "alpha_selic", "alpha_ew"):
            item[key] = float(item[key]) if item[key] is not None else None
        items.append(item)
    model = dict(header)
    model["items"] = items
    model["num_items"] = len(items)
    return model


def validate_b3_portfolio_model(model: dict, *,
                                weight_tolerance: float = 1e-6) -> dict:
    """Valida integridade estrutural da versao persistida antes de restaurar.

    Nao compara plan_hash: ao contrario de core.fii_portfolio_model, o hash da
    B3 e calculado sobre o peso BRUTO de entrada (antes da normalizacao),
    enquanto a coluna `weight` gravada ja e o peso normalizado — recalcular o
    hash a partir do que foi persistido nunca bateria com o hash gravado, o
    que tornaria a checagem sempre falsa-negativa. A garantia aqui e
    estrutural: sem itens, tickers duplicados/vazios ou pesos fora do
    intervalo (0, 1] bloqueiam a restauracao da mesma forma que bloqueariam a
    gravacao original.
    """
    items = list(model.get("items") or [])
    reasons: list[str] = []
    tickers = [str(item.get("ticker") or "").strip().upper() for item in items]
    if not items:
        reasons.append("modelo sem itens")
    if any(not ticker for ticker in tickers):
        reasons.append("ticker vazio")
    if len(set(tickers)) != len(tickers):
        reasons.append("tickers duplicados")
    weights = [float(item.get("weight") or 0.0) for item in items]
    if any(weight <= 0 or weight > 1 for weight in weights):
        reasons.append("peso fora do intervalo (0, 1]")
    weight_sum = sum(weights)
    if items and abs(weight_sum - 1.0) > weight_tolerance:
        reasons.append(f"soma dos pesos divergente: {weight_sum:.8f}")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "item_count": len(items),
        "weight_sum": weight_sum,
    }


def _clear_b3_portfolio_caches() -> None:
    load_active_b3_portfolio_model.clear()
    list_b3_portfolio_model_versions.clear()


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
        _preflight_schema(conn)
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
            # Parecer do gate qualitativo (classificacao/motivo) — a Avaliacao
            # de Portfolio exibe isso junto ao relatorio completo da empresa.
            if item.get("quali"):
                meta["quali"] = item["quali"]
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

    # Persiste o snapshot analitico (fundamentos, historico, premissas) da
    # carteira. Aditivo: capture_snapshots nunca levanta excecao, entao uma
    # falha aqui deixa o salvamento exatamente como era antes.
    # Import local: no topo criaria ciclo com core/portfolio/snapshots.py,
    # que importa _clean_nan deste modulo.
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
        capture_snapshots("b3", model_id, items, params, owner_id=owner)

    _clear_b3_portfolio_caches()
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


@st.cache_data(ttl=120, show_spinner=False)
def list_b3_portfolio_model_versions(limit: int = 10) -> list[dict]:
    """Lista versoes do proprietario sem expor posicoes ou dados pessoais.

    Espelha core.fii_portfolio_model.list_fii_portfolio_model_versions.
    """
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
                FROM b3_portfolio_models m
                LEFT JOIN b3_portfolio_model_items i ON i.model_id = m.id
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


def restore_b3_portfolio_model(model_id: str) -> str:
    """Restaura uma versao integra do proprietario em uma unica transacao.

    Espelha core.fii_portfolio_model.restore_fii_portfolio_model.
    """
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
                    SELECT id FROM b3_portfolio_models
                    WHERE user_id = :uid
                    FOR UPDATE
                """),
                {"uid": owner},
            ).scalars()
        }
        if target_id not in owned_ids:
            raise ValueError("Versão inexistente ou pertencente a outro usuário.")
        target = _model_from_connection(conn, owner, model_id=target_id)
        integrity = validate_b3_portfolio_model(target)
        if not integrity["ok"]:
            raise RuntimeError(
                "Restauração bloqueada por falha de integridade: "
                + " · ".join(integrity["reasons"])
            )
        if target.get("status") != "active":
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
                    UPDATE b3_portfolio_models
                    SET status = 'active', updated_at = NOW()
                    WHERE id = :mid AND user_id = :uid
                """),
                {"mid": target_id, "uid": owner},
            )
        restored = _model_from_connection(conn, owner, model_id=target_id)
        if restored.get("status") != "active":
            raise RuntimeError("A versão restaurada não ficou ativa.")
    _clear_b3_portfolio_caches()
    return target_id

"""
Persistencia do portfolio americano modelo criado pelo usuario.

Espelho de ``core/b3_portfolio_model.py`` para o mercado dos Estados Unidos.
Grava no banco unificado (schema public) o resultado da Criacao de Portfolio da
secao Empresas Americanas. Nao representa posicao comprada; representa a
carteira-alvo/modelo que o usuario decidiu adotar como padrao.

Por que public e nao market_us: o schema market_us e o armazem/vitrine de dados
de mercado e roda em modo snapshot (somente leitura) no deploy. A decisao do
usuario e dado do usuario e vive junto das demais carteiras-modelo (B3, FIIs).
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

from core.config import settings
from core.database import get_engine
from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION, US_SCHEMA_VERSION

_REQUIRED_TABLES = ("us_portfolio_models", "us_portfolio_model_items")
_SCHEMA_MIGRATIONS = ("047_us_portfolio_models.sql",)
_OWNER_POLICIES = {
    "us_portfolio_models": "us_portfolio_models_owner_all",
    "us_portfolio_model_items": "us_portfolio_model_items_owner_all",
}


def _clean_nan(obj: Any) -> Any:
    """Converte NaN/Infinity (float ou numpy) em None — JSON/Postgres não os aceitam."""
    import math
    import numbers
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean_nan(v) for v in obj]
    if isinstance(obj, bool):               # bool é subtipo de int — preservar
        return obj
    if isinstance(obj, numbers.Integral):   # int / np.int* — preservar como int
        return int(obj)
    if isinstance(obj, numbers.Real):       # float / np.float* — sanear não-finitos
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


def _num(value: Any, default: float | None = 0.0) -> float | None:
    """float() tolerante: NaN/None/texto viram ``default`` em vez de estourar."""
    import math
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _text(*valores: Any) -> str | None:
    """Primeiro valor textual utilizável. NaN vira None (a coluna é VARCHAR/TEXT).

    Os itens chegam de ``DataFrame.to_dict("records")``: célula ausente é float
    NaN, não None — gravá-la produziria a string "nan" no banco.
    """
    for valor in valores:
        if valor is None:
            continue
        if isinstance(valor, float) and valor != valor:   # NaN
            continue
        texto = str(valor).strip()
        if texto and texto.lower() != "nan":
            return texto
    return None


def _symbol_of(item: dict) -> str:
    return (_text(item.get("symbol"), item.get("ticker")) or "").upper()


def _plan_hash(items: list[dict], params: dict) -> str:
    normalized_items = []
    for item in items:
        symbol = _symbol_of(item)
        if not symbol:
            continue
        normalized_items.append({
            "symbol": symbol,
            "weight": round(_num(item.get("weight") or item.get("peso")) or 0.0, 10),
            "entry_score": round(_num(item.get("entry_score")) or 0.0, 8),
            "setor": _text(item.get("sector_group"), item.get("setor")) or "",
            "industria": _text(item.get("industry_group"), item.get("industria")) or "",
        })
    payload = {
        "items": sorted(normalized_items, key=lambda item: item["symbol"]),
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
            "Preflight da carteira EUA falhou (" + "; ".join(problems) + "). "
            "Aplique a migration " + ", ".join(_SCHEMA_MIGRATIONS)
            + " antes de salvar; o aplicativo não executa DDL em runtime."
        )


def _owner_id() -> str:
    owner = settings.OWNER_USER_ID
    if not owner:
        raise RuntimeError(
            "OWNER_USER_ID nao configurado; nao e possivel salvar o portfolio modelo."
        )
    return str(owner)


def _normalize_weight(items: list[dict]) -> dict[str, float]:
    raw = {_symbol_of(i): (_num(i.get("weight") or i.get("peso")) or 0.0) for i in items}
    raw = {k: v for k, v in raw.items() if k}
    total = sum(v for v in raw.values() if v > 0)
    if total <= 0 and raw:
        ew = 1.0 / len(raw)
        return {k: ew for k in raw}
    return {k: max(v, 0.0) / total for k, v in raw.items()}


def _model_from_connection(conn, owner: str, *,
                           model_id: str | None = None,
                           active_only: bool = False) -> dict:
    """Le um header + itens do portfolio EUA, por id ou pelo modelo ativo.

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
            FROM us_portfolio_models
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
            SELECT symbol, nome, setor, industria, weight, entry_score,
                   fundamental_score, coverage, rank_score
            FROM us_portfolio_model_items
            WHERE model_id = :mid
            ORDER BY weight DESC, entry_score DESC, symbol
        """),
        {"mid": str(header["id"])},
    ).mappings().all()
    items = []
    for row in rows:
        item = dict(row)
        for key in ("weight", "entry_score", "fundamental_score", "coverage"):
            item[key] = float(item[key]) if item[key] is not None else None
        item["ticker"] = item["symbol"]
        items.append(item)
    model = dict(header)
    model["items"] = items
    model["num_items"] = len(items)
    return model


def validate_us_portfolio_model(model: dict, *,
                                weight_tolerance: float = 1e-6) -> dict:
    """Valida integridade estrutural da versao persistida antes de restaurar.

    Nao compara plan_hash pelo mesmo motivo documentado em
    core.b3_portfolio_model.validate_b3_portfolio_model: o hash e calculado
    sobre o peso bruto de entrada, e a coluna `weight` gravada ja e o peso
    normalizado. A garantia aqui e estrutural: sem itens, symbols
    duplicados/vazios ou pesos fora do intervalo (0, 1] bloqueiam a
    restauracao da mesma forma que bloqueariam a gravacao original.
    """
    items = list(model.get("items") or [])
    reasons: list[str] = []
    symbols = [str(item.get("symbol") or "").strip().upper() for item in items]
    if not items:
        reasons.append("modelo sem itens")
    if any(not symbol for symbol in symbols):
        reasons.append("symbol vazio")
    if len(set(symbols)) != len(symbols):
        reasons.append("symbols duplicados")
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


def _clear_us_portfolio_caches() -> None:
    load_active_us_portfolio_model.clear()
    list_us_portfolio_model_versions.clear()


def save_us_portfolio_model(
    items: list[dict],
    params: dict,
    metrics: dict | None = None,
    name: str = "Portfolio EUA Modelo",
) -> str:
    """Salva/substitui o portfolio americano ativo do usuario e retorna o id."""
    if not items:
        raise ValueError("Nenhuma empresa selecionada para salvar.")

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Banco unificado nao configurado.")

    params = dict(params or {})
    params.setdefault("score_version", US_FUNDAMENTAL_SCORE_VERSION)
    params.setdefault("model_schema_version", US_SCHEMA_VERSION)
    owner = _owner_id()
    ano_compra = int(params.get("ano_compra") or date.today().year)
    weights = _normalize_weight(items)
    plan_hash = _plan_hash(items, params)
    model_id = str(uuid.uuid4())

    with engine.begin() as conn:
        _preflight_schema(conn)
        conn.execute(
            text("""
                UPDATE us_portfolio_models
                SET status = 'archived', updated_at = NOW()
                WHERE user_id = :uid AND status = 'active'
            """),
            {"uid": owner},
        )
        conn.execute(
            text("""
                INSERT INTO us_portfolio_models (
                    id, user_id, name, status, ano_compra, source, plan_hash,
                    params_json, metrics_json, notes
                )
                VALUES (
                    :id, :uid, :name, 'active', :ano_compra, 'criacao_portfolio_us',
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
                "notes": "Gerado pela Criacao de Portfolio das Empresas Americanas "
                         "e definido como portfolio padrao.",
            },
        )
        for idx, item in enumerate(items, start=1):
            symbol = _symbol_of(item)
            if not symbol:
                continue
            conn.execute(
                text("""
                    INSERT INTO us_portfolio_model_items (
                        model_id, symbol, nome, setor, industria, weight,
                        entry_score, fundamental_score, coverage, rank_score, meta_json
                    )
                    VALUES (
                        :model_id, :symbol, :nome, :setor, :industria, :weight,
                        :entry_score, :fundamental_score, :coverage, :rank_score,
                        CAST(:meta_json AS jsonb)
                    )
                """),
                {
                    "model_id": model_id,
                    "symbol": symbol,
                    "nome": _text(item.get("name"), item.get("nome")) or symbol,
                    "setor": _text(item.get("sector_group"), item.get("setor")),
                    "industria": _text(item.get("industry_group"), item.get("industria")),
                    "weight": weights.get(symbol, 0.0),
                    "entry_score": _num(item.get("entry_score")),
                    "fundamental_score": _num(item.get("fundamental_score")),
                    "coverage": _num(item.get("coverage")),
                    "rank_score": idx,
                    "meta_json": _safe_json(
                        {
                            "rank_visual": idx,
                            "params_hash": plan_hash,
                            "allocation_usd": _num(item.get("allocation_usd"), None),
                        },
                        {},
                    ),
                },
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
        capture_snapshots("us", model_id, items, params, owner_id=owner)

    _clear_us_portfolio_caches()
    return model_id


@st.cache_data(ttl=300, show_spinner=False)
def load_active_us_portfolio_model() -> dict:
    """Carrega o portfolio americano modelo ativo do usuario. {} se nao existir."""
    engine = get_engine()
    if engine is None or not settings.OWNER_USER_ID:
        return {}

    with engine.connect() as conn:
        exists = conn.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'us_portfolio_models'
            )
        """)).scalar()
        if not exists:
            return {}

        header = conn.execute(
            text("""
                SELECT id, name, ano_compra, plan_hash, params_json, metrics_json, created_at
                FROM us_portfolio_models
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
                SELECT symbol, nome, setor, industria, weight, entry_score,
                       fundamental_score, coverage, rank_score
                FROM us_portfolio_model_items
                WHERE model_id = :mid
                ORDER BY weight DESC, entry_score DESC, symbol
            """),
            {"mid": str(header["id"])},
        ).mappings().all()

    items = []
    for r in rows:
        d = dict(r)
        for k in ("weight", "entry_score", "fundamental_score", "coverage"):
            d[k] = float(d[k] or 0)
        # ``ticker`` é alias de leitura: o dashboard trata B3 e EUA com o mesmo
        # código de exibição e não precisa saber qual mercado gerou o item.
        d["ticker"] = d["symbol"]
        items.append(d)

    model = dict(header)
    model["items"] = items
    model["num_items"] = len(items)
    params = model.get("params_json") or {}
    model["is_stale"] = (
        params.get("score_version") != US_FUNDAMENTAL_SCORE_VERSION
        or int(params.get("model_schema_version") or 0) != US_SCHEMA_VERSION
    )
    model["current_score_version"] = US_FUNDAMENTAL_SCORE_VERSION
    return model


@st.cache_data(ttl=120, show_spinner=False)
def list_us_portfolio_model_versions(limit: int = 10) -> list[dict]:
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
                FROM us_portfolio_models m
                LEFT JOIN us_portfolio_model_items i ON i.model_id = m.id
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


def restore_us_portfolio_model(model_id: str) -> str:
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
                    SELECT id FROM us_portfolio_models
                    WHERE user_id = :uid
                    FOR UPDATE
                """),
                {"uid": owner},
            ).scalars()
        }
        if target_id not in owned_ids:
            raise ValueError("Versão inexistente ou pertencente a outro usuário.")
        target = _model_from_connection(conn, owner, model_id=target_id)
        integrity = validate_us_portfolio_model(target)
        if not integrity["ok"]:
            raise RuntimeError(
                "Restauração bloqueada por falha de integridade: "
                + " · ".join(integrity["reasons"])
            )
        if target.get("status") != "active":
            conn.execute(
                text("""
                    UPDATE us_portfolio_models
                    SET status = 'archived', updated_at = NOW()
                    WHERE user_id = :uid AND status = 'active'
                """),
                {"uid": owner},
            )
            conn.execute(
                text("""
                    UPDATE us_portfolio_models
                    SET status = 'active', updated_at = NOW()
                    WHERE id = :mid AND user_id = :uid
                """),
                {"mid": target_id, "uid": owner},
            )
        restored = _model_from_connection(conn, owner, model_id=target_id)
        if restored.get("status") != "active":
            raise RuntimeError("A versão restaurada não ficou ativa.")
    _clear_us_portfolio_caches()
    return target_id

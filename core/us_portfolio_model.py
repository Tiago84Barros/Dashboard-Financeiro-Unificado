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
import uuid
from datetime import date
from typing import Any

import streamlit as st
from sqlalchemy import text

from core.config import settings
from core.database import get_engine
from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION, US_SCHEMA_VERSION


DDL_SQL = [
    """
    CREATE TABLE IF NOT EXISTS us_portfolio_models (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
        name         VARCHAR(160) NOT NULL,
        status       VARCHAR(20) NOT NULL DEFAULT 'active',
        ano_compra   INTEGER,
        source       VARCHAR(80) NOT NULL DEFAULT 'criacao_portfolio_us',
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
    CREATE TABLE IF NOT EXISTS us_portfolio_model_items (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        model_id      UUID NOT NULL REFERENCES us_portfolio_models(id) ON DELETE CASCADE,
        symbol        VARCHAR(16) NOT NULL,
        nome          VARCHAR(200),
        setor         TEXT,
        industria     TEXT,
        weight        NUMERIC(12,8),
        entry_score   NUMERIC(18,8),
        fundamental_score NUMERIC(18,8),
        coverage      NUMERIC(12,4),
        rank_score    INTEGER,
        meta_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (model_id, symbol)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_us_portfolio_models_user_status
    ON us_portfolio_models (user_id, status, created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_us_portfolio_model_items_model_weight
    ON us_portfolio_model_items (model_id, weight DESC)
    """,
    # Mesmo endurecimento das carteiras B3/FII: RLS (protege acesso via Supabase
    # API/anon key; a conexao do app, role postgres, bypassa) e no maximo um
    # modelo ativo por usuario.
    "ALTER TABLE us_portfolio_models ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE us_portfolio_model_items ENABLE ROW LEVEL SECURITY",
    """
    DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='public'
              AND tablename='us_portfolio_models'
              AND policyname='us_portfolio_models_owner_all'
        ) THEN
            CREATE POLICY us_portfolio_models_owner_all ON us_portfolio_models
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
              AND tablename='us_portfolio_model_items'
              AND policyname='us_portfolio_model_items_owner_all'
        ) THEN
            CREATE POLICY us_portfolio_model_items_owner_all ON us_portfolio_model_items
                USING (EXISTS (
                    SELECT 1 FROM us_portfolio_models m
                    WHERE m.id = model_id AND m.user_id = auth.uid()
                ))
                WITH CHECK (EXISTS (
                    SELECT 1 FROM us_portfolio_models m
                    WHERE m.id = model_id AND m.user_id = auth.uid()
                ));
        END IF;
    END; $$
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_us_portfolio_models_active_per_user
    ON us_portfolio_models (user_id)
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


def _ensure_tables(conn) -> None:
    for ddl in DDL_SQL:
        conn.execute(text(ddl))


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
        _ensure_tables(conn)
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
    from core.portfolio.capture import capture_snapshots
    capture_snapshots("us", model_id, items, params, owner_id=owner)

    load_active_us_portfolio_model.clear()
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

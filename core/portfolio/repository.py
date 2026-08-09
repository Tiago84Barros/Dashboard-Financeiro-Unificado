"""Persistencia dos snapshots analiticos.

Unico modulo do pacote com SQL. Compativel com PostgreSQL (producao) e SQLite
(testes) por meio de dois fragmentos escolhidos pelo dialeto.

Coberto por tests/test_portfolio_repository.py.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import text

from core.portfolio.models import AssetSnapshot
from core.portfolio.registry import SPECS, get_spec
from core.portfolio.snapshots import canonical_json

RETENTION_ARCHIVED = 5

_TABELA = "portfolio_asset_snapshots"


def _resolve_engine(engine):
    if engine is not None:
        return engine
    from core.database import get_engine
    eng = get_engine()
    if eng is None:
        raise RuntimeError("Banco unificado nao configurado.")
    return eng


def _resolve_owner(owner_id):
    if owner_id:
        return str(owner_id)
    from core.config import settings
    if not settings.OWNER_USER_ID:
        raise RuntimeError("OWNER_USER_ID nao configurado; snapshot nao pode ser gravado.")
    return str(settings.OWNER_USER_ID)


def _json_placeholder(engine) -> str:
    """PostgreSQL exige cast explicito de texto para JSONB; SQLite nao tem o tipo."""
    return "CAST(:payload AS jsonb)" if engine.dialect.name == "postgresql" else ":payload"


def _decode(valor):
    """Le a coluna payload: dict no PostgreSQL (JSONB), texto no SQLite."""
    if isinstance(valor, (dict, list)):
        return valor
    return json.loads(valor)


def save_snapshots(snapshots: list[AssetSnapshot], *, engine=None, owner_id=None) -> int:
    """Grava ou atualiza os snapshots. Retorna quantos foram persistidos."""
    if not snapshots:
        return 0

    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    placeholder = _json_placeholder(eng)

    sql = text(f"""
        INSERT INTO {_TABELA} (
            id, user_id, asset_class, model_id, symbol,
            schema_version, as_of_date, payload, payload_digest
        )
        VALUES (
            :id, :user_id, :asset_class, :model_id, :symbol,
            :schema_version, :as_of_date, {placeholder}, :payload_digest
        )
        ON CONFLICT (asset_class, model_id, symbol) DO UPDATE SET
            schema_version = EXCLUDED.schema_version,
            as_of_date     = EXCLUDED.as_of_date,
            payload        = EXCLUDED.payload,
            payload_digest = EXCLUDED.payload_digest
    """)

    with eng.begin() as conn:
        for snap in snapshots:
            get_spec(snap.asset_class)          # valida a classe antes de gravar
            conn.execute(sql, {
                "id": str(uuid.uuid4()),
                "user_id": owner,
                "asset_class": snap.asset_class,
                "model_id": str(snap.model_id),
                "symbol": snap.symbol,
                "schema_version": snap.schema_version,
                "as_of_date": snap.as_of_date.isoformat(),
                "payload": canonical_json(snap.payload),
                "payload_digest": snap.digest,
            })
    return len(snapshots)


def load_snapshots(asset_class: str, model_id: str, *, engine=None) -> dict[str, dict]:
    """Devolve {simbolo: payload} dos snapshots de um modelo."""
    spec = get_spec(asset_class)
    eng = _resolve_engine(engine)

    with eng.connect() as conn:
        linhas = conn.execute(
            text(f"""
                SELECT symbol, payload FROM {_TABELA}
                WHERE asset_class = :ac AND model_id = :mid
                ORDER BY symbol
            """),
            {"ac": spec.key, "mid": str(model_id)},
        ).mappings().all()

    return {linha["symbol"]: _decode(linha["payload"]) for linha in linhas}


def prune_orphans(*, engine=None) -> int:
    """Remove snapshots cujo modelo nao existe mais. Retorna quantos sairam.

    Necessario porque model_id e polimorfico e nao tem FK. Ver a nota de
    modelagem em supabase_unificado/schema/049_portfolio_asset_snapshots.sql.
    """
    eng = _resolve_engine(engine)
    removidos = 0

    with eng.begin() as conn:
        for key in sorted(SPECS):
            spec = SPECS[key]
            resultado = conn.execute(
                text(f"""
                    DELETE FROM {_TABELA}
                    WHERE asset_class = :ac
                      AND model_id NOT IN (SELECT id FROM {spec.models_table})
                """),
                {"ac": spec.key},
            )
            removidos += int(resultado.rowcount or 0)
    return removidos


def apply_retention(asset_class: str, *, engine=None, keep: int = RETENTION_ARCHIVED) -> int:
    """Descarta o payload das versoes arquivadas alem das `keep` mais recentes.

    A versao ativa nunca e afetada. Retorna quantos modelos perderam o payload.
    """
    spec = get_spec(asset_class)
    eng = _resolve_engine(engine)

    with eng.begin() as conn:
        arquivadas = [
            linha["id"] for linha in conn.execute(
                text(f"""
                    SELECT id FROM {spec.models_table}
                    WHERE status = 'archived'
                    ORDER BY created_at DESC, id DESC
                """)
            ).mappings().all()
        ]
        alvo = arquivadas[keep:]
        if not alvo:
            return 0

        for model_id in alvo:
            conn.execute(
                text(f"DELETE FROM {_TABELA} WHERE asset_class = :ac AND model_id = :mid"),
                {"ac": spec.key, "mid": str(model_id)},
            )
    return len(alvo)

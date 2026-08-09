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


_TABELA_ALVO = "portfolio_allocation_targets"


def active_model_id(asset_class: str, *, engine=None, owner_id=None) -> str | None:
    """Id do modelo ativo do dono para a classe, ou None se nao houver."""
    spec = get_spec(asset_class)
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)

    with eng.connect() as conn:
        linha = conn.execute(
            text(f"""
                SELECT id FROM {spec.models_table}
                WHERE user_id = :uid AND status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            """),
            {"uid": owner},
        ).mappings().first()
    return str(linha["id"]) if linha else None


def load_active_snapshots(asset_class: str, *, engine=None, owner_id=None) -> dict[str, dict]:
    """{simbolo: payload} do modelo ATIVO da classe. Vazio se nao houver modelo."""
    model_id = active_model_id(asset_class, engine=engine, owner_id=owner_id)
    if not model_id:
        return {}
    return load_snapshots(asset_class, model_id, engine=engine)


def _normalizar_alvos(targets: dict) -> dict[str, float]:
    """Valida as classes e normaliza os pesos para somar 1."""
    limpos: dict[str, float] = {}
    for chave, valor in targets.items():
        spec = get_spec(chave)              # levanta KeyError em classe desconhecida
        peso = float(valor or 0.0)
        if peso < 0:
            raise ValueError(f"peso negativo para a classe {spec.key!r}")
        limpos[spec.key] = peso

    total = sum(limpos.values())
    if total <= 0:
        raise ValueError("a soma dos pesos da alocacao-alvo precisa ser maior que zero")
    return {k: limpos[k] / total for k in sorted(limpos)}


def save_allocation_targets(targets: dict[str, float], *, total_brl: float | None = None,
                            notes: str = "", engine=None, owner_id=None) -> str:
    """Salva a alocacao-alvo ativa, arquivando a anterior. Devolve o id."""
    normalizados = _normalizar_alvos(targets)
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)
    placeholder = ("CAST(:targets_json AS jsonb)"
                   if eng.dialect.name == "postgresql" else ":targets_json")
    novo_id = str(uuid.uuid4())

    with eng.begin() as conn:
        conn.execute(
            text(f"UPDATE {_TABELA_ALVO} SET status = 'archived' "
                 f"WHERE user_id = :uid AND status = 'active'"),
            {"uid": owner},
        )
        conn.execute(
            text(f"""
                INSERT INTO {_TABELA_ALVO}
                    (id, user_id, status, total_brl, targets_json, notes)
                VALUES
                    (:id, :uid, 'active', :total_brl, {placeholder}, :notes)
            """),
            {
                "id": novo_id, "uid": owner, "total_brl": total_brl,
                "targets_json": canonical_json(normalizados), "notes": notes or "",
            },
        )
    return novo_id


def load_allocation_targets(*, engine=None, owner_id=None) -> dict:
    """Alocacao-alvo ativa do dono. Estrutura vazia se nao houver."""
    eng = _resolve_engine(engine)
    owner = _resolve_owner(owner_id)

    with eng.connect() as conn:
        linha = conn.execute(
            text(f"""
                SELECT total_brl, targets_json, notes FROM {_TABELA_ALVO}
                WHERE user_id = :uid AND status = 'active'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
            """),
            {"uid": owner},
        ).mappings().first()

    if not linha:
        return {"targets": {}, "total_brl": None, "notes": ""}

    alvos = _decode(linha["targets_json"]) or {}
    total = linha["total_brl"]
    return {
        "targets": {str(k): float(v) for k, v in sorted(alvos.items())},
        "total_brl": float(total) if total is not None else None,
        "notes": linha["notes"] or "",
    }

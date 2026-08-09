"""Modelo de dados do snapshot analitico. Sem I/O.

Coberto por tests/test_portfolio_models.py.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from core.portfolio.snapshots import build_payload, payload_digest


@dataclass(frozen=True)
class AssetSnapshot:
    """Snapshot analitico de um ativo dentro de uma carteira-modelo."""

    asset_class: str
    model_id: str
    symbol: str
    as_of_date: dt.date
    payload: dict = field(default_factory=dict)

    @classmethod
    def from_blocks(cls, *, asset_class: str, model_id: str, symbol: str,
                    as_of_date: dt.date, blocks: dict) -> "AssetSnapshot":
        """Constrói snapshot a partir de blocos, normalizando e validando.

        Args:
            asset_class: Classe do ativo (será normalizada para minúsculas)
            model_id: ID do modelo
            symbol: Símbolo do ativo (será normalizado para maiúsculas)
            as_of_date: Data do snapshot
            blocks: Blocos que serão passados para build_payload

        Returns:
            Instância de AssetSnapshot

        Raises:
            ValueError: Se symbol for vazio ou apenas espaços em branco
        """
        simbolo = str(symbol or "").strip().upper()
        if not simbolo:
            raise ValueError("symbol vazio ao montar AssetSnapshot")
        return cls(
            asset_class=str(asset_class or "").strip().lower(),
            model_id=str(model_id),
            symbol=simbolo,
            as_of_date=as_of_date,
            payload=build_payload(blocks),
        )

    @property
    def digest(self) -> str:
        """Digest SHA-256 do payload canonico."""
        return payload_digest(self.payload)

    @property
    def schema_version(self) -> int:
        """Versão do schema recuperada do payload."""
        return int(self.payload.get("schema_version") or 0)

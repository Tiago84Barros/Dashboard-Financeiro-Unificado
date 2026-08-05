"""Camada canonica de persistencia das carteiras-modelo.

Aditiva: nao substitui core/b3_portfolio_model.py e irmaos, apenas guarda o
snapshot analitico rico que eles nao guardavam.
"""
from core.portfolio.snapshots import SCHEMA_VERSION, build_payload, payload_digest

__all__ = ["SCHEMA_VERSION", "build_payload", "payload_digest"]

"""Log de lacunas: tudo o que o app admite nao saber, gravado para um agente
de IA triar e corrigir. Spec: docs/superpowers/specs/2026-09-28-log-de-lacunas-design.md
"""
from core.lacunas.evento import Lacuna
from core.lacunas.registro import registrar_lacuna

__all__ = ["Lacuna", "registrar_lacuna"]

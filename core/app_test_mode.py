"""Modo sintético e isolado para a validação visual do App 4.

Este módulo não importa configuração, repositórios ou providers.  Ele existe
para que a rota seja decidida *antes* de carregar uma tela que possa abrir uma
conexão ou consultar dados de mercado.
"""
from __future__ import annotations

import os

TEST_MODE_ENV = "APP_TEST_MODE"
TEST_MODE_VIEW = "app_test_mode"
TESTABLE_ROUTES = frozenset({
    "empresas_b3",
    "fiis",
    "empresas_americanas",
    "portfolio_global",
})
VALID_STATES = frozenset({"loaded", "empty", "partial", "error"})
STATE_OPTIONS = ("loaded", "empty", "partial", "error")


def is_app_test_mode(value: str | None = None) -> bool:
    """Retorna se o modo sintético foi habilitado explicitamente."""
    raw_value = os.getenv(TEST_MODE_ENV) if value is None else value
    return str(raw_value or "").strip().lower() == "true"


def module_for_route(route: str, *, test_mode: bool | None = None) -> str:
    """Resolve uma view; no modo sintético jamais retorna uma view de produção."""
    enabled = is_app_test_mode() if test_mode is None else test_mode
    if enabled:
        return TEST_MODE_VIEW
    return route


def state_from_value(value: str | None) -> str:
    """Normaliza o estado visual; valores desconhecidos preservam ``loaded``."""
    state = str(value or "").strip().lower()
    return state if state in VALID_STATES else "loaded"

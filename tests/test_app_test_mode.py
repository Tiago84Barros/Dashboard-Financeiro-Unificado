from __future__ import annotations

import ast
import importlib
import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

from core.app_test_mode import (
    TEST_MODE_VIEW,
    is_app_test_mode,
    module_for_route,
    state_from_value,
)


class _FakeStreamlit(ModuleType):
    def __init__(self, selected_menu: str | None = None):
        super().__init__("streamlit")
        self.selected_menu = selected_menu
        self.imported_menu_options: list[tuple[str, ...]] = []
        self.sidebar = nullcontext()

    def set_page_config(self, **_kwargs):
        pass

    def markdown(self, *_args, **_kwargs):
        pass

    def radio(self, _label, options, **_kwargs):
        options = tuple(options)
        self.imported_menu_options.append(options)
        return self.selected_menu or options[0]

    def divider(self):
        pass

    def caption(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def _execute_app(monkeypatch, *, env_value: str | None, selected_menu: str | None):
    """Executa o router sem importar Streamlit nem qualquer view real."""
    if env_value is None:
        monkeypatch.delenv("APP_TEST_MODE", raising=False)
    else:
        monkeypatch.setenv("APP_TEST_MODE", env_value)

    fake_st = _FakeStreamlit(selected_menu)
    imported: list[str] = []
    fake_view = SimpleNamespace(render=lambda: None)

    monkeypatch.setitem(sys.modules, "streamlit", fake_st)
    monkeypatch.setitem(sys.modules, "core.auth", SimpleNamespace(verificar_autenticacao=lambda: None))
    monkeypatch.setitem(sys.modules, "core.config", SimpleNamespace(settings=SimpleNamespace(validate=lambda: [])))
    monkeypatch.setitem(sys.modules, "design.componentes", SimpleNamespace(mensagem_erro=lambda *_args: None))
    monkeypatch.setitem(sys.modules, "design.tema", SimpleNamespace(aplicar_tema=lambda: None))

    def fake_import_module(name: str):
        imported.append(name)
        return fake_view

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    runpy.run_path("app.py", run_name="app_test_mode_router_test")
    return fake_st, imported


def test_mode_requires_explicit_true_value(monkeypatch):
    monkeypatch.delenv("APP_TEST_MODE", raising=False)
    assert is_app_test_mode("true") is True
    assert is_app_test_mode("TRUE") is True
    assert is_app_test_mode("1") is False
    assert is_app_test_mode("false") is False
    assert is_app_test_mode() is False


def test_test_mode_redirects_every_route_to_the_synthetic_view():
    for route in ("empresas_b3", "fiis", "empresas_americanas", "portfolio_global"):
        assert module_for_route(route, test_mode=True) == TEST_MODE_VIEW
        assert module_for_route(route, test_mode=False) == route

    assert module_for_route("controle_financeiro", test_mode=True) == TEST_MODE_VIEW


def test_test_mode_router_uses_only_synthetic_default_and_every_menu_route(monkeypatch):
    expected_labels = (
        "🏢 Empresas B3",
        "🌎 Empresas Americanas",
        "🏬 Seleção de FIIs",
        "🌐 Portfólio Global",
    )

    for selected_menu in (None, *expected_labels):
        fake_st, imported = _execute_app(
            monkeypatch,
            env_value="true",
            selected_menu=selected_menu,
        )

        assert fake_st.imported_menu_options == [expected_labels]
        assert imported == [f"views.{TEST_MODE_VIEW}"]


def test_disabled_or_missing_test_mode_preserves_production_route(monkeypatch):
    for env_value in (None, "false"):
        fake_st, imported = _execute_app(
            monkeypatch,
            env_value=env_value,
            selected_menu="📊 Dashboard Geral",
        )

        assert "📊 Dashboard Geral" in fake_st.imported_menu_options[0]
        assert imported == ["views.dashboard_geral"]


def test_unknown_visual_state_defaults_to_loaded():
    assert state_from_value("partial") == "partial"
    assert state_from_value("unknown") == "loaded"
    assert state_from_value(None) == "loaded"


def test_synthetic_view_has_no_production_view_or_data_provider_imports():
    source = Path("views/app_test_mode.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert not any(name.startswith(("views.", "core.b3_", "core.market_", "core.us_", "data_pipeline"))
                   for name in imported_modules)
    assert "yfinance" not in imported_modules


def test_synthetic_view_uses_current_stretch_width_api():
    source = Path("views/app_test_mode.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    dataframe_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dataframe"
    ]

    assert len(dataframe_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in dataframe_calls[0].keywords}
    assert "use_container_width" not in keywords
    assert isinstance(keywords["width"], ast.Constant)
    assert keywords["width"].value == "stretch"

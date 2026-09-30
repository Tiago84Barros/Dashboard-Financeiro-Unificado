"""Nenhum cache imprime a tarja "Running <função>(...)" na tela.

O spinner do `st.cache_data`/`st.cache_resource` mostra o nome qualificado da
função em execução. Quando o cache passa por `core.user_context.user_cache_data`,
o nome que aparece é o da closure interna -- `user_cache_data.<locals>.decorate.
<locals>.cached` --, detalhe de implementação que só polui a tela de quem usa o
app. A varredura vale como portão: decorador novo sem `show_spinner` reprova.
"""
import ast
import pathlib

import pytest

_RAIZ = pathlib.Path(__file__).resolve().parents[1]
_PASTAS = ("core", "views", "design")
# Só os caches diretos: quem passa por `user_cache_data` herda o padrão do
# wrapper, e exigir o argumento em cada chamador seria pedir repetição inútil.
_CACHES = {"cache_data", "cache_resource"}


def _arquivos() -> list[pathlib.Path]:
    arquivos = [_RAIZ / "app.py"]
    for pasta in _PASTAS:
        arquivos.extend(sorted((_RAIZ / pasta).rglob("*.py")))
    return arquivos


def _nome(no: ast.AST) -> str:
    if isinstance(no, ast.Attribute):
        return no.attr
    if isinstance(no, ast.Name):
        return no.id
    return ""


def _decoradores_de_cache():
    """(arquivo, linha, tem_show_spinner) de cada decorador de cache do app."""
    for arq in _arquivos():
        arvore = ast.parse(arq.read_text(encoding="utf-8"), filename=str(arq))
        for no in ast.walk(arvore):
            if not isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in no.decorator_list:
                chamada = dec if isinstance(dec, ast.Call) else None
                alvo = chamada.func if chamada else dec
                if _nome(alvo) not in _CACHES:
                    continue
                kwargs = [k.arg for k in chamada.keywords] if chamada else []
                # `**options` (arg None) chega do chamador: é o caso do próprio
                # wrapper, que põe o padrão antes de repassar.
                explicito = "show_spinner" in kwargs or None in kwargs
                yield arq.relative_to(_RAIZ), dec.lineno, explicito


def test_ha_caches_para_varrer():
    """Guarda da guarda: varredura que não acha nada não prova nada."""
    assert len(list(_decoradores_de_cache())) > 40


@pytest.mark.parametrize(
    ("arquivo", "linha"),
    [(a, n) for a, n, ok in _decoradores_de_cache() if not ok],
)
def test_todo_cache_declara_show_spinner(arquivo, linha):
    pytest.fail(
        f"{arquivo}:{linha} decora um cache sem `show_spinner`. O padrão do "
        "Streamlit é mostrar a tarja 'Running <função>(...)' na tela."
    )


def test_user_cache_data_desliga_o_spinner_por_padrao():
    import inspect

    from core import user_context

    fonte = inspect.getsource(user_context.user_cache_data)
    assert 'options.setdefault("show_spinner", False)' in fonte


def test_chamador_de_user_cache_data_vence_o_padrao(monkeypatch):
    """Quem pedir o spinner de volta continua podendo."""
    from core import user_context

    vistos: list[dict] = []

    def _falso_cache_data(**opcoes):
        vistos.append(opcoes)

        def _decora(f):
            f.clear = lambda: None
            return f

        return _decora

    monkeypatch.setattr(user_context.st, "cache_data", _falso_cache_data)

    user_context.user_cache_data(ttl=60)(lambda: None)
    user_context.user_cache_data(ttl=60, show_spinner="Carregando…")(lambda: None)

    assert vistos[0]["show_spinner"] is False
    assert vistos[1]["show_spinner"] == "Carregando…"

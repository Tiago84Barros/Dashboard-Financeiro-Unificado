"""Regressão do achado A-014: handler generico de erro em app.py (A-013).

app.py isola qualquer excecao levantada ao carregar um modulo de views/: a
tela recebe so ``MSG_ERRO_GENERICO_AO_CARREGAR_MODULO`` (via
``design.componentes.mensagem_erro``), nunca o texto tecnico da excecao; o
detalhe completo vai para o log via ``logger.exception``. O veredito da
rodada 7 (``artifacts/app4_professionalizacao/veredito_final_2026-08-19_rodada7.md``)
confirmou esse comportamento por reproducao manual, mas apontou que nenhum
teste automatizado o exercita — este arquivo fecha essa lacuna.
"""
from __future__ import annotations

import ast
import importlib
import logging
import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

# Não importar `app` diretamente: o módulo executa efeitos de topo de nível
# (st.set_page_config, autenticação) contra o Streamlit real assim que
# importado. O padrão já usado em tests/test_app_test_mode.py é ler a
# constante do próprio código-fonte, sem executar o módulo fora do runpy
# controlado (com os mocks já injetados em sys.modules).
_APP_SOURCE = Path("app.py").read_text(encoding="utf-8")
_APP_TREE = ast.parse(_APP_SOURCE)


def _extrai_valor_constante(node: ast.AST) -> str:
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(v.value) for v in node.values if isinstance(v, ast.Constant)
        )
    raise TypeError(f"Não sei extrair valor de {type(node)!r}")


MSG_ERRO_GENERICO_AO_CARREGAR_MODULO = next(
    _extrai_valor_constante(node.value)
    for node in ast.walk(_APP_TREE)
    if isinstance(node, ast.Assign)
    and any(
        isinstance(target, ast.Name)
        and target.id == "MSG_ERRO_GENERICO_AO_CARREGAR_MODULO"
        for target in node.targets
    )
)

_SEGREDO_TECNICO = "SECRET_PASSWORD_XYZ connection refused on host db.internal:5432"


class _FakeStreamlit(ModuleType):
    """Espelha o fake de tests/test_app_test_mode.py, com foco no branch de erro."""

    def __init__(self, selected_menu: str):
        super().__init__("streamlit")
        self.selected_menu = selected_menu
        self.sidebar = nullcontext()
        self.errors_shown: list[str] = []

    def set_page_config(self, **_kwargs):
        pass

    def markdown(self, *_args, **_kwargs):
        pass

    def radio(self, _label, options, **_kwargs):
        return self.selected_menu

    def divider(self):
        pass

    def caption(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, msg, *_args, **_kwargs):
        # Se o router cair no ramo errado (APP_TEST_MODE), o texto cru
        # apareceria aqui — mantido para o teste poder acusar a regressão.
        self.errors_shown.append(str(msg))


def test_excecao_ao_carregar_modulo_nao_vaza_para_a_tela_e_log_recebe_detalhe(
    monkeypatch, caplog,
):
    """Reproduz o cenario real: importlib.import_module explode ao carregar a view."""
    monkeypatch.delenv("APP_TEST_MODE", raising=False)

    fake_st = _FakeStreamlit(selected_menu="📊 Dashboard Geral")
    mensagens_amigaveis: list[tuple[str, str]] = []

    def fake_mensagem_erro(titulo: str, detalhe: str = "") -> None:
        mensagens_amigaveis.append((titulo, detalhe))

    def fake_import_module(name: str):
        if name == "views.dashboard_geral":
            raise RuntimeError(_SEGREDO_TECNICO)
        raise AssertionError(f"import inesperado: {name}")

    monkeypatch.setitem(sys.modules, "streamlit", fake_st)
    monkeypatch.setitem(
        sys.modules, "core.auth", SimpleNamespace(verificar_autenticacao=lambda: None)
    )
    monkeypatch.setitem(
        sys.modules, "core.config", SimpleNamespace(settings=SimpleNamespace(validate=lambda: []))
    )
    monkeypatch.setitem(
        sys.modules,
        "design.componentes",
        SimpleNamespace(mensagem_erro=fake_mensagem_erro),
    )
    monkeypatch.setitem(sys.modules, "design.tema", SimpleNamespace(aplicar_tema=lambda: None))
    monkeypatch.setattr(importlib, "import_module", fake_import_module)

    with caplog.at_level(logging.ERROR):
        runpy.run_path("app.py", run_name="app_error_handling_test")

    # (a) a mensagem amigavel apareceu, sem fragmento da excecao.
    assert len(mensagens_amigaveis) == 1
    titulo, detalhe = mensagens_amigaveis[0]
    assert titulo == 'Erro ao carregar o módulo "📊 Dashboard Geral"'
    assert detalhe == MSG_ERRO_GENERICO_AO_CARREGAR_MODULO

    # (b) o texto tecnico da excecao NAO aparece em nenhuma saida visivel
    # (nem no titulo/detalhe da mensagem amigavel, nem em st.error, que so
    # seria usado no ramo de APP_TEST_MODE — aqui deve ficar vazio).
    assert _SEGREDO_TECNICO not in titulo
    assert _SEGREDO_TECNICO not in detalhe
    assert fake_st.errors_shown == []

    # (c) o log recebe o detalhe tecnico completo, incluindo o marcador. O
    # texto do RuntimeError só aparece no traceback formatado (exc_info),
    # não na mensagem curta do logger.exception — por isso a asserção lê
    # `caplog.text`, que já inclui o traceback renderizado por registro.
    assert _SEGREDO_TECNICO in caplog.text
    # `logger.exception` inclui o traceback formatado no registro.
    assert any(record.exc_info for record in caplog.records)


def test_modulo_carregado_com_sucesso_nao_aciona_o_handler_de_erro(monkeypatch, caplog):
    """Contraprova: quando a view carrega sem excecao, nada de erro é emitido."""
    monkeypatch.delenv("APP_TEST_MODE", raising=False)

    fake_st = _FakeStreamlit(selected_menu="📊 Dashboard Geral")
    mensagens_amigaveis: list[tuple[str, str]] = []
    fake_view = SimpleNamespace(render=lambda: None)

    monkeypatch.setitem(sys.modules, "streamlit", fake_st)
    monkeypatch.setitem(
        sys.modules, "core.auth", SimpleNamespace(verificar_autenticacao=lambda: None)
    )
    monkeypatch.setitem(
        sys.modules, "core.config", SimpleNamespace(settings=SimpleNamespace(validate=lambda: []))
    )
    monkeypatch.setitem(
        sys.modules,
        "design.componentes",
        SimpleNamespace(mensagem_erro=lambda titulo, detalhe="": mensagens_amigaveis.append(
            (titulo, detalhe)
        )),
    )
    monkeypatch.setitem(sys.modules, "design.tema", SimpleNamespace(aplicar_tema=lambda: None))
    monkeypatch.setattr(importlib, "import_module", lambda _name: fake_view)

    with caplog.at_level(logging.ERROR):
        runpy.run_path("app.py", run_name="app_error_handling_test_ok")

    assert mensagens_amigaveis == []
    assert fake_st.errors_shown == []
    assert not any(record.exc_info for record in caplog.records)

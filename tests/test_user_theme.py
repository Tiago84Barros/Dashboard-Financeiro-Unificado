"""Preferência visual isolada, durável e sem sobrescrita de memória dos chats."""
import runpy
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import text
from streamlit.testing.v1 import AppTest

from core import user_preferences as preferences
from tests import test_multiuser as fixtures
from tests.app_bootstrap_stubs import instalar_stubs_de_bootstrap
from tests.test_multiuser import ADMIN, PASSWORD, accounts, login_as

postgres = fixtures.postgres
user_state = fixtures.user_state


def test_tema_isolado_e_preserva_outras_preferencias(postgres, user_state):
    login_as(user_state, ADMIN)
    other = accounts.create_user("Pessoa tema", "theme@example.test", PASSWORD)
    assert preferences.load_theme() == "dark"
    with postgres.begin() as conn:
        extra = accounts.locked_preferences(conn, ADMIN)
        extra["app4_private_chats_v2"] = {"synthetic": {"messages": []}}
        accounts.write_preferences(conn, ADMIN, extra)
    preferences.save_theme("light")
    assert preferences.load_theme() == "light"
    login_as(user_state, other)
    assert preferences.load_theme() == "dark"
    preferences.save_theme("dark")
    user_state.clear()
    login_as(user_state, ADMIN)
    assert preferences.load_theme() == "light"
    with postgres.connect() as conn:
        extra = conn.execute(text("SELECT extra_settings FROM user_settings WHERE user_id=:uid"),
                             {"uid": ADMIN}).scalar()
    assert extra["app4_private_chats_v2"] == {"synthetic": {"messages": []}}


def test_tema_rejeita_entrada_e_sessao_invalida(user_state):
    with pytest.raises(PermissionError):
        preferences.load_theme()
    with pytest.raises(PermissionError):
        preferences.save_theme("light")
    login_as(user_state, ADMIN)
    with pytest.raises(ValueError):
        preferences.save_theme("<style>untrusted</style>")


SCRIPT = '''
import time
import streamlit as st
from design.theme_selector import current_theme, render_theme_selector
from design.tema import aplicar_tema
st.session_state.setdefault('_app4_user', {'id': 'A', 'expires_at': time.time()+600})
aplicar_tema(current_theme())
render_theme_selector()
'''


def test_interface_salva_recarrega_e_nao_herda_tema_ao_trocar_usuario(monkeypatch):
    import design.theme_selector as selector
    from core.user_context import require_user
    stored = {"A": "light", "B": "dark"}
    monkeypatch.setattr(selector, "load_theme", lambda: stored[require_user()])
    monkeypatch.setattr(selector, "save_theme", lambda theme: stored.update({require_user(): theme}))
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.selectbox[0].value == "light"
    app.selectbox[0].select("dark").run()
    assert not app.exception
    assert stored["A"] == "dark"
    app.selectbox[0].select("light").run()
    fresh = AppTest.from_string(SCRIPT).run()
    assert fresh.selectbox[0].value == "light"
    user = dict(app.session_state["_app4_user"])
    user["id"] = "B"
    app.session_state["_app4_user"] = user
    app.run()
    assert not app.exception
    assert app.selectbox[0].value == "dark"
    assert stored == {"A": "light", "B": "dark"}


def test_falha_de_gravacao_nao_confirma_nem_muda_tema(monkeypatch):
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme", lambda: "dark")

    def fail(theme):
        raise RuntimeError("sensitive details must never be rendered")

    monkeypatch.setattr(selector, "save_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    assert app.session_state["_app4_theme_preference"] == ("A", "dark")
    assert len(app.error) == 1
    assert "sensitive" not in app.error[0].value


def test_falha_de_leitura_bloqueia_gravacao_e_permite_retry(monkeypatch):
    import design.theme_selector as selector

    def fail():
        raise RuntimeError("sensitive")

    monkeypatch.setattr(selector, "load_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.selectbox[0].disabled
    monkeypatch.setattr(selector, "load_theme", lambda: "light")
    app.run()
    assert not app.exception
    assert not app.selectbox[0].disabled
    assert app.selectbox[0].value == "light"


# Resolver o tema acontece no TOPO de app.py, acima de todo o conteúdo da
# página. Por isso este script isola a resolução: o que importa medir é que
# ``current_theme`` não desenha nada -- ver o docstring da função.
SCRIPT_SO_RESOLVE = '''
import time
import streamlit as st
from design.theme_selector import current_theme
st.session_state.setdefault('_app4_user', {'id': 'A', 'expires_at': time.time()+600})
st.session_state['_tema_resolvido'] = current_theme()
st.write("conteudo da pagina")
'''


def test_resolver_o_tema_nao_desenha_nada_acima_da_pagina(monkeypatch):
    """Falha de leitura não pode inserir elemento acima do conteúdo.

    Medido em 22/09/2026 num app isolado: com a estrutura estável, nem o rerun
    do ``file_uploader`` nem elementos novos DENTRO da aba movem a seleção do
    ``st.tabs``; UM elemento a mais acima das abas devolve a seleção para a
    primeira, sempre. O aviso que ``current_theme`` desenhava só nas execuções
    em que a leitura falhava era exatamente esse elemento -- e por isso subir
    arquivo em "Atualização de dados" jogava a pessoa de volta em "Geral" na
    mesma execução em que o app repintava de escuro.
    """
    import design.theme_selector as selector

    def fail():
        raise RuntimeError("banco indisponivel")

    # Sem memória de processo: o que este teste mede é a sessão nova e sem
    # nenhuma leitura boa antes -- o caso em que cair para dark é o resto.
    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "load_theme", fail)
    app = AppTest.from_string(SCRIPT_SO_RESOLVE).run()
    assert not app.exception
    assert app.session_state["_tema_resolvido"] == "dark"
    assert list(app.warning) == [], (
        "current_theme desenhou um aviso no fluxo principal. Esse elemento "
        "aparece acima do st.tabs de Configurações e devolve a pessoa para a "
        "primeira aba. O aviso pertence a render_theme_selector, que já mora "
        "dentro da aba Geral."
    )
    assert list(app.error) == []


def test_falha_de_leitura_avisa_onde_o_tema_se_escolhe(monkeypatch):
    """Tirar o aviso do topo não pode tirá-lo da tela.

    Sem isto, a correção do parágrafo acima viraria silêncio: o app cairia para
    dark e o seletor apareceria desabilitado sem nenhuma explicação.
    """
    import design.theme_selector as selector

    def fail():
        raise RuntimeError("banco indisponivel")

    # Sem memória de processo: o que este teste mede é a sessão nova e sem
    # nenhuma leitura boa antes -- o caso em que cair para dark é o resto.
    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "load_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.selectbox[0].disabled
    assert len(app.warning) == 1
    assert "tema" in app.warning[0].value.lower()
    monkeypatch.setattr(selector, "load_theme", lambda: "light")
    app.run()
    assert list(app.warning) == []


SCRIPT_CONTA_ELEMENTOS = '''
import time
import streamlit as st
from design.tema import aplicar_tema
st.session_state.setdefault('_app4_user', {'id': 'A', 'expires_at': time.time()+600})
aplicar_tema(st.session_state.get('_tema_pedido', 'dark'))
st.write("conteudo da pagina")
'''


def test_aplicar_tema_nao_muda_a_contagem_de_elementos_por_tema():
    """Trocar de tema não pode deslocar o conteúdo da página.

    O CSS claro entrava como um ``st.markdown`` EXTRA: dois elementos no claro
    contra um no escuro. Qualquer execução que resolvesse o tema diferente da
    anterior mudava o número de elementos acima do ``st.tabs`` de
    Configurações, e o Streamlit devolve a seleção para a primeira aba quando o
    grupo de abas muda de posição -- era o "voltei para a aba Geral" que
    acompanhava o "o app ficou escuro".
    """
    escuro = AppTest.from_string(SCRIPT_CONTA_ELEMENTOS).run()
    assert not escuro.exception
    claro = AppTest.from_string(SCRIPT_CONTA_ELEMENTOS)
    claro.session_state["_tema_pedido"] = "light"
    claro.run()
    assert not claro.exception
    assert len(claro.markdown) == len(escuro.markdown), (
        "aplicar_tema desenhou um número de elementos diferente por tema"
    )
    assert "--app-bg" in "".join(bloco.value for bloco in claro.markdown)


def test_falha_de_leitura_mantem_o_ultimo_tema_conhecido(monkeypatch):
    """Falha de leitura não é troca de preferência.

    Enquanto a primeira leitura da sessão falha não há cache, então TODA
    execução relê -- e um job de atualização de dados, que ocupa a única
    conexão do pool, derruba a leitura justamente no clique do botão. Cair para
    dark nessas execuções repintava o app inteiro no meio do trabalho.
    """
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "load_theme", lambda: "light")
    app = AppTest.from_string(SCRIPT_SO_RESOLVE).run()
    assert app.session_state["_tema_resolvido"] == "light"
    del app.session_state["_app4_theme_preference"]

    def fail():
        raise RuntimeError("banco ocupado")

    monkeypatch.setattr(selector, "load_theme", fail)
    app.run()
    assert not app.exception
    assert app.session_state["_tema_resolvido"] == "light"
    assert app.session_state["_app4_theme_load_error"] is True


# -- o tema não pode depender do portão de autenticação -----------------------

class _PortaoInterrompeu(Exception):
    """Faz o papel do ``st.stop()`` que ``verificar_autenticacao`` dispara."""


class _StreamlitDeBootstrap(ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.sidebar = nullcontext()

    def set_page_config(self, **_kwargs):
        pass

    def markdown(self, *_args, **_kwargs):
        pass


def test_o_tema_e_aplicado_antes_do_portao_de_autenticacao(monkeypatch):
    """Execução interrompida pelo portão também tem de sair com tema.

    ``aplicar_tema`` morava DEPOIS de ``verificar_autenticacao()``. Toda
    execução que o portão interrompe -- a tela de login e o aviso de sessão
    que não deu para validar -- desenhava sem CSS nenhum, e o tema base do
    ``.streamlit/config.toml`` é escuro: o app aparecia escuro sem ninguém
    ter trocado nada. É o que acontece no meio de uma importação em
    Configurações, que ocupa a única conexão do pool e faz a validação da
    sessão falhar na execução seguinte.
    """
    temas: list[str] = []

    def portao():
        raise _PortaoInterrompeu

    instalar_stubs_de_bootstrap(
        monkeypatch,
        _StreamlitDeBootstrap(),
        core_auth=SimpleNamespace(verificar_autenticacao=portao,
                                  encerrar_sessao=lambda: None),
        design_tema=SimpleNamespace(aplicar_tema=lambda theme="dark": temas.append(theme)),
        design_theme_selector=SimpleNamespace(
            current_theme=lambda: "light",
            tema_para_pintar=lambda: "light",
            render_theme_selector=lambda: None,
        ),
    )
    with pytest.raises(_PortaoInterrompeu):
        runpy.run_path("app.py", run_name="app_tema_antes_do_portao_test")
    assert temas == ["light"], (
        "o tema não foi aplicado antes do portão: a tela que o portão desenha "
        "sai com o tema base do config.toml, que é escuro"
    )


def test_tema_para_pintar_nunca_levanta_e_nao_adivinha_conta(monkeypatch):
    """Antes do portão não há sessão validada -- e pode não haver conta.

    ``current_theme`` levanta ``PermissionError`` sem conta na sessão, de
    propósito. Aplicar o tema antes do portão exige um resolvedor que devolva
    algo em qualquer caso, e sem chutar a preferência de quem não se
    identificou.
    """
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "load_theme", lambda: "light")
    script = '''
import streamlit as st
from design.theme_selector import tema_para_pintar
st.session_state['_tema_pintado'] = tema_para_pintar()
'''
    app = AppTest.from_string(script).run()
    assert not app.exception
    assert app.session_state["_tema_pintado"] == "dark"


def test_sessao_nova_do_mesmo_usuario_nao_cai_para_dark_sozinha(monkeypatch):
    """A preferência sobrevive à sessão enquanto o processo é o mesmo.

    Reconexão do navegador, aba reaberta, app que voltou a aceitar conexão:
    a sessão nova começa sem ``st.session_state``, a primeira leitura do tema
    é obrigatória e, quando ela falha, não havia último tema de sessão para
    segurar a preferência -- o app repintava de escuro quem escolheu claro.
    A leitura falha justamente quando uma importação longa de Configurações
    está com a única conexão do pool.
    """
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "load_theme", lambda: "light")
    primeira = AppTest.from_string(SCRIPT_SO_RESOLVE).run()
    assert primeira.session_state["_tema_resolvido"] == "light"

    def fail():
        raise RuntimeError("pool ocupado pela importacao")

    monkeypatch.setattr(selector, "load_theme", fail)
    nova = AppTest.from_string(SCRIPT_SO_RESOLVE).run()
    assert not nova.exception
    assert nova.session_state["_tema_resolvido"] == "light", (
        "sessão nova com leitura falhando caiu para dark e repintou o app"
    )

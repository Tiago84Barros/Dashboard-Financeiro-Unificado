"""Preferência visual isolada, durável e sem sobrescrita de memória dos chats."""
import re
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
    # Conta que nunca escolheu tema responde ausência, não "dark": são duas
    # respostas diferentes e confundi-las apagava a memória de quem está no claro.
    assert preferences.load_theme() is None
    with postgres.begin() as conn:
        extra = accounts.locked_preferences(conn, ADMIN)
        extra["app4_private_chats_v2"] = {"synthetic": {"messages": []}}
        accounts.write_preferences(conn, ADMIN, extra)
    preferences.save_theme("light")
    assert preferences.load_theme() == "light"
    login_as(user_state, other)
    assert preferences.load_theme() is None
    preferences.save_theme("dark")
    user_state.clear()
    login_as(user_state, ADMIN)
    assert preferences.load_theme() == "light"
    with postgres.connect() as conn:
        extra = conn.execute(text("SELECT extra_settings FROM user_settings WHERE user_id=:uid"),
                             {"uid": ADMIN}).scalar()
    assert extra["app4_private_chats_v2"] == {"synthetic": {"messages": []}}


class _ConexaoEspiada:
    """Registra SQL e parâmetros, e delega o resto para a conexão real."""

    def __init__(self, conexao, registro):
        self._conexao = conexao
        self._registro = registro

    def execute(self, comando, parametros=None, *args, **kwargs):
        self._registro.append((str(comando), parametros or {}))
        if parametros is None:
            return self._conexao.execute(comando, *args, **kwargs)
        return self._conexao.execute(comando, parametros, *args, **kwargs)

    def __getattr__(self, nome):
        return getattr(self._conexao, nome)


class _ContextoEspiado:
    def __init__(self, contexto, registro):
        self._contexto = contexto
        self._registro = registro

    def __enter__(self):
        return _ConexaoEspiada(self._contexto.__enter__(), self._registro)

    def __exit__(self, *erro):
        return self._contexto.__exit__(*erro)


class _EngineEspiada:
    def __init__(self, engine, registro):
        self._engine = engine
        self._registro = registro

    def connect(self):
        return _ContextoEspiado(self._engine.connect(), self._registro)

    def begin(self):
        return _ContextoEspiado(self._engine.begin(), self._registro)


def test_tema_nao_carrega_nem_reescreve_o_documento_inteiro(postgres, user_state, monkeypatch):
    """O custo do tema não pode crescer com o que NÃO é tema.

    ``extra_settings`` é um documento só por conta: o tema divide a coluna com
    o histórico dos chats e o das análises. Ler e gravar o documento inteiro
    para mexer numa palavra fazia a conta grande pagar pelo que tem dentro --
    medido em 25/10/2026 contra o Supabase, 480 KB de ``extra_settings``
    custavam 225 ms de leitura e 561 ms de gravação (picos de 1,95 s), contra
    47 ms e 242 ms de uma conta pequena. Na única conexão do pool do Cloud é
    tempo suficiente para a escolha do tema não chegar ao banco: o claro "não
    entrava" exatamente na conta que tem conversa guardada.

    O que este teste tranca não é o tempo -- que depende da rede -- e sim a
    causa dele: nem a leitura nem a gravação do tema podem trazer ou mandar o
    documento. Por isso a asserção é sobre o SQL e os parâmetros, e não sobre
    o relógio.
    """
    login_as(user_state, ADMIN)
    conversa = {"synthetic": {"messages": ["x" * 200_000], "updated": 2_000_000_000}}
    with postgres.begin() as conn:
        extra = accounts.locked_preferences(conn, ADMIN)
        extra["app4_private_chats_v2"] = conversa
        accounts.write_preferences(conn, ADMIN, extra)

    registro: list[tuple[str, object]] = []
    monkeypatch.setattr(preferences, "_engine",
                        lambda: _EngineEspiada(postgres, registro))
    preferences.save_theme("light")
    assert preferences.load_theme() == "light"

    # Nenhum parâmetro carrega o documento: a gravação de hoje mandava os
    # 200 KB da conversa de volta só para trocar "dark" por "light".
    gordos = [(sql, chave) for sql, parametros in registro
              for chave, valor in dict(parametros).items()
              if len(str(valor)) > 1_000]
    assert not gordos, f"parâmetro grande demais para trocar o tema: {gordos}"
    # E nenhuma consulta pede a coluna crua: o documento não sai do servidor.
    cruas = [sql for sql, _ in registro
             if re.search(r"select\s+extra_settings\s+from", sql, re.IGNORECASE)]
    assert not cruas, f"o tema leu o documento inteiro: {cruas}"

    # O resto da coluna continua de pé, byte por byte.
    with postgres.connect() as conn:
        extra = conn.execute(text(
            "SELECT extra_settings FROM user_settings WHERE user_id=:uid"
        ), {"uid": ADMIN}).scalar()
    assert extra["app4_private_chats_v2"] == conversa
    assert extra["app4_theme"] == "light"


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


def test_falha_de_gravacao_aplica_o_comando_e_avisa_sem_desfazer(monkeypatch):
    """Banco indisponível no clique não desfaz o comando de quem clicou.

    Era o contrário: a exceção devolvia o seletor ao tema anterior. Então um
    comando explícito era descartado por disputa de conexão -- a única do pool
    do Cloud, que a mesma execução gasta revalidando a sessão e rodando o que
    Configurações faz --, e a pessoa via "cliquei e ele não obedece", com o app
    seguindo (corretamente) no tema antigo para sempre depois disso. Agora a
    escolha vale na hora, em todas as memórias locais, e o aviso diz a única
    coisa que de fato falhou: guardar na conta.
    """
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme", lambda: "dark")
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())
    tentativas: list[str] = []

    def fail(theme):
        tentativas.append(theme)
        raise RuntimeError("sensitive details must never be rendered")

    monkeypatch.setattr(selector, "save_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    # O comando valeu: cache da sessão, seletor e tema pintado estão em light.
    assert app.session_state["_app4_theme_preference"] == ("A", "light")
    assert app.session_state["_app4_theme_choice"] == "light"
    assert app.session_state["_app4_theme_not_saved"] == "light"
    # Insistiu antes de desistir, em vez de amarrar o comando a um instante.
    assert len(tentativas) >= selector._TENTATIVAS_DE_GRAVACAO
    assert all(t == "light" for t in tentativas)
    # Aviso, não erro -- e sem vazar o detalhe da exceção.
    assert not app.error
    assert len(app.warning) == 1
    assert "sensitive" not in app.warning[0].value


def test_comando_de_tema_sobrevive_a_uma_falha_passageira(monkeypatch):
    """A gravação que falhou no clique é retomada na próxima vez que a tela abre."""
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme", lambda: "dark")
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())
    gravado: list[str] = []
    indisponivel = {"sim": True}

    def save(theme):
        if indisponivel["sim"]:
            raise RuntimeError("conexão ocupada")
        gravado.append(theme)

    monkeypatch.setattr(selector, "save_theme", save)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert app.session_state["_app4_theme_not_saved"] == "light"
    indisponivel["sim"] = False
    app.run()
    assert not app.exception
    assert gravado == ["light"]
    assert "_app4_theme_not_saved" not in app.session_state
    assert not app.warning


def test_tema_ausente_na_conta_nao_apaga_o_que_a_sessao_ja_sabia(monkeypatch):
    """Leitura que deu certo e não achou preferência não é comando de escurecer.

    ``load_theme`` devolvia "dark" para a ausência, e esse "dark" era adotado
    como escolha de verdade: entrava no cache, no lembrete do processo e no
    cookie, apagando a memória de quem estava no claro.
    """
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme", lambda: None)
    monkeypatch.setattr(selector, "save_theme", lambda theme: None)
    selector._LEMBRADO["A"] = "light"
    try:
        app = AppTest.from_string(SCRIPT).run()
        assert not app.exception
        assert app.selectbox[0].value == "light"
        assert app.session_state["_app4_theme_preference"] == ("A", "light")
        assert selector._LEMBRADO["A"] == "light"
    finally:
        selector._LEMBRADO.pop("A", None)


def test_conta_sem_preferencia_registra_o_tema_que_ja_esta_na_tela(monkeypatch):
    """Quem está no claro e quer continuar no claro também precisa conseguir.

    O ``selectbox`` não dispara ``on_change`` quando se escolhe o valor que já
    está marcado. Então a conta sem tema gravado, pintada de claro pelo cookie,
    não tinha nenhum gesto capaz de registrar essa preferência: ficava para
    sempre sem linha no banco e voltava ao escuro em qualquer outro navegador.
    Abrir o seletor grava a cor que já está na tela -- gravar o que já se vê
    não muda cor nenhuma.
    """
    import design.theme_selector as selector
    gravado: list[str] = []
    monkeypatch.setattr(selector, "load_theme", lambda: None)
    monkeypatch.setattr(selector, "save_theme", lambda theme: gravado.append(theme))
    selector._LEMBRADO["A"] = "light"
    try:
        app = AppTest.from_string(SCRIPT).run()
        assert not app.exception
        assert gravado == ["light"], "a preferência ausente não foi registrada"
        assert "_app4_theme_unset" not in app.session_state
        assert not app.warning
    finally:
        selector._LEMBRADO.pop("A", None)


def test_ultimo_tema_bom_nao_atravessa_de_uma_conta_para_outra(monkeypatch):
    """O último tema bom carrega dono; sem isso ele era a memória anônima.

    Com a leitura falhando, trocar de conta no mesmo navegador pintava a conta
    nova com a preferência da anterior -- a mesma mudança de tema sem
    consentimento que a #566 fechou no cookie.
    """
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme", lambda: "light")
    monkeypatch.setattr(selector, "save_theme", lambda theme: None)
    app = AppTest.from_string(SCRIPT).run()
    assert app.session_state["_app4_theme_last_ok"] == ("A", "light")

    def fail():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(selector, "load_theme", fail)
    selector._LEMBRADO.pop("B", None)
    user = dict(app.session_state["_app4_user"])
    user["id"] = "B"
    app.session_state["_app4_user"] = user
    app.run()
    assert not app.exception
    # Nada de herdar "light" da conta A: sem preferência conhecida, escuro.
    # A asserção é sobre o que foi PINTADO -- o seletor fica desabilitado e
    # ainda exibe o rótulo da conta anterior, que é cosmético; o que mudaria
    # a cor da tela sem consentimento é o CSS.
    assert selector._ultimo_ok("B") is None
    pintado = " ".join(bloco.value for bloco in app.markdown)
    assert "#f5f7fb" not in pintado, "a conta B foi pintada com o claro da conta A"


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
    lembrados: list[str] = []

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
            lembrar_no_navegador=lambda tema, uid="": lembrados.append((tema, uid)),
            render_theme_selector=lambda: None,
        ),
    )
    with pytest.raises(_PortaoInterrompeu):
        runpy.run_path("app.py", run_name="app_tema_antes_do_portao_test")
    assert temas == ["light"], (
        "o tema não foi aplicado antes do portão: a tela que o portão desenha "
        "sai com o tema base do config.toml, que é escuro"
    )
    assert [tema for tema, _ in lembrados] == ["light"], (
        "o tema pintado não foi lembrado no navegador: a execução seguinte que "
        "perder a conta da sessão volta a cair para dark"
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


def _cookies_do_navegador(monkeypatch, **valores):
    """Finge os cookies que o servidor recebeu no aperto de mão do websocket."""
    import streamlit as st

    monkeypatch.setattr(type(st.context), "cookies", valores, raising=False)


def test_sem_conta_identificada_pinta_o_tema_lembrado_pelo_navegador(monkeypatch):
    """Era o que escurecia a tela ao anexar o PDF da Nomad.

    ``principal()`` devolve ``{}`` em mais situações do que a tela de login:
    a sessão expira em 12 h e nunca é renovada, e uma sessão nova do Streamlit
    -- reconexão do websocket no meio de uma execução longa -- nasce com o
    ``session_state`` vazio. Nesse ponto ``tema_para_pintar`` devolvia ``dark``
    e repintava de escuro quem tinha escolhido claro, sem autorização nenhuma.

    Nem ``_LEMBRADO`` (do processo) nem ``st.session_state`` (da sessão)
    alcançam esse caso: os dois só respondem com conta identificada. O cookie
    é do navegador e sobrevive.
    """
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    _cookies_do_navegador(monkeypatch, app4_tema="light")
    assert selector.tema_para_pintar() == "light", (
        "execução sem conta na sessão caiu para dark e repintou o app de quem "
        "escolheu claro"
    )


def test_sem_conta_e_sem_cookie_nao_chuta_preferencia(monkeypatch):
    """Navegador que nunca pintou nada continua no tema base do config.toml."""
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    _cookies_do_navegador(monkeypatch)
    assert selector.tema_para_pintar() == "dark"
    _cookies_do_navegador(monkeypatch, app4_tema="sepia")
    assert selector.tema_para_pintar() == "dark"


def test_processo_novo_com_leitura_falhando_usa_o_cookie(monkeypatch):
    """Deploy reinicia o processo e zera ``_LEMBRADO``; o navegador não zera.

    Primeira execução depois de um deploy, com o pool ocupado por uma
    importação longa: não há tema em cache de sessão nem de processo. Sem o
    cookie, a única resposta possível era ``dark``.

    O cookie vem com a etiqueta da conta desde 12/10/2026: o mesmo navegador
    serve mais de uma conta, e sem a marca ele entregava à seguinte o tema da
    anterior (``test_cookie_de_uma_conta_nao_pinta_a_outra``).
    """
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "require_user", lambda: "conta-1")
    monkeypatch.setattr(selector, "principal", lambda: {"id": "conta-1"})

    def fail():
        raise RuntimeError("pool ocupado pela importacao")

    monkeypatch.setattr(selector, "load_theme", fail)
    _cookies_do_navegador(
        monkeypatch, app4_tema=f"light.{selector._etiqueta('conta-1')}")
    assert selector.tema_para_pintar() == "light"


def test_lembrete_no_navegador_e_um_elemento_fixo_nos_dois_temas(monkeypatch):
    """O número de elementos acima da página não pode depender do tema.

    Mesma armadilha do ``st.warning`` que morava em ``current_theme``: um
    elemento que aparece só num dos temas desloca o ``st.tabs`` de
    Configurações e devolve a seleção para a primeira aba. Por isso o lembrete
    é emitido em toda execução e nos dois temas.
    """
    import streamlit.components.v1 as componentes

    import design.theme_selector as selector

    chamadas: list[tuple[str, object]] = []
    monkeypatch.setattr(
        componentes, "html",
        lambda corpo, **kwargs: chamadas.append((corpo, kwargs.get("height"))),
    )

    selector.lembrar_no_navegador("light")
    selector.lembrar_no_navegador("dark")
    assert len(chamadas) == 2, "o lembrete deixou de ser emitido em um dos temas"
    assert [altura for _, altura in chamadas] == [0, 0], (
        "o lembrete ocupa espaço na tela"
    )
    claro, escuro = chamadas[0][0], chamadas[1][0]
    assert "app4_tema=light" in claro and "app4_tema=dark" in escuro
    for corpo in (claro, escuro):
        # O componente roda num iframe, e o cookie do iframe não é o da
        # aplicação: sem `window.parent` o servidor nunca recebe o cookie.
        assert "window.parent.document.cookie" in corpo


def test_lembrete_no_navegador_nao_derruba_a_execucao(monkeypatch):
    """Lembrar é conveniência; falhar nisso não pode interromper o app."""
    import streamlit.components.v1 as componentes

    import design.theme_selector as selector

    def explode(*_args, **_kwargs):
        raise RuntimeError("componente indisponivel")

    monkeypatch.setattr(componentes, "html", explode)
    selector.lembrar_no_navegador("light")


def test_trocar_de_usuario_nao_volta_para_dark(monkeypatch):
    """Clicar em "Trocar de usuário" repintava a tela de escuro.

    ``encerrar_sessao`` limpa o ``session_state`` inteiro e dá ``st.rerun()``.
    A execução seguinte não tem conta, e o cookie do navegador -- que o #552
    pôs justamente para este caso -- **não alcança**: ``st.context.cookies`` é
    capturado no aperto de mão do websocket, e ``st.rerun()`` não abre conexão
    nova. Medido em 12/10/2026: três execuções depois de escrever
    ``app4_tema=light``, o servidor ainda listava só o ``_streamlit_xsrf``.

    Quem lembra dentro da sessão é ``_ULTIMO_OK`` -- e era exatamente ele que
    a limpeza levava junto.
    """
    import streamlit as st

    import core.auth as auth
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    _cookies_do_navegador(monkeypatch)
    monkeypatch.setattr(st, "rerun", lambda *a, **k: None)
    st.session_state.clear()
    st.session_state["_app4_user"] = {"id": "conta-1", "expires_at": 1 << 40}
    st.session_state[selector._ULTIMO_OK] = "light"

    auth.encerrar_sessao()

    assert "_app4_user" not in st.session_state, "a sessão não foi encerrada"
    assert selector.tema_para_pintar() == "light", (
        "a tela de entrada depois de trocar de usuário voltou para dark"
    )
    st.session_state.clear()


def test_sessao_invalidada_tambem_mantem_o_tema(monkeypatch):
    """Mesmo buraco no portão: sessão que não vale mais limpa tudo."""
    import streamlit as st

    import design.theme_selector as selector

    st.session_state.clear()
    st.session_state["_app4_user"] = {"id": "conta-1", "expires_at": 1 << 40}
    st.session_state[selector._ULTIMO_OK] = "light"
    st.session_state["rascunho_qualquer"] = 1

    selector.limpar_sessao_preservando_o_tema()

    assert st.session_state[selector._ULTIMO_OK] == "light"
    assert "_app4_user" not in st.session_state
    assert "rascunho_qualquer" not in st.session_state, (
        "a limpeza deixou de limpar o resto da sessão"
    )
    st.session_state.clear()


def test_entrar_nao_herda_o_tema_de_quem_estava_antes(monkeypatch):
    """Quem ENTRA traz a própria preferência, não a da conta anterior.

    Por isso o caminho de login continua com ``st.session_state.clear()`` puro:
    preservar o tema ali mostraria a preferência de outra pessoa se a leitura
    da conta nova falhasse.
    """
    import inspect

    import core.auth as auth

    fonte = inspect.getsource(auth._renderizar_login)
    assert "st.session_state.clear()" in fonte, (
        "o login passou a preservar o tema e pode herdar o da conta anterior"
    )


# -- nada no caminho do tema pode depender de ser administrador ---------------

def _contas():
    """A conta que administra e uma que não administra."""
    from core.config import settings

    return [settings.ADMIN_USER_ID or "conta-admin", "conta-comum-2"]


@pytest.mark.parametrize("uid", _contas())
def test_trocar_de_usuario_mantem_o_tema_em_qualquer_conta(monkeypatch, uid):
    """As correções do tema valem para quem administra e para quem não.

    O relato de 12/10/2026 foi que os defeitos de tema corrigidos na conta do
    administrador reapareciam nas outras contas. Estes três casos repetem, com
    uma conta comum, exatamente as cenas que #552 e #562 corrigiram -- e falham
    se alguma correção futura nascer presa ao ``ADMIN_USER_ID``.
    """
    import streamlit as st

    import core.auth as auth
    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    _cookies_do_navegador(monkeypatch)
    monkeypatch.setattr(st, "rerun", lambda *a, **k: None)
    st.session_state.clear()
    st.session_state["_app4_user"] = {"id": uid, "expires_at": 1 << 40}
    st.session_state[selector._ULTIMO_OK] = "light"

    auth.encerrar_sessao()

    assert selector.tema_para_pintar() == "light"
    st.session_state.clear()


@pytest.mark.parametrize("uid", _contas())
def test_leitura_que_falha_mantem_o_tema_em_qualquer_conta(monkeypatch, uid):
    """O pool ocupado por uma importação longa não distingue conta."""
    import streamlit as st

    import design.theme_selector as selector

    def fail():
        raise RuntimeError("pool ocupado pela importacao")

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "require_user", lambda: uid)
    monkeypatch.setattr(selector, "principal", lambda: {"id": uid})
    monkeypatch.setattr(selector, "load_theme", fail)
    _cookies_do_navegador(
        monkeypatch, app4_tema=f"light.{selector._etiqueta(uid)}")
    st.session_state.clear()

    assert selector.tema_para_pintar() == "light"
    st.session_state.clear()


@pytest.mark.parametrize("uid", _contas())
def test_sessao_perdida_mantem_o_tema_em_qualquer_conta(monkeypatch, uid):
    """Anexar arquivo com o websocket reconectando: sem conta na sessão."""
    import streamlit as st

    import design.theme_selector as selector

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    _cookies_do_navegador(
        monkeypatch, app4_tema=f"light.{selector._etiqueta(uid)}")
    st.session_state.clear()

    assert selector.tema_para_pintar() == "light"
    st.session_state.clear()


def test_cookie_de_uma_conta_nao_pinta_a_outra(monkeypatch):
    """Um navegador, duas contas -- e o cookie é do navegador.

    A #552 pôs o cookie no caminho para que a execução sem conta não caísse
    para dark. Num navegador usado por mais de uma conta isso virou o defeito
    oposto: a conta que entrava depois herdava o tema da anterior sempre que a
    leitura dela falhava. Mudar o tema de alguém porque OUTRA pessoa escolheu
    assim é mudança sem consentimento igual.
    """
    import streamlit as st

    import design.theme_selector as selector

    def fail():
        raise RuntimeError("pool ocupado pela importacao")

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "load_theme", fail)
    # A conta 1 acabou de pintar claro NESTE navegador.
    _cookies_do_navegador(
        monkeypatch, app4_tema=f"light.{selector._etiqueta('conta-1')}")

    def _pinta(uid: str) -> str:
        monkeypatch.setattr(selector, "require_user", lambda: uid)
        monkeypatch.setattr(selector, "principal", lambda: {"id": uid})
        st.session_state.clear()
        tema = selector.tema_para_pintar()
        st.session_state.clear()
        return tema

    # Para o dono do cookie ele continua valendo: é a memória que atravessa o
    # deploy, e tirá-la devolveria o escuro que a #552 veio corrigir.
    assert _pinta("conta-1") == "light", (
        "o cookie deixou de servir à própria conta que o escreveu"
    )
    assert _pinta("conta-2") == "dark", (
        "a conta seguinte foi pintada com o tema que outra conta escolheu "
        "neste navegador"
    )


def test_cookie_sem_etiqueta_nao_pinta_conta_nenhuma(monkeypatch):
    """O cookie que a versão anterior escrevia não dizia de quem era.

    Escrito como ``light`` e nada mais, ele era entregue a qualquer conta que
    entrasse neste navegador. É a forma do defeito relatado em 12/10/2026, e
    está escrita aqui com o vocabulário da versão que o produzia: sem conta a
    quem atribuí-lo, o cookie não pinta ninguém.
    """
    import streamlit as st

    import design.theme_selector as selector

    def fail():
        raise RuntimeError("pool ocupado pela importacao")

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "load_theme", fail)
    monkeypatch.setattr(selector, "require_user", lambda: "conta-2")
    monkeypatch.setattr(selector, "principal", lambda: {"id": "conta-2"})
    _cookies_do_navegador(monkeypatch, app4_tema="light")
    st.session_state.clear()

    assert selector.tema_para_pintar() == "dark", (
        "o cookie sem dono pintou uma conta que nunca escolheu claro"
    )
    st.session_state.clear()


def test_execucao_sem_conta_nao_apaga_a_etiqueta_do_cookie(monkeypatch):
    """A tela de entrada é a execução anterior a outra conta entrar.

    Regravar o cookie sem etiqueta ali deixaria a marca em branco, e cookie
    sem marca é aceito por qualquer conta: a proteção morreria exatamente no
    momento em que ela precisa valer.
    """
    import streamlit.components.v1 as componentes

    import design.theme_selector as selector

    corpos: list[str] = []
    monkeypatch.setattr(componentes, "html",
                        lambda corpo, **kwargs: corpos.append(corpo))
    etiqueta = selector._etiqueta("conta-1")
    _cookies_do_navegador(monkeypatch, app4_tema=f"light.{etiqueta}")

    selector.lembrar_no_navegador("light", "")

    assert f"app4_tema=light.{etiqueta}" in corpos[0]


def test_etiqueta_do_cookie_nao_devolve_a_conta():
    """O cookie ganhou uma marca, não um identificador."""
    import design.theme_selector as selector

    uid = "11111111-2222-3333-4444-555555555555"
    etiqueta = selector._etiqueta(uid)
    assert etiqueta and uid not in etiqueta
    assert etiqueta == selector._etiqueta(uid)
    assert etiqueta != selector._etiqueta(uid[:-1] + "6")
    assert selector._etiqueta("") == ""


SCRIPT_PINTA_E_ESCOLHE = '''
import time
import streamlit as st
from design.theme_selector import render_theme_selector, tema_para_pintar
from design.tema import aplicar_tema
st.session_state.setdefault('_app4_user', {'id': 'A', 'expires_at': time.time()+600})
_tema = tema_para_pintar()
st.session_state['_tema_pintado'] = _tema
aplicar_tema(_tema)
render_theme_selector()
'''


def test_leitura_boa_depois_da_pintura_repinta_a_tela(monkeypatch):
    """O seletor não pode acabar o run dizendo um tema e a tela mostrando outro.

    ``aplicar_tema`` roda no topo de ``app.py``; o seletor mora dentro da aba
    Geral, muito depois. Quando a leitura falha na pintura -- a única conexão
    do pool ocupada, o caso que ``test_falha_de_leitura_mantem_o_ultimo_tema``
    descreve -- a tela sai no tema lembrado, e a leitura do seletor, se der
    certo no mesmo run, adota a preferência de verdade e ainda limpa a marca
    de falha. O run terminava com "🌙 Dark (escuro)" escrito sobre uma página
    clara, sem aviso e sem erro: exatamente a tela relatada em 27/10/2026, com
    ``app4_theme = dark`` gravado no banco desde as 07:26 daquele dia.
    """
    import design.theme_selector as selector

    chamadas = {"n": 0}

    def primeira_leitura_do_run_falha():
        chamadas["n"] += 1
        if chamadas["n"] % 2 == 1:
            raise RuntimeError("pool sem conexao")
        return "dark"

    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "load_theme", primeira_leitura_do_run_falha)
    app = AppTest.from_string(SCRIPT_PINTA_E_ESCOLHE)
    app.session_state["_app4_theme_last_ok"] = "light"
    app.run()
    assert not app.exception
    assert app.selectbox[0].value == "dark"
    assert app.session_state["_tema_pintado"] == "dark", (
        "o seletor diz dark e a tela foi pintada de claro no mesmo run"
    )
    css = "".join(bloco.value for bloco in app.markdown)
    assert "#f5f7fb" not in css, "a camada do tema claro continuou na tela"

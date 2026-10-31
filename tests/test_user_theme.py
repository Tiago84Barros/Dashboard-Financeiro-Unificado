"""Preferência visual isolada, durável e sem sobrescrita de memória dos chats."""
import random
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


def test_gravacao_atrasada_nao_passa_por_cima_de_escolha_mais_nova(postgres, user_state):
    """A conta guarda a escolha MAIS RECENTE, não a última que conseguiu chegar.

    Uma escolha que ficou pendente num navegador (pool ocupado no clique) e só
    alcança o banco depois carrega o instante em que foi feita. Se nesse meio
    tempo a pessoa escolheu outra coisa em outro aparelho, a pendente chega
    atrasada e não pode desfazer a recente: seria o tema mudando sem ninguém
    ter pedido aquilo naquele momento.
    """
    login_as(user_state, ADMIN)
    assert preferences.load_theme_choice() is None
    assert preferences.save_theme("light", at=2_000) is True
    assert preferences.save_theme("dark", at=1_000) is False
    assert preferences.load_theme_choice() == ("light", 2_000)
    assert preferences.load_theme() == "light"
    # Repetir a mesma escolha (a pendência que finalmente chegou) é inofensivo.
    assert preferences.save_theme("light", at=2_000) is True

    # Tema gravado antes do relógio vale como o mais antigo possível.
    with postgres.begin() as conn:
        conn.execute(text(
            "UPDATE user_settings SET extra_settings = "
            "(extra_settings - 'app4_theme_at') || '{\"app4_theme\": \"dark\"}'::jsonb "
            "WHERE user_id = :uid"), {"uid": ADMIN})
    assert preferences.load_theme_choice() == ("dark", 0)
    assert preferences.save_theme("light", at=1) is True
    assert preferences.load_theme_choice() == ("light", 1)

    # Instante estranho no documento não derruba leitura nem gravação.
    with postgres.begin() as conn:
        conn.execute(text(
            "UPDATE user_settings SET extra_settings = "
            "extra_settings || '{\"app4_theme_at\": \"abc\"}'::jsonb "
            "WHERE user_id = :uid"), {"uid": ADMIN})
    assert preferences.load_theme_choice() == ("light", 0)
    assert preferences.save_theme("dark", at=5) is True
    assert preferences.load_theme_choice() == ("dark", 5)


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
    monkeypatch.setattr(selector, "load_theme_choice", lambda: stored[require_user()])
    monkeypatch.setattr(selector, "save_theme",
                        lambda theme, at=None: stored.update({require_user(): theme}))
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
    monkeypatch.setattr(selector, "load_theme_choice", lambda: "dark")
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())
    tentativas: list[str] = []

    def fail(theme, at=None):
        tentativas.append(theme)
        raise RuntimeError("sensitive details must never be rendered")

    monkeypatch.setattr(selector, "save_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    # O comando valeu: cache da sessão, seletor e tema pintado estão em light.
    conta, escolha = app.session_state["_app4_theme_preference"]
    assert (conta, escolha.tema) == ("A", "light") and escolha.instante > 0
    assert app.session_state["_app4_theme_choice"] == "light"
    # A pendência carrega o instante do CLIQUE, não o da próxima tentativa.
    assert app.session_state["_app4_theme_not_saved"] == ("light", escolha.instante)
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
    monkeypatch.setattr(selector, "load_theme_choice", lambda: "dark")
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())
    gravado: list[str] = []
    indisponivel = {"sim": True}

    def save(theme, at=None):
        if indisponivel["sim"]:
            raise RuntimeError("conexão ocupada")
        gravado.append(theme)

    monkeypatch.setattr(selector, "save_theme", save)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert app.session_state["_app4_theme_not_saved"][0] == "light"
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

    Escolha datada que só este processo conhece -- o clique cuja gravação
    falhou -- é levada à conta com o instante em que foi feita.
    """
    import design.theme_selector as selector
    gravado: list[tuple[str, object]] = []
    monkeypatch.setattr(selector, "load_theme_choice", lambda: None)
    monkeypatch.setattr(selector, "save_theme",
                        lambda theme, at=None: gravado.append((theme, at)))
    monkeypatch.setattr(selector, "_LEMBRADO", {"A": selector.Escolha("light", 123)})
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert app.selectbox[0].value == "light"
    assert app.session_state["_app4_theme_preference"] == ("A", ("light", 123))
    assert selector._LEMBRADO["A"] == ("light", 123)
    assert gravado == [("light", 123)], "a escolha que só o processo conhecia não foi à conta"
    assert "#f5f7fb" in " ".join(bloco.value for bloco in app.markdown)


def test_abrir_o_seletor_nao_grava_tema_que_ninguem_escolheu(monkeypatch):
    """O tema muda se e somente se a pessoa escolher -- e só escolha vai à conta.

    A versão anterior gravava na conta, ao abrir o seletor, a cor que estava na
    tela. Essa cor podia ser o escuro padrão de uma execução que não sabia a
    resposta, ou a memória de um cookie antigo que guardava o que foi pintado:
    era o app escolhendo pela pessoa, e a escolha inventada voltava em outro
    navegador como "preferência".

    Ela existia porque o ``selectbox`` não dispara ``on_change`` para o valor já
    marcado, e quem estava no claro não conseguia "escolher claro". A saída é o
    seletor mostrar a ESCOLHA, não a cor: sem escolha registrada ele fica vazio,
    e qualquer opção -- inclusive a que coincide com a tela -- é um comando.
    """
    import design.theme_selector as selector
    gravado: list[tuple[str, object]] = []
    monkeypatch.setattr(selector, "load_theme_choice", lambda: None)
    monkeypatch.setattr(selector, "save_theme",
                        lambda theme, at=None: gravado.append((theme, at)))
    # Memória de antes do relógio: pode ter sido pintura, não vira escolha.
    monkeypatch.setattr(selector, "_LEMBRADO", {"A": "light"})
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert gravado == [], "abrir o seletor gravou um tema que ninguém escolheu"
    assert app.selectbox[0].value is None
    # A tela continua no que estava mostrando: nada mudou de cor.
    assert "#f5f7fb" in " ".join(bloco.value for bloco in app.markdown)
    assert not app.warning
    # Escolher exatamente a cor que já está na tela registra a escolha.
    app.selectbox[0].select("light").run()
    assert not app.exception
    assert [tema for tema, _ in gravado] == ["light"]
    assert gravado[0][1] > 0
    assert app.selectbox[0].value == "light"


def test_ultimo_tema_bom_nao_atravessa_de_uma_conta_para_outra(monkeypatch):
    """O último tema bom carrega dono; sem isso ele era a memória anônima.

    Com a leitura falhando, trocar de conta no mesmo navegador pintava a conta
    nova com a preferência da anterior -- a mesma mudança de tema sem
    consentimento que a #566 fechou no cookie.
    """
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme_choice", lambda: "light")
    monkeypatch.setattr(selector, "save_theme", lambda theme, at=None: None)
    app = AppTest.from_string(SCRIPT).run()
    assert app.session_state["_app4_theme_last_ok"][:2] == ("A", "light")

    def fail():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(selector, "load_theme_choice", fail)
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


def test_falha_de_leitura_nao_impede_o_comando_e_a_conta_velha_nao_o_desfaz(monkeypatch):
    """Clicar é escolher, com ou sem banco -- e a escolha nova vence a velha.

    O seletor ficava desabilitado enquanto a leitura falhava, "para não
    sobrescrever uma preferência desconhecida". Com o instante na escolha não
    há o que proteger: o clique é, por definição, a escolha mais recente. E
    quando a leitura volta com a escolha ANTERIOR, ela não desfaz o clique --
    que é o "mudou de tema sozinho" visto de fora.
    """
    import design.theme_selector as selector

    def fail():
        raise RuntimeError("sensitive")

    gravado: list[tuple[str, object]] = []
    monkeypatch.setattr(selector, "_LEMBRADO", {})
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())
    monkeypatch.setattr(selector, "load_theme_choice", fail)
    monkeypatch.setattr(selector, "save_theme", lambda theme, at=None: (_ for _ in ()).throw(
        RuntimeError("banco fora")))
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert not app.selectbox[0].disabled
    app.selectbox[0].select("light").run()
    assert not app.exception
    assert "#f5f7fb" in " ".join(bloco.value for bloco in app.markdown)
    # O banco volta com a escolha de antes do clique: não desfaz o clique.
    monkeypatch.setattr(selector, "load_theme_choice", lambda: ("dark", 1000))
    monkeypatch.setattr(selector, "save_theme",
                        lambda theme, at=None: gravado.append((theme, at)))
    app.run()
    assert not app.exception
    assert app.selectbox[0].value == "light"
    assert "#f5f7fb" in " ".join(bloco.value for bloco in app.markdown)
    assert [tema for tema, _ in gravado] == ["light"], "a escolha pendente não foi à conta"
    assert "_app4_theme_not_saved" not in app.session_state


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
    monkeypatch.setattr(selector, "load_theme_choice", fail)
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
    monkeypatch.setattr(selector, "load_theme_choice", fail)
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    assert len(app.warning) == 1
    assert "tema" in app.warning[0].value.lower()
    monkeypatch.setattr(selector, "load_theme_choice", lambda: "light")
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

    monkeypatch.setattr(selector, "load_theme_choice", lambda: "light")
    app = AppTest.from_string(SCRIPT_SO_RESOLVE).run()
    assert app.session_state["_tema_resolvido"] == "light"
    del app.session_state["_app4_theme_preference"]

    def fail():
        raise RuntimeError("banco ocupado")

    monkeypatch.setattr(selector, "load_theme_choice", fail)
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
            escolha_pintada=lambda: ("light", 123),
            lembrar_no_navegador=lambda escolha, uid="": lembrados.append((escolha, uid)),
            render_theme_selector=lambda: None,
        ),
    )
    with pytest.raises(_PortaoInterrompeu):
        runpy.run_path("app.py", run_name="app_tema_antes_do_portao_test")
    assert temas == ["light"], (
        "o tema não foi aplicado antes do portão: a tela que o portão desenha "
        "sai com o tema base do config.toml, que é escuro"
    )
    # Vai ao navegador a ESCOLHA por trás da cor, com o instante dela -- não a
    # cor pintada, que pode ser o padrão de uma execução que não sabia.
    assert [escolha for escolha, _ in lembrados] == [("light", 123)], (
        "a escolha não foi lembrada no navegador: a execução seguinte que "
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
    monkeypatch.setattr(selector, "load_theme_choice", lambda: "light")
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
    monkeypatch.setattr(selector, "load_theme_choice", lambda: "light")
    primeira = AppTest.from_string(SCRIPT_SO_RESOLVE).run()
    assert primeira.session_state["_tema_resolvido"] == "light"

    def fail():
        raise RuntimeError("pool ocupado pela importacao")

    monkeypatch.setattr(selector, "load_theme_choice", fail)
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

    monkeypatch.setattr(selector, "load_theme_choice", fail)
    _cookies_do_navegador(
        monkeypatch, app4_tema=f"light.{selector._etiqueta('conta-1')}")
    assert selector.tema_para_pintar() == "light"


def test_lembrete_no_navegador_e_um_elemento_fixo_nos_dois_temas(monkeypatch):
    """O número de elementos acima da página não pode depender do tema.

    Mesma armadilha do ``st.warning`` que morava em ``current_theme``: um
    elemento que aparece só às vezes desloca o ``st.tabs`` de Configurações e
    devolve a seleção para a primeira aba. Por isso o lembrete é emitido em
    toda execução -- nos dois temas, e também quando não há escolha a lembrar.
    """
    import streamlit.components.v1 as componentes

    import design.theme_selector as selector

    chamadas: list[tuple[str, object]] = []
    monkeypatch.setattr(
        componentes, "html",
        lambda corpo, **kwargs: chamadas.append((corpo, kwargs.get("height"))),
    )

    etiqueta = selector._etiqueta("conta-1")
    selector.lembrar_no_navegador(selector.Escolha("light", 111), "conta-1")
    selector.lembrar_no_navegador(selector.Escolha("dark", 222), "conta-1")
    selector.lembrar_no_navegador(None, "conta-1")
    assert len(chamadas) == 3, "o lembrete deixou de ser emitido em algum caso"
    assert [altura for _, altura in chamadas] == [0, 0, 0], (
        "o lembrete ocupa espaço na tela"
    )
    claro, escuro, sem_escolha = (corpo for corpo, _ in chamadas)
    assert f"app4_tema=light.{etiqueta}.111" in claro
    assert f"app4_tema=dark.{etiqueta}.222" in escuro
    for corpo in (claro, escuro):
        # O componente roda num iframe, e o cookie do iframe não é o da
        # aplicação: sem `window.parent` o servidor nunca recebe o cookie.
        assert "window.parent.document.cookie" in corpo
    # Sem escolha não há o que lembrar: o padrão nunca vai ao cookie.
    assert "cookie" not in sem_escolha


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
    monkeypatch.setattr(selector, "load_theme_choice", fail)
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
    monkeypatch.setattr(selector, "load_theme_choice", fail)
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
    monkeypatch.setattr(selector, "load_theme_choice", fail)
    monkeypatch.setattr(selector, "require_user", lambda: "conta-2")
    monkeypatch.setattr(selector, "principal", lambda: {"id": "conta-2"})
    _cookies_do_navegador(monkeypatch, app4_tema="light")
    st.session_state.clear()

    assert selector.tema_para_pintar() == "dark", (
        "o cookie sem dono pintou uma conta que nunca escolheu claro"
    )
    st.session_state.clear()


def test_execucao_sem_conta_nao_mexe_no_cookie(monkeypatch):
    """A tela de entrada é a execução anterior a outra conta entrar.

    Ela não sabe de quem é a escolha, então não escreve nada: o cookie -- com a
    etiqueta e o instante de quem escolheu -- fica exatamente como estava.
    Regravá-lo ali apagava a marca no único momento em que ela precisa valer.
    """
    import streamlit.components.v1 as componentes

    import design.theme_selector as selector

    corpos: list[str] = []
    monkeypatch.setattr(componentes, "html",
                        lambda corpo, **kwargs: corpos.append(corpo))
    etiqueta = selector._etiqueta("conta-1")
    _cookies_do_navegador(monkeypatch, app4_tema=f"light.{etiqueta}.555")

    selector.lembrar_no_navegador(selector.Escolha("light", 555), "")

    assert len(corpos) == 1
    assert "cookie" not in corpos[0]


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
    monkeypatch.setattr(selector, "load_theme_choice", primeira_leitura_do_run_falha)
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


# -- a regra: o tema muda se e somente se a pessoa escolher ---------------------
#
# Os testes abaixo exercitam o caminho de ``app.py`` inteiro -- pintar no topo,
# levar a escolha ao cookie, desenhar o seletor -- contra uma conta falsa que
# grava como o banco (só escolha mais nova) e um navegador falso que guarda o
# cookie entre sessões e só o entrega ao servidor na sessão SEGUINTE, como o
# ``st.context.cookies`` real.

SCRIPT_APP = '''
import time
import streamlit as st
from design.theme_selector import (
    escolha_pintada, lembrar_no_navegador, render_theme_selector, tema_para_pintar,
)
from design.tema import aplicar_tema
st.session_state.setdefault('_app4_user', {'id': 'A', 'expires_at': time.time()+600})
_tema = tema_para_pintar()
st.session_state['_tema_pintado'] = _tema
aplicar_tema(_tema)
lembrar_no_navegador(escolha_pintada(), 'A')
st.write("conteudo da pagina")
render_theme_selector()
'''


class _ContaFalsa:
    """O banco do tema: lê a escolha e só grava escolha mais nova."""

    def __init__(self, valor=None):
        self.valor = valor
        self.no_ar = True
        self.gravacoes: list[tuple[str, int]] = []

    def instalar(self, monkeypatch, selector):
        monkeypatch.setattr(selector, "load_theme_choice", self.ler)
        monkeypatch.setattr(selector, "save_theme", self.gravar)
        monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())

    def ler(self):
        if not self.no_ar:
            raise RuntimeError("pool ocupado pela importacao")
        return self.valor

    def gravar(self, theme, at=None):
        if not self.no_ar:
            raise RuntimeError("pool ocupado pela importacao")
        assert at is not None, "escolha gravada sem o instante em que foi feita"
        self.gravacoes.append((theme, at))
        if self.valor is None or self.valor[1] <= at:
            self.valor = (theme, at)
            return True
        return False


class _Navegador:
    """Um navegador: guarda o cookie que o app escreve e o entrega ao abrir."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch
        self.cookie: str | None = None

    def _capturar(self):
        import streamlit.components.v1 as componentes

        def html(corpo, **_kwargs):
            achado = re.search(r"app4_tema=([^;\"]+);", corpo)
            if achado:
                self.cookie = achado.group(1)

        self._monkeypatch.setattr(componentes, "html", html)

    def abrir(self):
        """Sessão nova: o servidor recebe o cookie no aperto de mão."""
        if self.cookie:
            _cookies_do_navegador(self._monkeypatch, app4_tema=self.cookie)
        else:
            _cookies_do_navegador(self._monkeypatch)
        self._capturar()
        return AppTest.from_string(SCRIPT_APP).run()

    def rodar(self, app):
        self._capturar()
        return app.run()

    def clicar(self, app, tema):
        self._capturar()
        return app.selectbox[0].select(tema).run()


def _pintado(app) -> str:
    """O tema que a tela mostra -- pelo CSS de fato emitido, não por rótulo."""
    css = " ".join(bloco.value for bloco in app.markdown)
    claro = "#f5f7fb" in css
    assert app.session_state["_tema_pintado"] == ("light" if claro else "dark")
    return "light" if claro else "dark"


def test_escolha_que_nao_chegou_a_conta_vence_a_anterior_que_chegou(monkeypatch):
    """A cena que as correções anteriores não fecharam.

    A pessoa escolhe claro com o pool ocupado: a tela obedece, a conta não
    recebe. Vem um deploy (processo novo, sessão nova) e o banco volta,
    respondendo a escolha ANTERIOR -- escuro. O banco respondia, então o
    cookie com o claro era ignorado e a tela voltava ao escuro: de fora, o app
    trocando de tema sozinho. Agora vale a escolha mais recente, e ela é levada
    à conta com o instante em que foi feita.
    """
    import design.theme_selector as selector

    conta = _ContaFalsa(("dark", 1_000))
    conta.instalar(monkeypatch, selector)
    monkeypatch.setattr(selector, "_LEMBRADO", {})
    navegador = _Navegador(monkeypatch)
    app = navegador.abrir()
    assert _pintado(app) == "dark"

    conta.no_ar = False
    app = navegador.clicar(app, "light")
    assert not app.exception
    assert _pintado(app) == "light"
    assert conta.valor == ("dark", 1_000)

    selector._LEMBRADO.clear()
    conta.no_ar = True
    nova = navegador.abrir()
    assert not nova.exception
    assert _pintado(nova) == "light", "a escolha anterior desfez a escolha nova"
    assert nova.selectbox[0].value == "light"
    assert conta.valor[0] == "light", "a escolha não foi levada à conta"
    assert conta.valor[1] > 1_000


def test_padrao_nunca_entra_em_memoria_nenhuma(monkeypatch):
    """Sem escolha, o escuro padrão é pintado -- e não é lembrado como escolha.

    Era a raiz das trocas automáticas: o padrão de uma execução que não sabia
    a resposta ia para o cookie, para o lembrete do processo, para a memória
    da sessão e, pelo seletor, para a conta. Depois voltava de lá como se fosse
    a preferência da pessoa.
    """
    import design.theme_selector as selector

    conta = _ContaFalsa(None)
    conta.instalar(monkeypatch, selector)
    monkeypatch.setattr(selector, "_LEMBRADO", {})
    navegador = _Navegador(monkeypatch)
    for no_ar in (True, False):
        conta.no_ar = no_ar
        app = navegador.abrir()
        app = navegador.rodar(app)
        assert not app.exception
        assert _pintado(app) == "dark"
        assert app.selectbox[0].value is None
        assert "_app4_theme_last_ok" not in app.session_state
    assert selector._LEMBRADO == {}
    assert navegador.cookie is None, "o padrão foi gravado no navegador"
    assert conta.gravacoes == [], "o padrão foi gravado na conta"


def test_escolha_mais_nova_feita_em_outro_aparelho_vale_e_nao_e_desfeita(monkeypatch):
    """Dois aparelhos, uma conta: vale a escolha mais recente, de onde vier.

    E a escolha atrasada de um aparelho -- pendente desde um clique com o
    banco fora -- não desfaz a escolha mais nova que a pessoa fez no outro
    quando finalmente alcança o banco.
    """
    import design.theme_selector as selector

    conta = _ContaFalsa(None)
    conta.instalar(monkeypatch, selector)
    monkeypatch.setattr(selector, "_LEMBRADO", {})
    computador, celular = _Navegador(monkeypatch), _Navegador(monkeypatch)

    tela_pc = computador.clicar(computador.abrir(), "dark")
    assert _pintado(tela_pc) == "dark"
    # No celular, sem banco: a escolha fica pendente nesta sessão.
    conta.no_ar = False
    tela_cel = celular.clicar(celular.abrir(), "light")
    assert _pintado(tela_cel) == "light"
    # De volta ao computador, banco no ar: a pessoa escolhe claro e depois
    # escuro de novo, já DEPOIS do clique pendente do celular.
    conta.no_ar = True
    tela_pc = computador.clicar(computador.rodar(tela_pc), "light")
    tela_pc = computador.clicar(tela_pc, "dark")
    assert conta.valor[0] == "dark"
    # O celular tenta levar a pendência dele: chega atrasada e não grava.
    tela_cel = celular.rodar(tela_cel)
    assert "_app4_theme_not_saved" not in tela_cel.session_state
    assert conta.valor[0] == "dark", "a pendência atrasada desfez a escolha mais nova"
    # Sessão nova no celular, outro processo: vale a escolha mais recente.
    selector._LEMBRADO.clear()
    assert _pintado(celular.abrir()) == "dark"


@pytest.mark.parametrize("semente", range(12))
def test_simulacao_o_tema_pintado_e_sempre_a_ultima_escolha(monkeypatch, semente):
    """A regra inteira, contra uma sequência qualquer de acontecimentos.

    Cliques, execuções, sessões novas (reconexão, aba reaberta), processo novo
    (deploy) e o banco caindo e voltando, em ordem sorteada. Depois de CADA
    passo, a tela mostra a última escolha da pessoa -- ou o escuro padrão, se
    ela nunca escolheu. Nenhum acontecimento que não seja escolha muda a cor.
    """
    import design.theme_selector as selector

    sorteio = random.Random(semente)
    conta = _ContaFalsa(None)
    conta.instalar(monkeypatch, selector)
    monkeypatch.setattr(selector, "_LEMBRADO", {})
    navegador = _Navegador(monkeypatch)
    app = navegador.abrir()
    ultima: str | None = None
    acoes = ("clicar", "clicar", "rodar", "sessao_nova", "processo_novo", "banco")
    for passo in range(60):
        acao = sorteio.choice(acoes)
        if acao == "clicar":
            ultima = sorteio.choice(("dark", "light"))
            app = navegador.clicar(app, ultima)
        elif acao == "rodar":
            app = navegador.rodar(app)
        elif acao == "sessao_nova":
            app = navegador.abrir()
        elif acao == "processo_novo":
            selector._LEMBRADO.clear()
            app = navegador.abrir()
        else:
            conta.no_ar = not conta.no_ar
            app = navegador.rodar(app)
        assert not app.exception
        assert _pintado(app) == (ultima or "dark"), (
            f"semente {semente}, passo {passo} ({acao}): a tela mudou sem escolha"
        )
    # Com o banco de volta, a última escolha chega à conta na sessão seguinte.
    conta.no_ar = True
    app = navegador.abrir()
    if ultima is not None:
        assert conta.valor is not None and conta.valor[0] == ultima
    else:
        assert conta.gravacoes == []



# ── Diagnóstico que chega a quem corrige ──────────────────────────────────────
#
# Na Streamlit Cloud ninguém lê o stderr do contêiner, então `logger.exception`
# diagnostica para um leitor que não existe. Cinco correções procuraram no lugar
# errado por falta de uma linha de evidência vinda de produção. O log de lacunas
# (`app_lacunas`, migration 077) é o caminho que existe para isso.
#     memória: diagnostico-precisa-de-porta-de-entrada


@pytest.fixture
def lacunas_espiadas(monkeypatch):
    """Intercepta ``registrar_lacuna`` no módulo de onde ``_anotar`` o importa."""
    registradas: list[dict] = []

    def espiao(fonte, codigo, mensagem, **extra):
        registradas.append({"fonte": fonte, "codigo": codigo,
                            "mensagem": mensagem, **extra})

    monkeypatch.setattr("core.lacunas.registro.registrar_lacuna", espiao)
    return registradas


@pytest.fixture
def processo_sem_memoria_de_tema(monkeypatch):
    """Zera a memória de PROCESSO para este teste.

    ``_LEMBRADO`` é um dicionário de módulo: vive entre testes. Sujo de um
    instante real (``_agora_ms``), ele faz uma conta de instante fictício
    parecer sempre atrasada, e o caminho exercitado passa a ser
    ``_levar_a_conta`` em vez do clique -- o teste mediria outro ponto do
    módulo sem avisar. Pedido só por quem simula a conta com instante fixo.
    """
    monkeypatch.setattr("design.theme_selector._LEMBRADO", {})


def test_falha_de_gravacao_fica_registrada_no_log_de_lacunas(monkeypatch, lacunas_espiadas,
                                                             processo_sem_memoria_de_tema):
    """A falha que a pessoa vê como "mudou sozinho" precisa deixar rastro.

    O aviso na tela resolve para quem está olhando; não resolve para quem vai
    corrigir depois, e era exatamente essa a lacuna do diagnóstico: o defeito
    acontecia no app publicado, com uma conta que não é a minha, e eu só tinha
    o relato. A classe da exceção entra na mensagem porque é ela que separa
    disputa de conexão (``OperationalError``) de erro de schema ou permissão --
    correções opostas, sintoma idêntico na tela.
    """
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme_choice", lambda: ("dark", 1_000))
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())

    def fail(theme, at=None):
        raise TimeoutError("pool exhausted")

    monkeypatch.setattr(selector, "save_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    falhas = [r for r in lacunas_espiadas if r["codigo"] == "tema.gravacao_falhou"]
    assert len(falhas) == 1
    assert falhas[0]["fonte"] == "excecao"
    assert "TimeoutError" in falhas[0]["mensagem"]
    assert str(selector._TENTATIVAS_DE_GRAVACAO) in falhas[0]["mensagem"]
    assert falhas[0]["modulo"].endswith(":_gravar_com_insistencia")


def test_gravacao_que_so_passa_na_insistencia_nasce_como_detalhe(monkeypatch, lacunas_espiadas,
                                                                 processo_sem_memoria_de_tema):
    """O canário da disputa de conexão: insistir não pode esconder a disputa.

    A insistência absorve o defeito -- e, absorvendo, apagaria a única medida
    de que ele continua vivo. Nasce com o prefixo de DETALHE porque não há
    nada a corrigir nesta execução: a escolha chegou ao banco. Fica fora da
    fila de correção e fora da tela de quem usa, e dentro da tabela que eu leio.
    """
    import design.theme_selector as selector
    from core.lacunas.evento import e_detalhe
    monkeypatch.setattr(selector, "load_theme_choice", lambda: ("dark", 1_000))
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())
    tentativas: list[str] = []

    def falha_uma_vez(theme, at=None):
        tentativas.append(theme)
        if len(tentativas) == 1:
            raise ConnectionError("connection reset")

    monkeypatch.setattr(selector, "save_theme", falha_uma_vez)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    canarios = [r for r in lacunas_espiadas
                if r["codigo"].endswith("tema.gravacao_insistiu")]
    assert len(canarios) == 1
    assert e_detalhe(canarios[0]["codigo"])
    assert "ConnectionError" in canarios[0]["mensagem"]
    # E nenhuma lacuna de falha: o comando chegou ao banco.
    assert not [r for r in lacunas_espiadas if r["codigo"] == "tema.gravacao_falhou"]


def test_gravacao_de_primeira_nao_registra_nada(monkeypatch, lacunas_espiadas,
                                                processo_sem_memoria_de_tema):
    """O caminho normal é silencioso. Log que registra sucesso mede cliques."""
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme_choice", lambda: ("dark", 1_000))
    monkeypatch.setattr(selector, "save_theme", lambda theme, at=None: None)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    assert lacunas_espiadas == []


def test_falha_de_leitura_fica_registrada_no_log_de_lacunas(monkeypatch, lacunas_espiadas):
    """Leitura que falha pinta pela memória -- e precisa dizer que pintou."""
    import design.theme_selector as selector

    def fail():
        raise TimeoutError("read timeout")

    monkeypatch.setattr(selector, "load_theme_choice", fail)
    monkeypatch.setattr(selector, "save_theme", lambda theme, at=None: None)
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    leituras = [r for r in lacunas_espiadas if r["codigo"] == "tema.leitura_falhou"]
    assert len(leituras) >= 1
    assert "TimeoutError" in leituras[0]["mensagem"]
    assert leituras[0]["modulo"].endswith(":_escolha_em_vigor")


def test_escolha_que_nao_alcanca_a_conta_fica_registrada(monkeypatch, lacunas_espiadas):
    """A escolha presa no navegador é a cena que mais enganou o diagnóstico.

    O clique valeu na tela, o banco não recebeu, e na sessão seguinte a conta
    respondia a escolha ANTERIOR: para quem usa, "mudou sozinho". Agora o
    modelo leva a escolha nova à conta assim que der -- e quando essa viagem
    falha outra vez, é esta linha que diz por quê, nomeando o tema que ficou
    só no navegador.
    """
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme_choice", lambda: ("dark", 1_000))
    monkeypatch.setattr(selector, "_LEMBRADO",
                        {"A": selector.Escolha("light", 2_000)})

    def fail(theme, at=None):
        raise TimeoutError("pool exhausted")

    monkeypatch.setattr(selector, "save_theme", fail)
    app = AppTest.from_string(SCRIPT).run()
    assert not app.exception
    presas = [r for r in lacunas_espiadas
              if r["codigo"] == "tema.escolha_local_nao_chegou_a_conta"]
    assert len(presas) == 1
    assert "light" in presas[0]["mensagem"]
    assert "TimeoutError" in presas[0]["mensagem"]
    assert presas[0]["modulo"].endswith(":_levar_a_conta")


def test_falha_ao_anotar_nao_derruba_a_tela(monkeypatch,
                                           processo_sem_memoria_de_tema):
    """Anotar é acessório. Um problema no log de lacunas não pode virar o erro.

    Garantia que o próprio log de lacunas já dá na porta pública; repetida aqui
    porque o ponto de chamada é o topo do ``app.py``, onde uma exceção não
    deixaria sobrar tela nenhuma.
    """
    import design.theme_selector as selector
    monkeypatch.setattr(selector, "load_theme_choice", lambda: ("dark", 1_000))
    monkeypatch.setattr(selector, "_ESPERA_ENTRE_TENTATIVAS", ())
    monkeypatch.setattr(selector, "save_theme",
                        lambda theme, at=None: (_ for _ in ()).throw(TimeoutError("x")))

    def explode(*args, **kwargs):
        raise RuntimeError("log de lacunas fora do ar")

    monkeypatch.setattr("core.lacunas.registro.registrar_lacuna", explode)
    app = AppTest.from_string(SCRIPT).run()
    app.selectbox[0].select("light").run()
    assert not app.exception
    # O comando continua valendo, que é o que esta correção garantiu.
    assert app.session_state["_app4_theme_choice"] == "light"
    assert app.session_state["_app4_theme_not_saved"][0] == "light"


def test_anotar_nao_arrasta_o_pacote_de_lacunas_para_o_import_do_seletor():
    """O seletor de tema não paga o import do log de lacunas para carregar.

    ``core.lacunas.evento`` custa 87 µs sozinho, mas alcançá-lo pelo
    ``__init__`` do pacote importa ``captura`` -> ``registro`` -> ``destino``
    -> sqlalchemy: ~1,6 s a frio. Um ``from core.lacunas.evento import
    PREFIXO_DETALHE`` no topo de ``design/theme_selector.py`` -- que foi o que
    eu escrevi primeiro -- arrastava essa pilha inteira para dentro do import
    do seletor e somava ~2 s ao primeiro run de script, estourando o orçamento
    de 3 s do AppTest.  O conserto é o acesso tardio, prefixo incluído.

    O teste roda em subprocesso porque ``sys.modules`` da suíte já tem tudo
    importado por outros testes: aqui a pergunta é o que o import DESTE módulo
    exige, e ela só se responde num processo limpo.
    """
    import subprocess
    import sys
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    codigo = (
        "import sys; import design.theme_selector; "
        "print('sim' if 'core.lacunas' in sys.modules else 'nao')"
    )
    saida = subprocess.run([sys.executable, "-c", codigo], capture_output=True,
                           text=True, cwd=str(raiz), timeout=120)
    assert saida.returncode == 0, saida.stderr
    assert saida.stdout.strip() == "nao", (
        "design/theme_selector.py voltou a importar core.lacunas no topo; "
        "o acesso tem de ser tardio, dentro de _anotar")

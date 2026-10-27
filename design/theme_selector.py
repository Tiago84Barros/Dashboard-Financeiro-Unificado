"""Preferência visual da sessão, sempre vinculada ao usuário autenticado."""
import hashlib
import logging

import streamlit as st

from core.user_context import principal, require_user
from core.user_preferences import load_theme, save_theme

logger = logging.getLogger(__name__)

_CHOICE = "_app4_theme_choice"
_CACHE = "_app4_theme_preference"
_ERROR = "_app4_theme_error"
_LOAD_ERROR = "_app4_theme_load_error"
_ULTIMO_OK = "_app4_theme_last_ok"
_PINTADO = "_app4_theme_painted"
_COOKIE = "app4_tema"

# Último tema lido com sucesso de cada conta, no processo -- e não na
# sessão. Uma sessão nova (reconexão do navegador, aba reaberta, app que
# voltou a aceitar conexão) começa sem `st.session_state`: a primeira
# leitura é obrigatória, e quando ela falha não há último tema de sessão
# para segurar a preferência -- o app repintava de escuro quem escolheu
# claro. A falha é mais provável justamente no meio de uma importação
# longa em Configurações, que ocupa a única conexão do pool.
_LEMBRADO: dict[str, str] = {}


def _lembrar(uid: str, theme: str) -> None:
    if theme in ("dark", "light"):
        _LEMBRADO[uid] = theme


def _etiqueta(uid: str) -> str:
    """Marca opaca da conta, para o cookie saber de quem é o tema que guarda.

    O cookie é do NAVEGADOR e o tema é preferência de CONTA. Num navegador
    compartilhado -- que é como as contas deste app são testadas -- o cookie
    que o administrador acabou de escrever era entregue à conta seguinte como
    se fosse dela: a conta comum, com a leitura falhando, aparecia pintada com
    o tema de outra pessoa. Isso é mudança de tema sem consentimento, e nasceu
    justamente da correção #552, que pôs o cookie no caminho.

    ``sha256`` truncado do identificador, nunca o identificador. Não dá para
    voltar dele à conta, e o cookie continua sem nome, e-mail ou id -- o que
    ele ganha é só a capacidade de dizer "este tema não é seu".
    """
    return hashlib.sha256(str(uid).encode()).hexdigest()[:12] if uid else ""


def _cookie_cru() -> str:
    try:
        valor = st.context.cookies.get(_COOKIE)
    except Exception:
        return ""
    return valor if isinstance(valor, str) else ""


def tema_do_navegador(uid: str = "") -> str | None:
    """Último tema que ESTE navegador pintou, lido do cookie da sessão HTTP.

    ``_LEMBRADO`` é do processo e ``st.session_state`` é da sessão -- e os dois
    só respondem quando há conta identificada. O cookie é do navegador e
    sobrevive ao que derruba a sessão (expiração das 12 h, reconexão do
    websocket, aba reaberta): é o único lugar de onde uma execução sem conta
    consegue saber que a pessoa estava no claro.

    Com ``uid``, o cookie só responde se a etiqueta for da mesma conta. Sem
    ``uid`` -- tela de entrada, sessão perdida -- responde sempre: ali não há
    conta a quem atribuir preferência, e o que importa é não piscar escuro no
    meio do que o navegador já estava pintando.

    Cookie sem etiqueta -- anterior a esta correção -- é recusado quando há
    conta: não dá para saber de quem ele é, e pintar alguém com a preferência
    possivelmente de outra pessoa é o defeito que esta função existe para
    fechar. Custa uma execução escura, só para quem tem o cookie antigo E a
    leitura falhando na primeira execução depois do deploy; a execução seguinte
    já reescreve o cookie com etiqueta.
    """
    tema, _, etiqueta = _cookie_cru().partition(".")
    if tema not in ("dark", "light"):
        return None
    if uid and etiqueta != _etiqueta(uid):
        return None
    return tema


def lembrar_no_navegador(theme: str, uid: str = "") -> None:
    """Grava o tema no cookie do navegador, SEMPRE e na mesma posição.

    Emitido em toda execução e nos dois temas, de propósito. Um elemento que
    só aparecesse num deles mudaria a contagem de elementos acima da página
    conforme o tema -- e é exatamente isso que devolve o ``st.tabs`` de
    Configurações para a primeira aba (ver :func:`current_theme`).

    O cookie é escrito no documento PAI: ``components.html`` roda num iframe,
    e o cookie do iframe não é o da aplicação. Guarda ``dark``/``light`` e a
    etiqueta opaca de :func:`_etiqueta`, nada que identifique a conta.

    Sem ``uid`` a etiqueta que já está no cookie é MANTIDA. Regravar sem ela
    apagaria a marca justamente nas execuções sem conta -- a tela de entrada,
    que é a execução imediatamente anterior a outra conta entrar -- e o cookie
    sem marca é aceito por qualquer uma: a proteção morreria no único momento
    em que ela precisa valer.
    """
    marcado = theme if theme in ("dark", "light") else "dark"
    etiqueta = _etiqueta(uid) or _cookie_cru().partition(".")[2]
    if etiqueta:
        marcado = f"{marcado}.{etiqueta}"
    try:
        from streamlit.components.v1 import html as _html
    except Exception:
        return
    try:
        _html(
            "<script>try{window.parent.document.cookie="
            f'"{_COOKIE}={marcado}; path=/; max-age=31536000; samesite=lax";'
            "}catch(e){}</script>",
            height=0,
        )
    except Exception:
        logger.debug("não foi possível lembrar o tema no navegador", exc_info=True)


def _repintar_se_divergiu(theme: str) -> None:
    """Refaz a execução quando a tela já saiu pintada com o OUTRO tema.

    O tema é resolvido DUAS vezes na mesma execução, por caminhos
    independentes: ``tema_para_pintar()`` no topo de ``app.py``, que pinta, e
    ``current_theme()`` de novo lá embaixo, dentro da aba Geral, quando o
    seletor é desenhado. Quando a leitura falha na primeira (a única conexão
    do pool ocupada -- o caso que :func:`current_theme` descreve) e dá certo na
    segunda, a segunda adota a preferência de verdade, preenche o cache e ainda
    limpa a marca de falha: a execução terminava com "🌙 Dark (escuro)" escrito
    sobre uma página clara, sem aviso e sem erro. Foi a tela relatada em
    27/10/2026, com ``app4_theme = dark`` gravado no banco desde as 07:26.

    Repintar não é possível -- o CSS já foi emitido acima de tudo. O que
    resolve é rodar de novo: o cache da sessão já está preenchido com o tema
    certo, então a execução seguinte pinta certo e não volta aqui. Sem laço.
    """
    pintado = st.session_state.get(_PINTADO)
    if pintado in ("dark", "light") and pintado != theme:
        st.session_state.pop(_PINTADO, None)
        st.rerun()


def current_theme() -> str:
    """Resolve o tema da conta **sem desenhar nada na tela**.

    Esta função roda no topo de ``app.py``, acima de todo o conteúdo da
    página. Desenhar aqui custava caro de um jeito nada óbvio: um ``st.warning``
    que aparece só nas execuções em que a leitura falha insere um elemento
    ACIMA do ``st.tabs`` de Configurações, e o Streamlit devolve a seleção para
    a primeira aba quando o grupo de abas muda de posição.

    Medido em 22/09/2026 num app isolado, com as abas de Configurações e o
    mesmo ``file_uploader``: rerun de botão preserva a aba, adicionar arquivo
    ao uploader preserva a aba, elementos novos DENTRO da aba preservam a aba.
    Um elemento a mais acima das abas devolve para a primeira, sempre.

    Era um defeito só, com dois sintomas que pareciam dois: subir um arquivo em
    "Atualização de dados" jogava a pessoa de volta em "Geral" **e** repintava o
    app de escuro -- as duas coisas na execução em que esta leitura falhou.

    O aviso passou a morar em :func:`render_theme_selector`, que já está dentro
    da aba Geral: lá ele é conteúdo da aba, não deslocamento da página.
    """
    uid = require_user()
    cached = st.session_state.get(_CACHE)
    if not cached or cached[0] != uid:
        try:
            theme = load_theme()
        except Exception:
            # O log é o que faltava para diagnosticar: até aqui a exceção era
            # engolida inteira e a única pista que sobrava era o app escuro.
            logger.exception("falha ao ler o tema da conta")
            st.session_state[_LOAD_ERROR] = True
            # Falha de leitura não é troca de preferência. Enquanto o cache da
            # sessão não existe (a primeira leitura falhou), toda execução
            # relê, e cair para "dark" em cada falha repintava o app inteiro no
            # meio do trabalho -- foi o que acontecia ao clicar num botão de
            # atualização em Configurações, que ocupa a única conexão do pool.
            ultimo = (
                st.session_state.get(_ULTIMO_OK)
                or _LEMBRADO.get(uid)
                or tema_do_navegador(uid)
            )
            return ultimo if ultimo in ("dark", "light") else "dark"
        st.session_state.pop(_LOAD_ERROR, None)
        st.session_state[_CACHE] = (uid, theme)
        st.session_state[_CHOICE] = theme
        st.session_state[_ULTIMO_OK] = theme
        _lembrar(uid, theme)
        _repintar_se_divergiu(theme)
    return st.session_state[_CACHE][1]


def limpar_sessao_preservando_o_tema() -> None:
    """``st.session_state.clear()`` sem levar junto o tema que está na tela.

    Trocar de usuário e invalidar a sessão limpam o estado inteiro, e levavam
    junto a única memória de tema que uma execução sem conta alcança DENTRO da
    sessão -- a tela de entrada aparecia escura para quem estava no claro.

    O cookie do navegador não cobre este caso. ``st.context.cookies`` é
    capturado no aperto de mão do websocket, e nem ``st.rerun()`` nem a limpeza
    abrem conexão nova: o cookie escrito nesta sessão só chega ao servidor na
    PRÓXIMA. Medido em 12/10/2026 -- três execuções depois de escrever
    ``app4_tema=light``, o servidor ainda listava só o ``_streamlit_xsrf``.

    Não é usada quando alguém ENTRA: aí a conta muda, e herdar o tema de quem
    estava antes seria mostrar a preferência de outra pessoa.
    """
    ultimo = st.session_state.get(_ULTIMO_OK)
    st.session_state.clear()
    if ultimo in ("dark", "light"):
        st.session_state[_ULTIMO_OK] = ultimo


def tema_para_pintar() -> str:
    """Tema a aplicar ANTES do portão de autenticação, e que nunca levanta.

    ``aplicar_tema`` morava depois de ``verificar_autenticacao()``, e toda
    execução interrompida pelo portão desenhava sem CSS nenhum -- com o tema
    base do ``config.toml``, que é escuro. Era o que acontecia ao importar os
    Dados Históricos B3: a importação longa ocupa a única conexão do pool, a
    validação da sessão na execução seguinte não consegue conexão e o app
    aparecia escuro, sem autorização de ninguém.

    Sem conta na sessão sobrou o caminho que escurecia a tela ao anexar o PDF
    da Nomad: ``principal()`` devolve ``{}`` assim que a sessão expira (12 h,
    nunca renovadas) e numa sessão nova do Streamlit -- reconexão do websocket
    durante uma execução longa, por exemplo -- o ``session_state`` nasce vazio.
    Devolver ``dark`` nesse ponto repintava de escuro quem tinha escolhido
    claro. O cookie do navegador é o único lembrete que atravessa a perda da
    sessão, e ele guarda só ``dark``/``light``.
    """
    # A marca da execução anterior sai ANTES de resolver: o ``current_theme()``
    # chamado aqui dentro não pode se comparar com o que foi pintado no run
    # passado -- quem compara é o seletor, depois que esta função registrar o
    # tema que ESTA execução pintou.
    st.session_state.pop(_PINTADO, None)
    tema = _resolver_o_tema_para_pintar()
    st.session_state[_PINTADO] = tema
    return tema


def _resolver_o_tema_para_pintar() -> str:
    uid = str(principal().get("id", ""))
    if not uid:
        # O último tema desta sessão vem ANTES do cookie: o cookie é a memória
        # entre sessões, e dentro da sessão ele chega atrasado uma conexão.
        ultimo = st.session_state.get(_ULTIMO_OK)
        if ultimo in ("dark", "light"):
            return ultimo
        return tema_do_navegador() or "dark"
    try:
        return current_theme()
    except Exception:
        logger.exception("falha ao resolver o tema antes do portão")
        return _LEMBRADO.get(uid) or tema_do_navegador(uid) or "dark"


def _persist_choice() -> None:
    """Grava a escolha no callback do widget, e não depois de lê-lo.

    Comparar o retorno do ``selectbox`` com a preferência em cache e gravar na
    diferença parecia equivalente, mas ``st.rerun()`` reexecuta o script com o
    valor anterior ainda no widget: a segunda troca de tema da sessão era
    revertida e gravada de volta, deixando a pessoa presa no tema escolhido da
    primeira vez. No callback, o Streamlit já entregou o valor novo e o rerun
    seguinte lê o tema correto -- sem ``st.rerun()`` explícito.
    """
    uid = require_user()
    choice = st.session_state.get(_CHOICE)
    cached = st.session_state.get(_CACHE)
    anterior = cached[1] if cached and cached[0] == uid else None
    if choice not in ("dark", "light") or choice == anterior:
        return
    try:
        save_theme(choice)
    except Exception:
        # A preferência anterior é a verdade até o banco confirmar a nova.
        st.session_state[_CHOICE] = anterior or "dark"
        st.session_state[_ERROR] = True
    else:
        st.session_state[_CACHE] = (uid, choice)
        st.session_state[_ULTIMO_OK] = choice
        _lembrar(uid, choice)


def render_theme_selector() -> None:
    theme = current_theme()
    # Sem leitura válida, não sobrescreve uma preferência ainda desconhecida.
    cached = st.session_state.get(_CACHE)
    ready = bool(cached and cached[0] == require_user())
    st.session_state.setdefault(_CHOICE, theme)
    st.selectbox(
        "Tema da minha conta", ("dark", "light"),
        format_func=lambda value: "🌙 Dark (escuro)" if value == "dark" else "☀️ Light (claro)",
        key=_CHOICE, disabled=not ready, on_change=_persist_choice,
        help="Preferência salva apenas para sua conta, e aplicada na hora.",
    )
    if st.session_state.get(_LOAD_ERROR):
        # Não é `pop`: enquanto a leitura falhar, o seletor fica desabilitado e
        # a pessoa precisa continuar vendo por quê. `current_theme` limpa a
        # marca na primeira leitura que der certo.
        atual = st.session_state.get(_ULTIMO_OK)
        mantido = (
            "o app manteve o último tema que conseguiu ler"
            if atual in ("dark", "light")
            else "o app está usando dark temporariamente"
        )
        st.warning(
            "Não foi possível carregar o tema da sua conta. "
            f"{mantido[0].upper()}{mantido[1:]} e sua preferência não foi "
            "alterada — tente novamente em instantes."
        )
    if st.session_state.pop(_ERROR, False):
        st.error("Não foi possível salvar o tema. Sua preferência anterior foi mantida; tente novamente.")

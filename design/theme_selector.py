"""Tema da conta: muda se e somente se a pessoa escolher.

Regra do dono do app, 31/10/2026: o tema claro/escuro muda **se e somente se**
quem usa escolher explicitamente -- nunca de forma automática pelo app.

As correções anteriores (#552, #562, #566, #585, #586) fecharam caminhos um a
um e o tema continuou mudando sozinho, porque o defeito estava na raiz do
modelo: as memórias do tema -- sessão, processo, cookie do navegador e, pelo
seletor, a própria conta -- guardavam o tema que a tela PINTOU. E a tela pinta
o escuro padrão sempre que não sabe a resposta (leitura que falhou, conta sem
preferência, sessão nova). O padrão entrava nas memórias como se fosse escolha
e voltava delas como "preferência". Duas cenas concretas:

- a escolha que não chegou ao banco (pool ocupado no clique) perdia, na sessão
  seguinte, para a escolha anterior que tinha chegado -- o banco respondia e o
  cookie com a escolha nova era ignorado;
- abrir Configurações → Geral numa conta sem tema gravado gravava na conta a
  cor que estava na tela, e essa cor podia ser o padrão.

O modelo agora:

- Uma **escolha** é ``(tema, instante)`` e só nasce num lugar: o clique no
  seletor (:func:`_persist_choice`). O instante é em milissegundos.
- Toda memória guarda **só escolhas**, com o instante em que foram feitas.
  Nenhuma guarda o que foi pintado; nenhuma guarda o padrão.
- O tema pintado é a escolha **mais recente** que alguma memória conhece; no
  empate vale a da conta. A escolha que ficou só no navegador é levada à conta
  assim que der -- e a gravação é condicional, não passa por cima de escolha
  mais nova feita em outro aparelho (:func:`core.user_preferences.save_theme`).
- Sem escolha conhecida, pinta o padrão (:data:`PADRAO`, o mesmo ``base`` do
  ``config.toml``) e não grava isso em lugar nenhum.

Memória de antes desta versão (sem instante) continua aceita com instante zero:
perde para qualquer escolha datada e nunca é levada à conta, porque não dá para
saber se foi escolha ou pintura.
"""
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import NamedTuple

import streamlit as st

from core.user_context import principal, require_user
from core.user_preferences import load_theme_choice, save_theme

logger = logging.getLogger(__name__)

TEMAS = ("dark", "light")
#: Tema da tela quando ninguém escolheu nada. É o ``base`` do ``config.toml``:
#: é o que o Streamlit desenha antes de qualquer CSS do app chegar.
PADRAO = "dark"

_CHOICE = "_app4_theme_choice"
_CACHE = "_app4_theme_preference"
_LOAD_ERROR = "_app4_theme_load_error"
_ULTIMO_OK = "_app4_theme_last_ok"
_PINTADO = "_app4_theme_painted"
_PINTADO_ESCOLHA = "_app4_theme_painted_choice"
_NAO_GUARDADO = "_app4_theme_not_saved"
_SEM_PREFERENCIA = "_app4_theme_unset"
_COOKIE = "app4_tema"

#: Tentativas de gravação de uma escolha de tema, e a espera entre elas.
#: Uma tentativa só fazia o comando da pessoa depender de conseguir a única
#: conexão do pool do Cloud no instante exato do clique -- disputada, na mesma
#: execução, pela revalidação da sessão e por tudo que Configurações faz.
_TENTATIVAS_DE_GRAVACAO = 3
_ESPERA_ENTRE_TENTATIVAS = (0.25, 0.75)


def _anotar(codigo: str, mensagem: str, funcao: str, *,
            detalhe: bool = False) -> None:
    """Grava no log de lacunas o que só o servidor via.

    ``logger.exception`` resolve o diagnóstico aqui na máquina e não resolve
    nada na Streamlit Cloud, onde ninguém lê o stderr do contêiner. Então toda
    falha de tema que acontecia com a pessoa usando o app publicado terminava
    em "mudou sozinho" sem uma única linha de evidência -- e foram cinco
    correções procurando no lugar errado por falta dessa linha.
        memória: diagnostico-precisa-de-porta-de-entrada

    ``registrar_lacuna`` é a porta certa: nunca levanta, grava uma vez por
    impressão digital por sessão (e não por clique, então a contagem mede
    fatos e não cliques), e na nuvem o UPSERT em ``app_lacunas`` sai numa
    thread, sem somar latência ao render.

    O acesso a ``core.lacunas`` é tardio, inclusive para a constante do
    prefixo: ``core.lacunas.evento`` custa 87 µs sozinho, mas alcançá-lo pelo
    ``__init__`` do pacote importa ``captura`` -> ``registro`` -> ``destino``
    -> **sqlalchemy**, ~1,6 s a frio. Um ``from core.lacunas.evento import …``
    no topo deste módulo arrastaria a pilha de banco inteira para dentro do
    import do seletor de tema -- que roda em TODA execução do script, porque
    ``app.py`` pinta o tema no topo. Anotar é acessório: um problema no log de
    lacunas não pode impedir o seletor de carregar, nem pesar no primeiro
    render (foi o que estourou o orçamento de 3 s do AppTest).

    ``detalhe`` marca o código com o prefixo que faz a lacuna nascer
    *legítima*: registro de algo que já se resolveu, fora da fila de correção
    e fora da tela de quem usa.
    """
    try:
        from core.lacunas.evento import PREFIXO_DETALHE
        from core.lacunas.registro import registrar_lacuna

        prefixo = PREFIXO_DETALHE if detalhe else ""
        registrar_lacuna("excecao", f"{prefixo}{codigo}", mensagem,
                         modulo=f"design/theme_selector.py:{funcao}")
    except Exception:  # noqa: BLE001 - anotar a falha não pode virar falha
        logger.exception("falha ao anotar o diagnóstico do tema")


def _motivo(erro: BaseException | None) -> str:
    """Classe e texto da exceção, para a mensagem da lacuna.

    A classe é o que distingue timeout de conexão (``OperationalError``) de
    erro de schema ou de permissão, que pedem correções opostas. A mensagem
    sai sanitizada pelo log de lacunas -- URL, e por dentro dela a string de
    conexão, nunca chegam à tabela.
    """
    if erro is None:
        return "sem exceção registrada"
    return f"{type(erro).__name__}: {erro}"


class Escolha(NamedTuple):
    """Um comando de tema: qual tema, e quando a pessoa o deu."""

    tema: str
    #: Milissegundos desde a época. Zero = memória de antes do relógio, que
    #: perde para qualquer escolha datada.
    instante: int


# Última escolha conhecida de cada conta, no processo -- e não na sessão. Uma
# sessão nova (reconexão do navegador, aba reaberta) começa sem
# ``st.session_state``, e quando a primeira leitura falha é daqui que vem a
# escolha. Só guarda escolha: nunca o padrão, nunca o que foi pintado.
_LEMBRADO: dict[str, Escolha] = {}

_SEM_CACHE = object()


def _agora_ms() -> int:
    return int(time.time() * 1000)


def _instante_do_texto(texto: str) -> int:
    return int(texto) if texto.isdigit() and len(texto) <= 18 else 0


def _escolha(valor) -> "Escolha | None":
    """Qualquer formato guardado -- atual ou de versão anterior -- como escolha.

    Só o tema (formato antigo) vira escolha de instante zero; o que não for
    tema conhecido não vira nada.
    """
    if isinstance(valor, str):
        return Escolha(valor, 0) if valor in TEMAS else None
    if isinstance(valor, (tuple, list)) and len(valor) == 2 and valor[0] in TEMAS:
        try:
            instante = int(valor[1])
        except (TypeError, ValueError):
            return None
        return Escolha(valor[0], max(instante, 0))
    return None


def _mais_recente(*candidatas: "Escolha | None") -> "Escolha | None":
    """A escolha de instante maior; no empate fica a que veio primeiro."""
    vencedora = None
    for candidata in candidatas:
        if candidata is not None and (
                vencedora is None or candidata.instante > vencedora.instante):
            vencedora = candidata
    return vencedora


# -- memória da sessão ----------------------------------------------------------

def _guardar_ultimo_ok(uid: str, escolha: Escolha) -> None:
    """Guarda a última escolha conhecida COM a conta a que ela pertence.

    Sem a conta junto, este era o lugar por onde o tema de uma pessoa chegava a
    outra: trocar de conta no mesmo navegador com a leitura falhando pintava a
    conta nova com a escolha da anterior (#566).
    """
    st.session_state[_ULTIMO_OK] = (uid, escolha.tema, escolha.instante)


def _ultimo_ok_escolha(uid: str = "") -> "Escolha | None":
    """Última escolha conhecida nesta sessão, recusada quando é de outra conta.

    Sem ``uid`` responde sempre: a tela de entrada e a sessão perdida não têm
    conta a quem atribuir a escolha, e ali o que importa é não trocar o tema
    que o navegador já estava mostrando.

    Os formatos antigos -- ``(conta, tema)`` e só o tema -- continuam aceitos,
    com instante zero: uma sessão aberta antes do deploy tem a chave assim.
    """
    valor = st.session_state.get(_ULTIMO_OK)
    if isinstance(valor, tuple) and len(valor) == 3:
        dono, tema, instante = valor
    elif isinstance(valor, tuple) and len(valor) == 2:
        (dono, tema), instante = valor, 0
    else:
        dono, tema, instante = "", valor, 0
    if uid and dono and dono != uid:
        return None
    return _escolha((tema, instante)) if tema in TEMAS else None


def _ultimo_ok(uid: str = "") -> str | None:
    escolha = _ultimo_ok_escolha(uid)
    return escolha.tema if escolha else None


def _valido_como_ultimo_ok(valor) -> bool:
    if isinstance(valor, tuple) and len(valor) in (2, 3):
        return valor[1] in TEMAS
    return valor in TEMAS


# -- memória do processo --------------------------------------------------------

def _lembrar(uid: str, escolha: Escolha) -> None:
    atual = _lembrado(uid)
    if uid and (atual is None or escolha.instante >= atual.instante):
        _LEMBRADO[uid] = escolha


def _lembrado(uid: str) -> "Escolha | None":
    return _escolha(_LEMBRADO.get(uid)) if uid else None


# -- memória do navegador -------------------------------------------------------

def _etiqueta(uid: str) -> str:
    """Marca opaca da conta, para o cookie saber de quem é a escolha que guarda.

    O cookie é do NAVEGADOR e o tema é preferência de CONTA. Num navegador
    compartilhado, o cookie que uma conta escreveu era entregue à seguinte como
    se fosse dela (#566). ``sha256`` truncado do identificador, nunca o
    identificador: não dá para voltar dele à conta, e o cookie continua sem
    nome, e-mail ou id.
    """
    return hashlib.sha256(str(uid).encode()).hexdigest()[:12] if uid else ""


def _cookie_cru() -> str:
    try:
        valor = st.context.cookies.get(_COOKIE)
    except Exception:
        return ""
    return valor if isinstance(valor, str) else ""


def escolha_do_navegador(uid: str = "") -> "Escolha | None":
    """Última escolha que ESTE navegador conhece, lida do cookie.

    Formato ``tema.etiqueta.instante``. ``_LEMBRADO`` é do processo e
    ``st.session_state`` é da sessão, e os dois só respondem quando há conta
    identificada; o cookie sobrevive ao que derruba a sessão (expiração das
    12 h, reconexão do websocket, aba reaberta, deploy).

    Com ``uid``, só responde se a etiqueta for da mesma conta -- cookie sem
    etiqueta é recusado, porque não dá para saber de quem é. Sem ``uid`` (tela
    de entrada, sessão perdida) responde sempre. Cookie sem instante (versão
    anterior) vale como escolha de instante zero.
    """
    tema, _, resto = _cookie_cru().partition(".")
    etiqueta, _, instante = resto.partition(".")
    if tema not in TEMAS:
        return None
    if uid and etiqueta != _etiqueta(uid):
        return None
    return Escolha(tema, _instante_do_texto(instante))


def tema_do_navegador(uid: str = "") -> str | None:
    escolha = escolha_do_navegador(uid)
    return escolha.tema if escolha else None


def lembrar_no_navegador(escolha: "Escolha | None", uid: str = "") -> None:
    """Leva ao cookie a escolha em vigor -- e NADA quando não há escolha.

    Antes gravava o tema PINTADO, e o padrão escuro de uma execução que não
    sabia a resposta entrava no cookie como se fosse a escolha da pessoa,
    voltando dele na sessão seguinte. Agora só escolha vai ao cookie, com o
    instante dela; sem escolha (conta que nunca escolheu) ou sem conta (tela de
    entrada, que não sabe de quem é) o cookie fica como está.

    Emitido em toda execução, como UM elemento, nos dois casos: um elemento que
    só aparecesse às vezes mudaria a contagem de elementos acima da página, e é
    isso que devolve o ``st.tabs`` de Configurações para a primeira aba (ver
    :func:`current_theme`).

    O cookie é escrito no documento PAI: ``components.html`` roda num iframe, e
    o cookie do iframe não é o da aplicação.
    """
    escolha = _escolha(escolha)
    script = ""
    if escolha is not None and uid:
        valor = f"{escolha.tema}.{_etiqueta(uid)}.{escolha.instante}"
        script = (
            "try{window.parent.document.cookie="
            f'"{_COOKIE}={valor}; path=/; max-age=31536000; samesite=lax";'
            "}catch(e){}"
        )
    try:
        from streamlit.components.v1 import html as _html
    except Exception:
        return
    try:
        _html(f"<script>{script}</script>", height=0)
    except Exception:
        logger.debug("não foi possível lembrar o tema no navegador", exc_info=True)


# -- resolução ------------------------------------------------------------------

def _repintar_se_divergiu(theme: str) -> None:
    """Refaz a execução quando a tela já saiu pintada com o OUTRO tema.

    O tema é resolvido no topo de ``app.py`` (que pinta) e de novo dentro da
    aba Geral, quando o seletor é desenhado. Quando a leitura falha na primeira
    e dá certo na segunda, a segunda pode descobrir uma escolha mais recente:
    a execução terminava com o seletor dizendo um tema sobre uma página do
    outro (relato de 27/10/2026). O CSS já foi emitido; o que resolve é rodar
    de novo com o cache já preenchido -- a execução seguinte pinta certo e não
    volta aqui. Sem laço. Não é troca automática: é a tela alcançando a
    escolha que a pessoa fez.
    """
    pintado = st.session_state.get(_PINTADO)
    if pintado in TEMAS and pintado != theme:
        st.session_state.pop(_PINTADO, None)
        st.rerun()


def _em_cache(uid: str):
    """Escolha já resolvida nesta sessão para ``uid``.

    ``Escolha``; ``None`` quando a conta respondeu e ninguém escolheu nada; ou
    ``_SEM_CACHE`` quando a conta ainda não respondeu nesta sessão.
    """
    valor = st.session_state.get(_CACHE)
    if not (isinstance(valor, tuple) and len(valor) == 2 and valor[0] == uid):
        return _SEM_CACHE
    return _escolha(valor[1])


def _escolha_local(uid: str) -> "Escolha | None":
    """A escolha mais recente que sessão, processo e navegador conhecem."""
    return _mais_recente(
        _ultimo_ok_escolha(uid), _lembrado(uid), escolha_do_navegador(uid))


def _escolha_em_vigor(uid: str) -> "Escolha | None":
    """A escolha mais recente conhecida para ``uid``, ou ``None``.

    Lê a conta uma vez por sessão. Falha de leitura não é comando: devolve a
    escolha que as memórias locais conhecem, sem preencher o cache -- a
    execução seguinte relê. Leitura boa compara a escolha da conta com a das
    memórias locais pelo instante, e a mais nova vale; se a mais nova ficou só
    neste navegador (gravação que falhou no clique), ela é levada à conta.
    """
    em_cache = _em_cache(uid)
    if em_cache is not _SEM_CACHE:
        return em_cache
    try:
        da_conta = _escolha(load_theme_choice())
    except Exception as erro:
        # O log é o que faltava para diagnosticar: a exceção era engolida
        # inteira e a única pista que sobrava era a cor da tela.
        logger.exception("falha ao ler o tema da conta")
        _anotar("tema.leitura_falhou",
                f"tema da conta não lido; {_motivo(erro)}", "_escolha_em_vigor")
        st.session_state[_LOAD_ERROR] = True
        return _escolha_local(uid)
    st.session_state.pop(_LOAD_ERROR, None)
    if da_conta is None:
        st.session_state[_SEM_PREFERENCIA] = True
    else:
        st.session_state.pop(_SEM_PREFERENCIA, None)
    escolha = _mais_recente(da_conta, _escolha_local(uid))
    st.session_state[_CACHE] = (uid, escolha)
    if escolha is not None:
        _guardar_ultimo_ok(uid, escolha)
        _lembrar(uid, escolha)
        if escolha.instante > (da_conta.instante if da_conta else 0):
            _levar_a_conta(escolha)
    _repintar_se_divergiu(escolha.tema if escolha else PADRAO)
    return escolha


def _levar_a_conta(escolha: Escolha) -> None:
    """A conta ficou atrás de uma escolha feita neste navegador: uma tentativa.

    Só escolha datada chega aqui -- memória de instante zero pode ter sido
    pintura, e pintura não vira preferência da conta. Falhou, fica pendente e o
    seletor tenta de novo.
    """
    try:
        save_theme(escolha.tema, at=escolha.instante)
    except Exception as erro:
        logger.exception("falha ao levar a escolha de tema à conta")
        _anotar("tema.escolha_local_nao_chegou_a_conta",
                f"escolha de {escolha.tema} feita neste navegador não chegou "
                f"à conta; {_motivo(erro)}", "_levar_a_conta")
        st.session_state[_NAO_GUARDADO] = (escolha.tema, escolha.instante)
    else:
        st.session_state.pop(_NAO_GUARDADO, None)
        st.session_state.pop(_SEM_PREFERENCIA, None)


def current_theme() -> str:
    """Tema da conta da sessão, resolvido **sem desenhar nada na tela**.

    Roda no topo de ``app.py``, acima de todo o conteúdo. Um ``st.warning``
    aqui, só nas execuções em que a leitura falha, insere um elemento ACIMA do
    ``st.tabs`` de Configurações, e o Streamlit devolve a seleção para a
    primeira aba quando o grupo de abas muda de posição (medido em
    22/09/2026). O aviso mora em :func:`render_theme_selector`, dentro da aba.
    """
    escolha = _escolha_em_vigor(require_user())
    return escolha.tema if escolha else PADRAO


def limpar_sessao_preservando_o_tema() -> None:
    """``st.session_state.clear()`` sem levar junto a escolha que está na tela.

    Trocar de usuário e invalidar a sessão limpam o estado inteiro, e levavam
    junto a única memória de escolha que uma execução sem conta alcança DENTRO
    da sessão. O cookie não cobre este caso: ``st.context.cookies`` é capturado
    no aperto de mão do websocket, e o cookie escrito nesta sessão só chega ao
    servidor na PRÓXIMA (medido em 12/10/2026).

    Não é usada quando alguém ENTRA: aí a conta muda, e herdar a escolha de
    quem estava antes seria mostrar a preferência de outra pessoa.
    """
    guardado = st.session_state.get(_ULTIMO_OK)
    st.session_state.clear()
    if _valido_como_ultimo_ok(guardado):
        st.session_state[_ULTIMO_OK] = guardado


def tema_para_pintar() -> str:
    """Tema a aplicar ANTES do portão de autenticação, e que nunca levanta.

    Toda execução interrompida pelo portão (tela de entrada, sessão que não deu
    para validar) também sai pintada. Sem conta na sessão -- expirou, ou o
    websocket reconectou e o ``session_state`` nasceu vazio -- vale a escolha
    que a sessão ou o navegador conhecem; sem nenhuma, o padrão.
    """
    # A marca da execução anterior sai ANTES de resolver: a resolução aqui
    # dentro não pode se comparar com o que foi pintado no run passado.
    st.session_state.pop(_PINTADO, None)
    escolha = _resolver_a_escolha_para_pintar()
    tema = escolha.tema if escolha else PADRAO
    st.session_state[_PINTADO] = tema
    st.session_state[_PINTADO_ESCOLHA] = (
        (escolha.tema, escolha.instante) if escolha else None)
    return tema


def _resolver_a_escolha_para_pintar() -> "Escolha | None":
    uid = str(principal().get("id", ""))
    if not uid:
        # A sessão vem ANTES do cookie no empate: dentro da sessão o cookie
        # chega atrasado uma conexão.
        return _mais_recente(_ultimo_ok_escolha(), escolha_do_navegador())
    try:
        return _escolha_em_vigor(uid)
    except Exception as erro:
        logger.exception("falha ao resolver o tema antes do portão")
        _anotar("tema.resolucao_antes_do_portao_falhou",
                f"tema não resolvido antes do portão; {_motivo(erro)}",
                "_resolver_a_escolha_para_pintar")
        return _escolha_local(uid)


def escolha_pintada() -> "Escolha | None":
    """A escolha por trás do tema desta execução; ``None`` quando é o padrão.

    É o que ``app.py`` leva ao cookie: a escolha, nunca a cor da tela.
    """
    return _escolha(st.session_state.get(_PINTADO_ESCOLHA))


def tema_pintado() -> str:
    """O tema que ESTA execução pintou no topo de ``app.py``.

    Para quem desenha fora do alcance de ``aplicar_tema``: o
    ``components.html`` roda num iframe, que não herda o CSS do app, e precisa
    receber a cor escolhida por fora -- senão segue o tema do sistema
    operacional, que é exatamente a troca automática que este módulo proíbe.
    Lê a marca em vez de resolver de novo; sem marca, resolve pelo mesmo
    caminho.
    """
    pintado = st.session_state.get(_PINTADO)
    if pintado in TEMAS:
        return pintado
    return tema_para_pintar()


# -- o comando ------------------------------------------------------------------

def _persist_choice() -> None:
    """O único lugar onde nasce uma escolha de tema.

    Roda no callback do widget, e não depois de lê-lo: ``st.rerun()`` reexecuta
    o script com o valor anterior ainda no widget, e a segunda troca de tema da
    sessão era revertida.

    O comando VALE primeiro, em todas as memórias locais, e só depois tenta ir
    ao banco. O instante da escolha é sempre maior que o da escolha em vigor: o
    clique é, por definição, a escolha mais recente.
    """
    uid = require_user()
    choice = st.session_state.get(_CHOICE)
    if choice not in TEMAS:
        return
    vigente = _em_cache(uid)
    anterior = vigente if isinstance(vigente, Escolha) else _escolha_local(uid)
    instante = max(_agora_ms(), anterior.instante + 1 if anterior else 0)
    escolha = Escolha(choice, instante)
    st.session_state[_CACHE] = (uid, escolha)
    _guardar_ultimo_ok(uid, escolha)
    _lembrar(uid, escolha)
    if _gravar_com_insistencia(escolha):
        st.session_state.pop(_NAO_GUARDADO, None)
        st.session_state.pop(_SEM_PREFERENCIA, None)
    else:
        # Aplicado e não guardado: a tela obedece agora, o cookie leva a
        # escolha na próxima execução, e o seletor insiste em guardá-la.
        st.session_state[_NAO_GUARDADO] = (escolha.tema, escolha.instante)


def _gravar_com_insistencia(escolha: Escolha) -> bool:
    """Insiste na gravação antes de desistir. ``True`` se o banco respondeu.

    A segunda tentativa cai depois de a execução concorrente ter devolvido a
    conexão, e é ela que faz a escolha chegar ao banco na prática.
    """
    ultima: BaseException | None = None
    for numero in range(_TENTATIVAS_DE_GRAVACAO):
        try:
            save_theme(escolha.tema, at=escolha.instante)
        except Exception as erro:
            ultima = erro
            logger.exception("falha ao gravar o tema (tentativa %d de %d)",
                             numero + 1, _TENTATIVAS_DE_GRAVACAO)
            if numero < len(_ESPERA_ENTRE_TENTATIVAS):
                time.sleep(_ESPERA_ENTRE_TENTATIVAS[numero])
        else:
            if numero:
                # Canário. A escolha chegou ao banco, então não há nada a
                # corrigir nesta execução -- por isso nasce como DETALHE, fora
                # da fila de correção e fora da tela de quem usa. Mas é a
                # medida de que a disputa pela única conexão do pool continua
                # viva: sem esta linha, a insistência esconderia exatamente o
                # defeito que ela existe para absorver.
                _anotar("tema.gravacao_insistiu",
                        f"tema gravado na tentativa {numero + 1} de "
                        f"{_TENTATIVAS_DE_GRAVACAO}; a anterior falhou com "
                        f"{_motivo(ultima)}", "_gravar_com_insistencia",
                        detalhe=True)
            return True
    _anotar("tema.gravacao_falhou",
            f"tema não gravado depois de {_TENTATIVAS_DE_GRAVACAO} "
            f"tentativas; última falha {_motivo(ultima)}",
            "_gravar_com_insistencia")
    return False


def _rotulo(tema: str) -> str:
    return "🌙 Dark (escuro)" if tema == "dark" else "☀️ Light (claro)"


def render_theme_selector() -> None:
    uid = require_user()
    escolha = _escolha_em_vigor(uid)
    theme = escolha.tema if escolha else PADRAO
    # O seletor mostra a ESCOLHA, não a cor da tela. Sem escolha registrada,
    # fica vazio -- e assim qualquer opção dispara o callback, inclusive a que
    # coincide com a cor atual. Era o motivo de existir o "gravar a cor da tela
    # ao abrir o seletor", que transformava o padrão em preferência da conta:
    # o ``selectbox`` não dispara ``on_change`` para o valor já marcado.
    sem_registro = escolha is None or (
        bool(st.session_state.get(_SEM_PREFERENCIA)) and escolha.instante == 0)
    st.session_state[_CHOICE] = None if sem_registro else escolha.tema
    st.selectbox(
        "Tema da minha conta", TEMAS,
        # O valor vem sempre da sessão (acima). ``index=None`` só sem escolha
        # registrada: com ele o Streamlit desenha o botão de limpar, e limpar
        # não é escolha nenhuma.
        index=None if sem_registro else 0,
        format_func=_rotulo,
        placeholder="Nenhum tema escolhido ainda",
        key=_CHOICE, on_change=_persist_choice,
        help="O tema muda só quando você escolhe aqui. Fica salvo na sua conta.",
    )
    if sem_registro and not st.session_state.get(_LOAD_ERROR):
        if escolha is None:
            st.caption("Você ainda não escolheu um tema: o app usa o escuro "
                       "padrão e só muda quando você escolher.")
        else:
            st.caption(f"A tela está em {_rotulo(theme)}, como este navegador "
                       "lembrava, mas a sua conta ainda não tem escolha "
                       "registrada. Escolha acima para registrar.")
    if st.session_state.get(_LOAD_ERROR):
        # Não é ``pop``: enquanto a leitura falhar, a pessoa precisa continuar
        # vendo por quê. A primeira leitura que der certo limpa a marca.
        mantido = ("A tela segue na sua última escolha conhecida"
                   if escolha else "A tela segue no escuro padrão")
        st.warning(
            "Não foi possível ler o tema da sua conta agora. "
            f"{mantido} e nada foi alterado. Escolher aqui continua valendo "
            "na hora."
        )
    pendente = _escolha(st.session_state.get(_NAO_GUARDADO))
    if pendente is not None:
        # Uma tentativa a mais, agora: a disputa pela conexão é do instante do
        # clique, e quem está vendo esta tela já passou desse instante. Só uma,
        # e só aqui, para não virar tráfego de banco em toda tela.
        try:
            save_theme(pendente.tema, at=pendente.instante)
        except Exception as erro:
            logger.exception("falha ao regravar o tema pendente")
            _anotar("tema.pendencia_nao_regravada",
                    f"escolha pendente de {pendente.tema} não guardada na "
                    f"conta na segunda chance; {_motivo(erro)}",
                    "render_theme_selector")
        else:
            st.session_state.pop(_NAO_GUARDADO, None)
            st.session_state.pop(_SEM_PREFERENCIA, None)
            pendente = None
    if pendente is not None:
        st.warning(
            "Tema aplicado agora, mas não foi possível guardá-lo na sua conta. "
            "Ele já vale nesta sessão e neste navegador, e vai para a conta "
            "assim que o banco responder -- sem passar por cima de uma escolha "
            "mais nova feita em outro aparelho."
        )
    _render_diagnostico(escolha, theme)


def _quando(instante: int) -> str:
    if not instante:
        return "de antes do registro de horário"
    try:
        from zoneinfo import ZoneInfo
        fuso = ZoneInfo("America/Sao_Paulo")
    except Exception:
        fuso = timezone.utc
    return datetime.fromtimestamp(instante / 1000, fuso).strftime("%d/%m/%Y %H:%M:%S")


def _descrever(escolha: "Escolha | None") -> str:
    if escolha is None:
        return "nenhuma"
    return f"{escolha.tema} (escolhido {_quando(escolha.instante)})"


def _render_diagnostico(escolha: "Escolha | None", pintado: str) -> None:
    """Porta de entrada para o diagnóstico do tema, fechada por padrão.

    Correções foram feitas às cegas porque o app não dizia de onde vinha o
    tema da execução: o log do Cloud não é acessível a quem usa. Um expander
    fechado, ao lado do próprio seletor que ele explica.
    """
    uid = require_user()
    if st.session_state.get(_LOAD_ERROR):
        origem = ("a conta não respondeu agora; vale a última escolha que esta "
                  "sessão/navegador conhece" if escolha else
                  "a conta não respondeu e não há escolha conhecida; padrão")
        gravada = "não deu para conferir agora"
    elif escolha is None:
        origem = "ninguém escolheu ainda; padrão (nunca gravado como escolha)"
        gravada = "não há escolha"
    else:
        origem = "a sua escolha mais recente"
        gravada = ("pendente — ainda não confirmada"
                   if st.session_state.get(_NAO_GUARDADO) else
                   "não — só neste navegador" if st.session_state.get(_SEM_PREFERENCIA)
                   else "sim")
    linhas = [
        f"- Tema aplicado agora: **{pintado}**",
        f"- Escolha em vigor: {_descrever(escolha)}",
        f"- Origem: {origem}",
        f"- Gravada na conta: {gravada}",
        f"- Lembrete deste navegador (cookie): {_descrever(escolha_do_navegador(uid))}",
    ]
    with st.expander("Diagnóstico do tema", expanded=False):
        st.markdown("\n".join(linhas))
        st.caption(
            "Serve para relatar um tema que não obedeceu: estas linhas dizem "
            "de onde veio a cor desta tela. Nenhuma delas muda o tema -- só a "
            "sua escolha no seletor muda."
        )

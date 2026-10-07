"""
app.py — Dashboard Financeiro Unificado
Ponto de entrada principal. Configura a página, aplica o tema e roteia para os módulos.

Roteamento: lazy imports manuais por branch.
Motivo: isolamento de erros por módulo e compatibilidade futura com autenticação.
"""
import importlib
import logging

import streamlit as st

from core.modulos_frescos import descartar_se_o_codigo_mudou

# Antes de qualquer outro import do projeto: depois de um deploy, o Cloud roda
# este arquivo novo sobre módulos antigos em memória (ImportError em 30/09/2026).
descartar_se_o_codigo_mudou()

from core.app_test_mode import is_app_test_mode, module_for_route  # noqa: E402

logger = logging.getLogger(__name__)

MSG_ERRO_GENERICO_AO_CARREGAR_MODULO = (
    "Não foi possível carregar este módulo. Tente novamente em instantes."
)

st.set_page_config(
    page_title="Dashboard Financeiro Unificado",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

_APP_TEST_MODE = is_app_test_mode()

# O modo sintético é resolvido antes de importar configuração/autenticação ou
# qualquer view financeira. Assim a validação visual não lê .env, banco, rede ou
# artefatos de dados reais. O caminho normal permanece inalterado.
if not _APP_TEST_MODE:
    from core.auth import verificar_autenticacao
    from core.config import settings
    from design.componentes import mensagem_erro, transicao_de_pagina
    from design.tema import aplicar_tema
    from design.theme_selector import lembrar_no_navegador, tema_para_pintar

    # O tema é aplicado ANTES do portão: toda execução que o portão interrompe
    # -- tela de login, ou o aviso de sessão que não deu para validar -- saía
    # sem CSS nenhum, e o tema base do `config.toml` é escuro. Era isso que
    # repintava o app de escuro no meio de uma importação em Configurações: a
    # importação longa ocupa a única conexão do pool e a validação da sessão
    # na execução seguinte não consegue conexão.
    # Continua sendo UM `st.markdown` só, na mesma posição: o número de
    # elementos acima da página não pode depender do tema (ver `aplicar_tema`).
    _tema = tema_para_pintar()
    aplicar_tema(_tema)
    # E fica lembrado no NAVEGADOR. O tema é preferência de conta, mas a conta
    # some da sessão quando ela expira ou quando o websocket reconecta -- e a
    # execução sem conta não tinha de onde saber que a pessoa estava no claro.
    # Também emitido em toda execução e nos dois temas, para que a contagem de
    # elementos acima da página não mude com o tema.
    lembrar_no_navegador(_tema)
    verificar_autenticacao()

# ── Mapeamento: label da sidebar → módulo em views/ ──────────────────────────
_ROTAS: dict[str, str] = {
    "📊 Dashboard Geral":     "dashboard_geral",
    "💰 Controle Financeiro": "controle_financeiro",
    "📈 Investimentos":       "investimentos",
    "🏢 Empresas B3":         "empresas_b3",
    # 🌎 e não 🇺🇸: Windows não renderiza emoji de bandeira (vira as letras "US")
    "🌎 Empresas Americanas": "empresas_americanas",
    "🏬 Seleção de FIIs":      "fiis",
    "🌐 Portfólio Global":    "portfolio_global",
    "⚙️ Configurações":       "configuracoes",
}

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # Tema, "Sair / trocar usuário" e "Conectado como X" saíram daqui em
    # 21/09/2026 e moram em Configurações → Geral. Eram três coisas visíveis em
    # toda tela, disputando a barra com o que ela existe para fazer: navegar.
    # O tema continua sendo APLICADO no topo deste arquivo; só a escolha mudou
    # de lugar.
    # O nome de quem está logado voltou ao topo em 03/10/2026, a pedido do dono
    # do app -- só a identificação, discreta, acima da marca. "Sair / trocar
    # usuário" continua em Configurações → Geral.
    from design.componentes import marca_sidebar_html

    _usuario = ""
    if not _APP_TEST_MODE:
        from core.user_context import principal

        _usuario = str(principal().get("name") or principal().get("email") or "")
    st.markdown(marca_sidebar_html(_usuario), unsafe_allow_html=True)

    st.markdown('<div class="nav-section">Navegação</div>', unsafe_allow_html=True)
    if _APP_TEST_MODE:
        # Essas são as únicas rotas permitidas: todas resolvem para a view em
        # memória ``views.app_test_mode`` antes que qualquer view real seja
        # importada.
        opcoes_menu = [
            "🏢 Empresas B3",
            "🌎 Empresas Americanas",
            "🏬 Seleção de FIIs",
            "🌐 Portfólio Global",
        ]
    else:
        opcoes_visao = ["📊 Dashboard Geral"]
        opcoes_financas = ["💰 Controle Financeiro"]
        opcoes_invest = ["📈 Investimentos", "🏢 Empresas B3",
                         "🌎 Empresas Americanas",
                         "🏬 Seleção de FIIs",
                         "🌐 Portfólio Global"]
        # Inteligência de Mercado, Macro Internacional e Homologação saíram da
        # sidebar a pedido do dono do app: são retaguarda analítica que alimenta
        # os módulos, não tela de consumo. Os módulos em ``views/`` continuam no
        # repositório e seus motores em ``core/`` seguem sendo consultados pelas
        # telas que dependem deles -- só a porta de entrada foi retirada.
        # "Grau de Confiança" deixou de ser rota própria e virou aba dentro de
        # Configurações (``views/configuracoes.py``); a porta de entrada existe,
        # mudou de lugar (``memoria: diagnostico-precisa-porta-de-entrada``).
        # "Documentação" seguiu o mesmo caminho em 24/09/2026: é a última aba
        # de Configurações.
        opcoes_sistema = ["⚙️ Configurações"]
        opcoes_menu = opcoes_visao + opcoes_financas + opcoes_invest + opcoes_sistema

    menu = st.radio(
        "Navegação",
        opcoes_menu,
        label_visibility="collapsed",
        key="app_main_navigation",
    )

    # Os avisos de ambiente (chave de API ausente, variavel nao configurada)
    # sairam da sidebar a pedido do dono do app: sao detalhe de operacao, nao
    # informacao de uso, e apareciam embaixo do menu em todas as telas. Nao
    # foram descartados -- continuam no log de quem opera e na aba
    # Configuracoes > Banco de dados, que so o admin ve.
    # O script inteiro reexecuta a cada rerun, entao a marca de "ja avisei"
    # precisa morar na sessao -- variavel de modulo voltaria ao inicial e o log
    # repetiria o mesmo aviso dezenas de vezes. ``getattr`` porque o modo
    # sintetico e os testes trocam ``streamlit`` por um dublê sem session_state.
    _sessao = getattr(st, "session_state", None)
    _ja_avisou = _sessao is not None and _sessao.get("_avisos_ambiente_logados")
    if not _APP_TEST_MODE and not _ja_avisou:
        if _sessao is not None:
            _sessao["_avisos_ambiente_logados"] = True
        for aviso in settings.validate():
            logger.warning("ambiente: %s", aviso)

# ── Transição de página ───────────────────────────────────────────────────────
# Antes de qualquer conteúdo da rota, e sem condição: o Streamlit entrega os
# elementos na ordem em que o script os cria, então este é o primeiro a chegar
# ao navegador e já age enquanto a view ainda carrega. Sem condição porque um
# elemento que existisse só em alguns runs deslocaria os `st.tabs` das views, e
# o Streamlit devolve a seleção para a primeira aba quando o grupo de abas muda
# de posição.
# A única condição é o modo sintético, que vale para a sessão inteira e não
# muda de um run para o outro -- a posição do elemento continua fixa.
if not _APP_TEST_MODE:
    transicao_de_pagina(menu)

# ── Roteamento ────────────────────────────────────────────────────────────────
modulo_nome = _ROTAS.get(menu)

if modulo_nome:
    try:
        modulo = importlib.import_module(f"views.{module_for_route(modulo_nome)}")
        modulo.render()
    except Exception as exc:  # noqa: BLE001 - fronteira de isolamento entre rotas
        if _APP_TEST_MODE:
            st.error(f'Erro ao carregar o modo sintético "{menu}": {exc}')
        else:
            # O detalhe tecnico completo (driver, host, porta, stack) vai so
            # para o log — nao para a tela. A pessoa usuaria ve uma mensagem
            # amigavel; quem opera o app le o log para diagnosticar. Ver
            # achado A-013 (vazamento de excecao crua ao usuario final).
            logger.exception('Erro ao carregar o modulo "%s"', menu)
            # Log de lacunas: a mesma identidade (tipo + frame do projeto, sem
            # a mensagem) vai para a fila que o agente de correcao le.
            try:
                from core.lacunas import registrar_excecao

                registrar_excecao(exc, rota=menu)
            except Exception:  # noqa: BLE001 - lacuna e extra, nunca requisito
                logger.exception("falha ao registrar a excecao como lacuna")
            mensagem_erro(
                f'Erro ao carregar o módulo "{menu}"',
                MSG_ERRO_GENERICO_AO_CARREGAR_MODULO,
            )
            # A-013 tirou a excecao crua da tela, e com ela sumiu qualquer
            # pista: em producao o traceback vive so no log da nuvem, que a
            # pessoa usuaria nao alcanca. Isto devolve a IDENTIDADE do defeito
            # (tipo + arquivo:linha deste repositorio) sem devolver a
            # mensagem, que e onde moram driver, host, porta e credencial.
            # Envolvido no proprio try: um diagnostico que falha nao pode
            # derrubar o tratamento do erro que ele veio explicar.
            # So o administrador ve o bloco desde 05/10/2026: detalhe tecnico
            # nao aparece na tela de uso, e a identidade do erro ja foi para
            # Configuracoes -> Restricoes pelo `registrar_excecao` acima.
            try:
                from core.erro_diagnostico import (
                    identidade_do_erro,
                    relatorio_tecnico,
                )
                from core.user_context import is_admin

                if is_admin():
                    with st.expander(f"Detalhes tecnicos - {identidade_do_erro(exc)}"):
                        st.caption(
                            "Copie este bloco ao reportar. Ele nao contem dados, "
                            "credenciais nem endereco de banco."
                        )
                        st.code(relatorio_tecnico(exc), language="text")
            except Exception:  # noqa: BLE001 - diagnostico e extra, nunca requisito
                logger.exception("falha ao montar o diagnostico da rota")
else:
    st.warning(f'Rota não encontrada para "{menu}".')

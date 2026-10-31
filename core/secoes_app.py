"""Seções do menu lateral que cada conta escolhe ver.

Pedido do dono do app em 31/10/2026: personalizar o app4 tirando do menu as
seções que a pessoa não usa, e o Dashboard Geral se montar só com o que ficou.
Dashboard Geral e Configurações não saem nunca -- o primeiro é a porta de
entrada, o segundo é onde a escolha se desfaz. Sem Configurações no menu, quem
escondesse tudo não teria como voltar.

**Esconder não é apagar.** A tela continua no repositório, o módulo continua
em ``app.py::_ROTAS``, os dados, as carteiras salvas e as conversas com a LLM
continuam no banco. Muda só o que o menu lateral oferece a ESTA conta, e a
seção volta inteira quando a pessoa a devolve. A premissa de que toda LLM
consulta todo dado disponível (``CLAUDE.md``) também não muda: o contexto de
mercado não olha o menu.

Três camadas, cada uma no seu lugar:

- o catálogo (:data:`SECOES`) e as regras puras (:func:`normalizar`,
  :func:`filtrar_menu`), testáveis sem banco nem Streamlit;
- a ida e a volta ao banco, em ``core/user_preferences.py``;
- o cache da sessão (:func:`ocultas_da_sessao`), porque ``app.py`` desenha o
  menu a CADA rerun e uma ida ao banco por clique é o custo que o tema já
  mediu e eliminou.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Secao:
    """Uma tela do menu lateral.

    ``rotulo`` é a chave de ``app.py::_ROTAS`` (com o ícone) e ``modulo`` é o
    valor -- o arquivo em ``views/``. A preferência grava o ``modulo``: o
    rótulo muda de ícone ou de grafia com mais frequência do que o arquivo.
    """

    modulo: str
    rotulo: str
    nome: str
    descricao: str
    fixa: bool = False


#: Na ordem do menu lateral. ``tests/test_secoes_app.py`` confere este
#: catálogo contra ``app.py::_ROTAS``: tela nova que entrar no menu sem entrar
#: aqui quebra o teste em vez de ficar sem interruptor.
SECOES: tuple[Secao, ...] = (
    Secao("dashboard_geral", "📊 Dashboard Geral", "Dashboard Geral",
          "Visão consolidada. Monta-se com as seções que você mantém no menu.",
          fixa=True),
    Secao("controle_financeiro", "💰 Controle Financeiro", "Controle Financeiro",
          "Receitas, despesas, cartão de crédito e o caixa do mês."),
    Secao("investimentos", "📈 Investimentos", "Investimentos",
          "A sua carteira real: posições, proventos, Tesouro e imposto."),
    Secao("empresas_b3", "🏢 Empresas B3", "Empresas B3",
          "Estudo de ações brasileiras e a carteira recomendada da B3."),
    Secao("empresas_americanas", "🌎 Empresas Americanas", "Empresas Americanas",
          "Estudo de ações dos EUA e a carteira recomendada americana."),
    Secao("fiis", "🏬 Seleção de FIIs", "Seleção de FIIs",
          "Triagem de fundos imobiliários e a carteira-modelo de FIIs."),
    Secao("portfolio_global", "🌐 Portfólio Global", "Portfólio Global",
          "As carteiras recomendadas somadas num patrimônio só."),
    Secao("configuracoes", "⚙️ Configurações", "Configurações",
          "Onde esta escolha se faz e se desfaz. Não sai do menu.",
          fixa=True),
)

#: Os módulos que podem sair do menu, na ordem dele.
OPCIONAIS: tuple[str, ...] = tuple(s.modulo for s in SECOES if not s.fixa)
#: Os que não saem nunca.
FIXAS: tuple[str, ...] = tuple(s.modulo for s in SECOES if s.fixa)
#: Todos, para quem precisa do "nada oculto".
TODAS: frozenset[str] = frozenset(s.modulo for s in SECOES)

_POR_MODULO = {s.modulo: s for s in SECOES}

#: ``(uid, ocultas, falhou_em)`` -- a sessão guarda a escolha da conta que a
#: leu. A conta entra na chave porque ``st.session_state`` sobrevive à troca de
#: usuário no mesmo navegador até ``encerrar_sessao`` limpá-lo.
_CACHE = "_app4_secoes_ocultas"
#: Depois de uma leitura que falhou, quanto esperar antes de tentar de novo. O
#: menu é desenhado a cada rerun; sem a espera, um banco fora do ar receberia
#: uma consulta por clique na única conexão do pool do Cloud.
_ESPERA_APOS_FALHA_S = 60.0


def secao(modulo: str) -> Secao | None:
    return _POR_MODULO.get(modulo)


def normalizar(modulos: Iterable[object] | None) -> tuple[str, ...]:
    """Só o que pode sair, sem repetição, na ordem do menu.

    Módulo desconhecido (tela que saiu do app depois de gravada a preferência)
    e módulo fixo (Dashboard Geral, Configurações) são descartados aqui, e não
    na tela: uma gravação adulterada ou antiga não consegue esconder a porta de
    volta.
    """
    pedidos = {str(m) for m in (modulos or ()) if m}
    return tuple(m for m in OPCIONAIS if m in pedidos)


def filtrar_menu(opcoes: Iterable[str], rotas: Mapping[str, str],
                 ocultas: Iterable[object]) -> list[str]:
    """As opções do menu menos as seções ocultas, na ordem recebida.

    ``rotas`` é ``app.py::_ROTAS`` (rótulo → módulo). Opção que não está em
    ``rotas`` passa: decidir o que fazer com ela é do roteador, não daqui.
    """
    fora = set(normalizar(ocultas))
    return [o for o in opcoes if rotas.get(o) not in fora]


def visiveis(ocultas: Iterable[object]) -> frozenset[str]:
    """Todos os módulos do menu menos os ocultos -- os fixos sempre entram."""
    return TODAS - frozenset(normalizar(ocultas))


# -- sessão -------------------------------------------------------------------

def _estado(state: MutableMapping | None) -> MutableMapping:
    if state is not None:
        return state
    import streamlit as st

    return st.session_state


def _uid() -> str:
    from core.user_context import principal

    return str(principal().get("id", ""))


def ocultas_da_sessao(state: MutableMapping | None = None) -> frozenset[str]:
    """O que esta conta tirou do menu, lido do banco uma vez por sessão.

    Falha de leitura devolve "nada oculto": o menu completo é o lado seguro --
    pior seria a pessoa perder o acesso a uma tela porque o banco piscou. A
    falha fica marcada para :func:`leitura_falhou` (Configurações → Geral avisa)
    e a próxima tentativa espera :data:`_ESPERA_APOS_FALHA_S`.
    """
    estado = _estado(state)
    uid = _uid()
    if not uid:
        return frozenset()
    guardado = estado.get(_CACHE)
    if isinstance(guardado, tuple) and len(guardado) == 3 and guardado[0] == uid:
        _, ocultas, falhou_em = guardado
        if falhou_em is None or time.time() - falhou_em < _ESPERA_APOS_FALHA_S:
            return frozenset(ocultas)
    try:
        from core.user_preferences import load_secoes_ocultas

        ocultas = frozenset(normalizar(load_secoes_ocultas()))
    except Exception:  # noqa: BLE001 - menu completo é o lado seguro
        logger.warning("leitura das seções ocultas falhou", exc_info=True)
        estado[_CACHE] = (uid, frozenset(), time.time())
        return frozenset()
    estado[_CACHE] = (uid, ocultas, None)
    return ocultas


def visiveis_da_sessao(state: MutableMapping | None = None) -> frozenset[str]:
    return visiveis(ocultas_da_sessao(state))


def leitura_falhou(state: MutableMapping | None = None) -> bool:
    guardado = _estado(state).get(_CACHE)
    return (isinstance(guardado, tuple) and len(guardado) == 3
            and guardado[0] == _uid() and guardado[2] is not None)


def esquecer_leitura(state: MutableMapping | None = None) -> None:
    """Descarta o cache: a próxima chamada vai ao banco."""
    _estado(state).pop(_CACHE, None)


def gravar(ocultas: Iterable[object],
           state: MutableMapping | None = None) -> tuple[bool, str]:
    """Grava a escolha e já a põe na sessão, para o menu mudar no próximo rerun.

    A sessão só muda DEPOIS de o banco aceitar: o menu nunca mostra uma escolha
    que a próxima sessão não vai encontrar.
    """
    novas = normalizar(ocultas)
    try:
        from core.user_preferences import save_secoes_ocultas

        save_secoes_ocultas(list(novas))
    except Exception as exc:  # noqa: BLE001
        logger.exception("falha ao gravar as seções ocultas %s", novas)
        try:
            from core.lacunas import registrar_excecao

            registrar_excecao(exc, rota="⚙️ Configurações")
        except Exception:  # noqa: BLE001 - lacuna é extra, nunca requisito
            logger.exception("falha ao registrar a exceção como lacuna")
        return False, ("Não foi possível salvar a escolha. O menu continua como "
                       "estava; o detalhe técnico foi registrado para o "
                       "administrador.")
    _estado(state)[_CACHE] = (_uid(), frozenset(novas), None)
    if not novas:
        return True, "Todas as seções estão no menu."
    nomes = ", ".join(_POR_MODULO[m].nome for m in novas)
    return True, (f"Fora do menu: {nomes}. Nada foi apagado: a seção volta "
                  "inteira quando você a ligar de novo.")

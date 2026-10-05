"""
design/lacunas.py
Restricoes e detalhes tecnicos SAEM da tela de uso e vao para o log de lacunas,
que o administrador le em Configuracoes -> Restricoes.

Pedido de 05/10/2026: "Informacoes tecnicas sobre limitacoes nao devem aparecer
diretamente no layout para o usuario." Ate entao ``aviso_lacuna`` exibia o aviso
E o registrava; agora so registra. A assinatura ficou igual (``nivel`` e
``icon`` sao aceitos e ignorados) para as chamadas existentes continuarem
validas e a guarda de CI (``tests/test_lacunas_guarda.py``) continuar vendo
cada uma delas.

Duas portas, pela natureza do que se declara:

* ``aviso_lacuna`` -- LACUNA: dado, fonte, historico ou calculo que nao existe.
  Nasce ``aberta`` e entra na fila do corretor diario.
* ``detalhe_tecnico`` -- DETALHE: procedencia, versao de metodologia, data da
  vitrine, tamanho de amostra, nome de tabela. Nao ha o que corrigir; nasce
  ``legitima`` e fica fora da fila, mas aparece para o administrador.

E uma terceira para erro de ACAO do usuario (salvar, importar, consultar):

* ``falha_de_acao`` -- mostra so a frase amigavel e manda a excecao (tipo +
  frame do projeto, nunca a mensagem crua) para a aba como "Erro".

Nao usar nenhuma das duas primeiras para estado vazio do proprio usuario ("nenhum
lancamento no filtro") nem para retorno de acao ("importado com sucesso"):
isso e conversa com o usuario e continua na tela.

``codigo`` e obrigatorio e estavel (``tela.<area>.<o_que_falta>`` ou
``detalhe.<area>.<o_que_e>``): ele e a chave, entao o texto pode mudar sem
virar registro novo.
"""
from __future__ import annotations

import logging
import sys

from core.lacunas import registrar_excecao, registrar_lacuna
from core.lacunas.evento import PREFIXO_DETALHE
from core.lacunas.registro import modulo_do_frame

_log = logging.getLogger(__name__)

AVISO_REGISTRADO = "O detalhe técnico foi registrado para o administrador."


def aviso_lacuna(mensagem: str, *, codigo: str, nivel: str = "info",
                 entidade: str | None = None, icon: str | None = None) -> None:
    """Registra a lacuna (fonte ``tela``) sem exibi-la. Nunca levanta.

    ``nivel`` e ``icon`` sobram da epoca em que o aviso ia para a tela; ficam
    para nao quebrar quem chama.
    """
    del nivel, icon
    registrar_lacuna("tela", codigo, mensagem, entidade=entidade,
                     modulo=modulo_do_frame(sys._getframe(1)))


def detalhe_tecnico(mensagem: str, *, codigo: str,
                    entidade: str | None = None) -> None:
    """Registra um detalhe tecnico que antes ia para a tela. Nunca levanta.

    ``codigo`` sem o prefixo ``detalhe.`` ganha o prefixo: e ele que faz o
    registro nascer ``legitima`` e ficar fora da fila de correcao.
    """
    codigo = (codigo or "").strip()
    if not codigo.startswith(PREFIXO_DETALHE):
        codigo = PREFIXO_DETALHE + codigo
    registrar_lacuna("tela", codigo, mensagem, entidade=entidade,
                     modulo=modulo_do_frame(sys._getframe(1)))


def falha_de_acao(mensagem: str, exc: BaseException) -> None:
    """Erro de uma acao do usuario: a tela recebe ``mensagem`` (sem o texto da
    excecao, que pode carregar SQL, host ou credencial); o log da nuvem recebe
    o traceback e a aba Restricoes, a identidade do erro. Nunca levanta."""
    _log.error("falha de acao: %s", mensagem, exc_info=exc)
    registrar_excecao(exc)
    try:
        import streamlit as st

        st.error(f"{mensagem} {AVISO_REGISTRADO}")
    except Exception:  # noqa: BLE001 - sem tela, o registro ja foi feito
        _log.warning("falha_de_acao sem tela", exc_info=True)

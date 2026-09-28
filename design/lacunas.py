"""
design/lacunas.py
``aviso_lacuna``: o aviso de tela que diz "falta dado" E o registra no log de
lacunas, numa chamada so.

Usar no lugar de ``st.info``/``st.warning``/``st.caption`` quando o aviso
declara uma LACUNA -- dado, fonte, historico ou calculo que nao existe. Nao
usar para estado vazio do proprio usuario ("nenhum lancamento no filtro"): isso
nao e defeito a corrigir e so poluiria a fila.

``codigo`` e obrigatorio e estavel (``tela.<area>.<o_que_falta>``): ele e a
chave da lacuna, entao o texto exibido pode mudar sem virar lacuna nova.
"""
from __future__ import annotations

import sys

import streamlit as st

from core.lacunas import registrar_lacuna
from core.lacunas.registro import modulo_do_frame

_NIVEIS = ("info", "warning", "caption")


def aviso_lacuna(mensagem: str, *, codigo: str, nivel: str = "info",
                 entidade: str | None = None, icon: str | None = None) -> None:
    """Exibe ``mensagem`` no ``nivel`` pedido e registra a lacuna (fonte ``tela``).

    O aviso aparece mesmo que o registro falhe: ``registrar_lacuna`` nunca levanta.
    """
    # Resolvido na chamada, nao na importacao: teste que troca `st.info` alcanca.
    exibir = getattr(st, nivel if nivel in _NIVEIS else "info")
    if icon and nivel != "caption":
        exibir(mensagem, icon=icon)
    else:
        exibir(mensagem)
    registrar_lacuna("tela", codigo, mensagem, entidade=entidade,
                     modulo=modulo_do_frame(sys._getframe(1)))

"""
design/gravacao_carteira.py
Gravação da carteira-modelo (EUA e FIIs) a partir de um botão.

Por que existe (08/11/2026): as carteiras EUA e FII "salvas" não chegavam ao
Dashboard Geral nem ao Portfólio Global. No banco, a última carteira EUA era
de 15/09 e a última de FIIs de 01/09; nenhuma exceção de gravação tinha sido
registrada. Três causas, uma por tela:

1. **EUA** — o botão primário, com ícone de disquete, era "Usar na Avaliação
   de Portfólio", que só põe a carteira na sessão. O que grava era o
   secundário. A mensagem "Carteira-modelo enviada" parecia de gravação.
2. **Rerun longo** — ``if st.button(...)`` só executa quando o script, rodando
   de novo do topo, chega à linha do botão. Na Seleção de FIIs esse rerun
   chegou a 325 s; nas Empresas Americanas, a 112 s. Quem troca de tela antes
   disso interrompe o rerun e o clique se perde sem erro nenhum. Aqui a
   gravação vai num ``on_click``, que o Streamlit executa ANTES do rerun.
3. **FIIs** — o botão ficava desabilitado sempre que a validação PIT da
   metodologia vigente não estava aprovada (6.10.0 reprova desde 04/10/2026),
   sem dizer o motivo na tela. Ver ``views/fiis.py::_render_save_portfolio``.

A tela também passa a dizer qual carteira está de fato no banco
(``resumo_da_carteira_salva``): era a falta dessa frase que deixava
"supostamente salva" passar por salva.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable

import streamlit as st

from core.lacunas import registrar_excecao
from data_pipeline.utils.date_utils import fmt_datetime_br
from design.lacunas import AVISO_REGISTRADO

_log = logging.getLogger(__name__)

_PREFIXO_ESTADO = "_gravacao_carteira_resultado::"


def ao_clicar_gravar(chave: str, gravar: Callable[[], str], *,
                     sucesso: str, falha: str) -> Callable[[], None]:
    """``on_click`` que grava a carteira antes do rerun e guarda o desfecho.

    ``gravar`` devolve o id do modelo. ``sucesso`` pode conter ``{id}``. O
    desfecho é exibido por ``mostrar_resultado(chave)`` no lugar do botão —
    escrever na tela de dentro do callback poria a mensagem no topo da página.
    Nunca levanta: erro vira mensagem amigável na tela e registro na aba
    Restrições, como ``falha_de_acao``.
    """
    def _callback() -> None:
        try:
            model_id = str(gravar())
        except Exception as exc:  # noqa: BLE001 - fronteira de persistência
            _log.error("falha de acao: %s", falha, exc_info=exc)
            registrar_excecao(exc)
            st.session_state[_PREFIXO_ESTADO + chave] = (
                "erro", f"{falha} {AVISO_REGISTRADO}")
        else:
            st.session_state[_PREFIXO_ESTADO + chave] = (
                "ok", sucesso.format(id=model_id[:8]))
    return _callback


def mostrar_resultado(chave: str) -> None:
    """Exibe (uma vez) o desfecho da última gravação disparada por ``chave``."""
    desfecho = st.session_state.pop(_PREFIXO_ESTADO + chave, None)
    if not desfecho:
        return
    tipo, mensagem = desfecho
    if tipo == "ok":
        st.success(mensagem)
    else:
        st.error(mensagem)


def _simbolos(itens: Iterable[dict]) -> set[str]:
    return {
        str(item.get("ticker") or item.get("symbol") or "").strip().upper()
        for item in itens or ()
    } - {""}


def resumo_da_carteira_salva(ativa: dict | None, simbolos_na_tela: Iterable[str],
                             *, unidade: str = "ativos") -> str:
    """Frase que diz qual carteira está no banco e se é a que está na tela.

    O Dashboard Geral e o Portfólio Global leem só o banco; sem esta frase a
    pessoa não tem como saber que a carteira montada ainda não foi gravada.
    """
    na_tela = {str(s).strip().upper() for s in simbolos_na_tela} - {""}
    if not ativa or not ativa.get("items"):
        return (
            "**Nenhuma carteira padrão salva.** O Dashboard Geral e o "
            "Portfólio Global ficam sem esta classe até você salvar."
        )
    salvos = _simbolos(ativa.get("items"))
    quando = fmt_datetime_br(ativa.get("created_at"))
    base = (f"**No banco hoje:** {len(salvos)} {unidade}, salva em {quando}. "
            "É ela que o Dashboard Geral e o Portfólio Global mostram.")
    if salvos == na_tela:
        return base + " Mesmos ativos da carteira acima."
    saem = len(salvos - na_tela)
    entram = len(na_tela - salvos)
    return (base + f" **Difere da carteira acima** ({entram} entram, {saem} "
            "saem): ela só passa a valer quando você salvar.")

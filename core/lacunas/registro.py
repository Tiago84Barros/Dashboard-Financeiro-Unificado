"""
core/lacunas/registro.py
Porta publica do log de lacunas: ``registrar_lacuna``.

Tres garantias, nesta ordem de importancia:

1. NUNCA levanta e nunca bloqueia a tela. Registrar uma lacuna nao pode virar
   uma lacuna nova. Qualquer falha vai para ``logging`` e para ali.
2. Grava UMA vez por impressao digital por sessao do Streamlit. O script roda
   de novo a cada clique; sem isso a frequencia mediria cliques, e a fila de
   correcao seria ordenada por quem mais mexeu na tela.
       memoria: medir-a-fonte-que-a-decisao-le
3. Na nuvem o UPSERT sai numa thread de um worker, para a latencia do Supabase
   nao somar ao render.

O modulo inferido e ``caminho/relativo.py:funcao`` SEM numero de linha: a linha
muda a cada edicao do arquivo e mudaria a impressao digital junto.
"""
from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from core.lacunas import destino
from core.lacunas.evento import construir_lacuna

_log = logging.getLogger(__name__)

_PACOTE = Path(__file__).resolve().parent
_CHAVE_SESSAO = "_lacunas_vistas"

_vistas_processo: set[str] = set()
_trava = threading.Lock()
_executor: ThreadPoolExecutor | None = None
_envios: list[Future] = []


def _modulo_chamador() -> str:
    """Primeiro frame fora de ``core/lacunas``, como ``caminho.py:funcao``."""
    frame = sys._getframe(1)
    while frame is not None:
        caminho = Path(frame.f_code.co_filename).resolve()
        if caminho.parent != _PACOTE:
            try:
                rel = caminho.relative_to(destino.RAIZ).as_posix()
            except ValueError:
                rel = caminho.name
            return f"{rel}:{frame.f_code.co_name}"
        frame = frame.f_back
    return ""


def _conjunto_de_vistas() -> set[str]:
    """O conjunto da sessao do Streamlit quando ha sessao; senao, o do processo."""
    try:
        from streamlit.runtime import exists

        if exists():
            import streamlit as st

            return st.session_state.setdefault(_CHAVE_SESSAO, set())
    except Exception:  # noqa: BLE001 - sem runtime, o processo e a sessao
        pass
    return _vistas_processo


def _ja_visto(impressao: str) -> bool:
    with _trava:
        vistas = _conjunto_de_vistas()
        if impressao in vistas:
            return True
        vistas.add(impressao)
        return False


def _engine():
    from core.database import get_engine

    return get_engine()


def _gravar_banco_seguro(engine, lacuna) -> None:
    try:
        destino.gravar_banco(engine, lacuna)
    except Exception:  # noqa: BLE001 - log de lacuna nunca derruba nada
        _log.warning("lacuna %s nao gravada no banco", lacuna.impressao, exc_info=True)


def _enviar_ao_banco(lacuna) -> None:
    global _executor
    engine = _engine()  # no thread do script: get_engine e cache do Streamlit
    if engine is None:
        return
    with _trava:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lacunas")
        _envios.append(_executor.submit(_gravar_banco_seguro, engine, lacuna))
        _envios[:] = [f for f in _envios if not f.done()]


def registrar_lacuna(fonte: str, codigo: str | None, mensagem: str, *,
                     modulo: str | None = None, entidade: str | None = None,
                     contexto: dict | None = None) -> None:
    """Registra uma lacuna declarada pelo app. Nunca levanta.

    ``fonte``: motor | llm | tela | excecao. ``codigo``: identificador estavel
    escolhido por quem chama (``"fii.vpa_sem_historico"``); vazio faz a mensagem
    normalizada virar a chave. ``contexto`` so aceita tabela, coluna, periodo,
    n_faltantes e versao_motor.
    """
    try:
        alvo = destino.escolher_destino()
        if alvo == "desligado":
            return
        lacuna = construir_lacuna(fonte=fonte, codigo=codigo, mensagem=mensagem,
                                  modulo=modulo or _modulo_chamador(),
                                  entidade=entidade, contexto=contexto)
        if _ja_visto(lacuna.impressao):
            return
        if alvo == "local":
            destino.gravar_local(lacuna)
        else:
            _enviar_ao_banco(lacuna)
    except Exception:  # noqa: BLE001 - registrar lacuna nao pode virar lacuna
        _log.warning("falha ao registrar lacuna (%s, %s)", fonte, codigo, exc_info=True)


def _limpar_vistas() -> None:
    """So para testes: esquece o que o processo ja registrou."""
    with _trava:
        _vistas_processo.clear()


def _aguardar_envios(timeout: float = 5.0) -> None:
    """So para testes: espera os UPSERTs pendentes da thread."""
    with _trava:
        pendentes = list(_envios)
    for futuro in pendentes:
        futuro.result(timeout=timeout)

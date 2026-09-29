"""
core/lacunas/captura.py
Adaptadores que transformam o que o app ja declara em lacunas registradas.

- ``registrar_excecao``: excecao que chegou a fronteira de uma rota. A chave e o
  TIPO da excecao mais o frame mais interno DESTE repositorio; a mensagem da
  excecao nunca sai daqui (pode carregar SQL, URL, valor do usuario).
- ``registrar_limitacoes``: as ``limitacoes``/``alertas`` que motores e blocos
  ja entregam prontos. Chamado no RENDER, nao no motor: o motor roda tambem em
  backtest e em rotina noturna, e cada execucao dessas viraria ruido na fila.

Ambos herdam a garantia de ``registrar_lacuna``: nunca levantam.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from core.lacunas.evento import normalizar
from core.lacunas.registro import registrar_lacuna

_log = logging.getLogger(__name__)

_FRAME = re.compile(r"^(?P<arquivo>.+?):\d+ em (?P<funcao>.+?)\(\)$")


def _modulo_da_excecao(exc: BaseException) -> str:
    from core.erro_diagnostico import frames_do_projeto

    frames = frames_do_projeto(exc)
    if not frames:
        return "fora do projeto"
    casado = _FRAME.match(frames[-1])
    if casado is None:
        return frames[-1]
    return f"{casado['arquivo']}:{casado['funcao']}"


def registrar_excecao(exc: BaseException, *, rota: str | None = None) -> None:
    """Registra uma excecao capturada na fronteira de uma rota. Nunca levanta."""
    try:
        from core.erro_diagnostico import identidade_do_erro

        mensagem = identidade_do_erro(exc)
        if rota:
            mensagem = f"{mensagem} (rota {rota})"
        registrar_lacuna("excecao", type(exc).__name__, mensagem,
                         modulo=_modulo_da_excecao(exc))
    except Exception:  # noqa: BLE001 - registrar lacuna nao pode virar lacuna
        _log.warning("falha ao registrar excecao como lacuna", exc_info=True)


def _textos(objeto) -> list[str]:
    if objeto is None:
        return []
    if isinstance(objeto, str):
        return [objeto]
    atributos = [getattr(objeto, nome, None) for nome in ("limitacoes", "alertas")]
    if any(a is not None for a in atributos):
        return [t for a in atributos if a for t in _textos(a)]
    if isinstance(objeto, Iterable):
        return [str(t) for t in objeto if t]
    return []


def _codigo_por_causa(texto: str, simbolos: set[str]) -> str | None:
    """``"KNSL: sem X"`` com KNSL em ``simbolos`` -> ``"limitacao:sem x"``."""
    prefixo, sep, resto = texto.partition(":")
    if not sep or prefixo.strip().upper() not in simbolos or not resto.strip():
        return None
    return "limitacao:" + normalizar(resto)


def registrar_limitacoes(objeto, *, modulo: str, entidade: str | None = None,
                         fonte: str = "motor", simbolos: Iterable[str] = ()) -> None:
    """Registra cada limitacao declarada por ``objeto``. Nunca levanta.

    ``objeto``: algo com ``.limitacoes`` e/ou ``.alertas``, uma sequencia de
    textos ou um texto. Sem codigo estavel, a chave de cada lacuna e o texto
    normalizado (datas e numeros viram marcadores), entao "faltam 12 meses" e
    "faltam 13 meses" continuam sendo a mesma lacuna.

    ``simbolos``: ativos que o motor pode citar como prefixo (``"KNSL: sem
    comparacao macro"``). Com eles, a chave vira o texto SEM o simbolo, e 30
    ativos com a mesma causa sao uma lacuna so -- a mensagem guarda o ultimo
    exemplo. Prefixo fora da lista nao e tratado ("VPA: ausente" e "DY:
    ausente" sao causas diferentes). Com ``entidade`` explicita, nada muda.
    """
    try:
        conhecidos = {str(s).strip().upper() for s in simbolos if s} if entidade is None else set()
        for texto in dict.fromkeys(t.strip() for t in _textos(objeto) if t and t.strip()):
            codigo = _codigo_por_causa(texto, conhecidos) if conhecidos else None
            registrar_lacuna(fonte, codigo, texto, modulo=modulo, entidade=entidade)
    except Exception:  # noqa: BLE001 - registrar lacuna nao pode virar lacuna
        _log.warning("falha ao registrar limitacoes de %s", modulo, exc_info=True)

"""
core/erro_diagnostico.py
Identidade publicavel de uma excecao.

O achado A-013 tirou a excecao crua da tela porque ela levava junto driver,
host, porta e credencial — a mensagem de erro do SQLAlchemy carrega a URL de
conexao inteira. A correcao daquele achado, porem, deixou a tela sem NENHUMA
pista: em producao o traceback vai so para o log da Streamlit Cloud, e quem
usa o app ve "tente novamente em instantes" sem ter o que reportar.

Este modulo devolve o meio-termo: o TIPO da excecao e os frames que pertencem
a ESTE repositorio (caminho relativo, linha, funcao). Nada disso e segredo —
sao arquivos versionados em publico. A mensagem da excecao nunca sai daqui,
porque e nela que moram credencial e endereco.

    memoria: defeito-silencioso-vs-erro
"""
from __future__ import annotations

import traceback
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent

MAX_FRAMES = 5


def _do_projeto(caminho: str) -> bool:
    """True quando o arquivo do frame esta dentro deste repositorio."""
    try:
        Path(caminho).resolve().relative_to(_RAIZ)
    except (ValueError, OSError):
        return False
    return True


def frames_do_projeto(exc: BaseException) -> list[str]:
    """Frames do traceback que vivem neste repositorio, do mais externo ao mais interno.

    Frames de biblioteca (site-packages, stdlib) ficam de fora: eles nao dizem
    o que consertar aqui e alongam a lista.
    """
    achados: list[str] = []
    for quadro in traceback.extract_tb(exc.__traceback__):
        if not _do_projeto(quadro.filename):
            continue
        rel = Path(quadro.filename).resolve().relative_to(_RAIZ).as_posix()
        achados.append(f"{rel}:{quadro.lineno} em {quadro.name}()")
    return achados[-MAX_FRAMES:]


def identidade_do_erro(exc: BaseException) -> str:
    """Uma linha que identifica o defeito sem revelar a mensagem da excecao.

    Formato: ``TipoDoErro · caminho/arquivo.py:123``. Quando nenhum frame e
    deste repositorio (falha inteiramente dentro de uma dependencia), a origem
    vira ``fora do projeto`` — dizer isso e mais util que mentir um local.
    """
    tipo = type(exc).__name__
    frames = frames_do_projeto(exc)
    if not frames:
        return f"{tipo} · fora do projeto"
    return f"{tipo} · {frames[-1].split(' em ')[0]}"


def relatorio_tecnico(exc: BaseException) -> str:
    """Bloco curto para o expander da tela: identidade + caminho ate ela.

    Tudo aqui e derivado de nome de tipo e de caminho de arquivo versionado.
    Nenhum valor de dado, nenhuma mensagem de excecao.
    """
    linhas = [identidade_do_erro(exc)]
    frames = frames_do_projeto(exc)
    if frames:
        linhas.append("")
        linhas.extend(f"  {f}" for f in frames)
    causa = exc.__cause__ or exc.__context__
    if causa is not None:
        linhas.append("")
        linhas.append(f"causada por {identidade_do_erro(causa)}")
    return "\n".join(linhas)

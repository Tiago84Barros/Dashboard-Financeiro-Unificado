"""Registro de formatos de extrato da empresa.

Cada formato é um :class:`Leitor`: um nome, como reconhecer o arquivo e como
lê-lo para :class:`core.pj.ofx.ExtratoLido`. Hoje existe só OFX, que é o que
o banco Cora exporta. Um formato novo (CSV de outro banco, PDF) entra com uma
linha em :data:`LEITORES`; tela, conciliação e importação não mudam.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.pj.ofx import ExtratoLido, ler_ofx, parece_ofx


@dataclass(frozen=True)
class Leitor:
    formato: str
    extensoes: tuple[str, ...]
    reconhece: Callable[[bytes, str], bool]
    ler: Callable[[bytes, str], ExtratoLido]


LEITORES: tuple[Leitor, ...] = (
    Leitor("OFX", ("ofx",), lambda dados, _nome: parece_ofx(dados), ler_ofx),
)


def extensoes_aceitas() -> list[str]:
    return sorted({e for leitor in LEITORES for e in leitor.extensoes})


def ler_extrato(conteudo: bytes, nome_arquivo: str = "") -> ExtratoLido:
    """Escolhe o leitor pelo conteúdo (a extensão só desempata) e lê."""
    for leitor in LEITORES:
        if leitor.reconhece(conteudo, nome_arquivo):
            return leitor.ler(conteudo, nome_arquivo)
    ext = nome_arquivo.rsplit(".", 1)[-1].lower() if "." in nome_arquivo else ""
    for leitor in LEITORES:
        if ext in leitor.extensoes:
            return leitor.ler(conteudo, nome_arquivo)
    formatos = ", ".join(leitor.formato for leitor in LEITORES)
    return ExtratoLido(formato="desconhecido", movimentos=[], avisos=[
        f"Formato de extrato não reconhecido. Aceitos hoje: {formatos}."])

"""
core/cenario/divergencia.py
Sinais de que o cenário salvo pode precisar de revisão. Puro.

Um sinal só nasce quando um dado de referência é MAIS NOVO que a última
revisão do item e o número dele se afasta do número da premissa além da
tolerância. Referência mais antiga que a premissa não a contradiz: o usuário
já sabia dela quando escreveu.

O sinal é aviso, nunca alteração. Quem muda o cenário é o usuário.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from core.cenario import modelo as mod


@dataclass(frozen=True)
class Referencia:
    valor: float
    data: dt.date                # período de referência do dado
    fonte: str                   # de onde veio, dito por extenso
    unidade: str                 # "% a.a.", "R$ por US$"...


@dataclass(frozen=True)
class Sinal:
    chave: str
    premissa: float
    referencia: Referencia
    texto: str


# chave → (tolerância, relativa?) — pp para taxas, fração para câmbio
TOLERANCIA: dict[str, tuple[float, bool]] = {
    "interest_rate": (0.25, False),
    "inflation": (0.5, False),
    "fx": (0.05, True),
}

_NUMERO = re.compile(r"-?\d+(?:[.,]\d+)?")


def primeiro_numero(texto: str) -> float | None:
    """O primeiro número do valor digitado ("15% a.a." → 15.0)."""
    achado = _NUMERO.search(texto or "")
    if not achado:
        return None
    try:
        return float(achado.group().replace(",", "."))
    except ValueError:
        return None


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def sinais(c: mod.Cenario | None, referencias: dict[str, Referencia]
           ) -> tuple[Sinal, ...]:
    if c is None:
        return ()
    saida = []
    for chave, (tol, relativa) in TOLERANCIA.items():
        ref = referencias.get(chave)
        it = c.item(chave)
        if ref is None or not it.preenchido:
            continue
        revisto = mod.data_iso(it.last_updated)
        if revisto is not None and ref.data <= revisto:
            continue
        premissa = primeiro_numero(it.current_value)
        if premissa is None:
            continue
        diferenca = abs(ref.valor - premissa)
        limite = tol * abs(premissa) if relativa else tol
        if diferenca <= limite:
            continue
        saida.append(Sinal(chave, premissa, ref, (
            f"{mod.ROTULO[chave]}: a premissa diz {_fmt(premissa)}; "
            f"{ref.fonte} mostra {_fmt(ref.valor)} {ref.unidade} em "
            f"{ref.data.strftime('%d/%m/%Y')}, depois da última revisão do "
            f"item ({it.last_updated or 'sem data'}). {mod.FRASE_REVISAO}")))
    return tuple(saida)

"""
core/tesouro_nomes.py
=====================
Traduz o codigo da divida publica para o nome que o Tesouro Direto usa.

Por que existe
--------------
O extrato "Posicao Detalhada" da B3 publica o titulo pelo codigo da divida --
"LFT mar/2031", "NTNB PRINC ago/2032", "LTN jan/2032". O site do Tesouro
Direto, o app do Tesouro e o investidor chamam os MESMOS papeis de "Tesouro
Selic 2031", "Tesouro IPCA+ 2032" e "Tesouro Prefixado 2032". Mostrar o
codigo na tela obriga quem le a traduzir de cabeca.

Por que nao dava para derivar do ticker
---------------------------------------
`_tesouro_ticker` colapsa "NTNB PRINC ago/2032" (principal, sem cupom) e
"NTNB ago/2032" (com juros semestrais) no mesmo `TIPCA2032` -- e isso esta
certo para o ticker, porque o indexador e o vencimento sao os mesmos. Mas sao
titulos diferentes, com fluxo de caixa diferente. O nome amigavel so pode sair
do TEXTO da fonte, antes de essa distincao se perder.

Regra de ouro
-------------
Familia desconhecida devolve o texto original, inalterado. Um rotulo amigavel
errado e pior que um codigo feio: o codigo quem le desconfia, o rotulo nao.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

__all__ = ["nome_amigavel", "familia"]


def _norm(texto: str) -> str:
    """Maiusculas, sem acento, sem pontuacao, espacos colapsados."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^A-Z0-9]+", " ", sem_acento.upper()).strip()


# Ordem importa: "NTNB PRINC" tem que ser testado antes de "NTNB", e "NTNF"
# antes de qualquer prefixo mais curto. Cada entrada e (regex, familia).
_FAMILIAS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^LFT\b"),                 "Tesouro Selic"),
    (re.compile(r"^NTN\s*B\s+PRINC\b"),     "Tesouro IPCA+"),
    (re.compile(r"^NTN\s*B\b"),             "Tesouro IPCA+ com Juros Semestrais"),
    (re.compile(r"^NTN\s*F\b"),             "Tesouro Prefixado com Juros Semestrais"),
    (re.compile(r"^LTN\b"),                 "Tesouro Prefixado"),
)

_ANO = re.compile(r"(\d{4})")


def familia(texto: Any) -> str | None:
    """Familia do titulo, no vocabulario do Tesouro Direto, ou None.

    None significa "nao reconheco" -- e quem chama devolve o texto da fonte,
    em vez de escolher uma familia por proximidade.
    """
    bruto = str(texto or "").strip()
    if not bruto:
        return None
    alvo = _norm(bruto)
    if alvo.startswith("TESOURO"):
        # Ja veio no vocabulario publico (o Consolidado da XP entrega assim).
        return None
    for padrao, nome in _FAMILIAS:
        if padrao.match(alvo):
            return nome
    return None


def _ano(texto: str, vencimento: Any) -> str:
    """Ano do vencimento: da coluna Vencimento, senao do proprio texto."""
    for fonte in (vencimento, texto):
        if fonte in (None, ""):
            continue
        achado = _ANO.search(str(fonte))
        if achado:
            return achado.group(1)
    return ""


def nome_amigavel(texto: Any, vencimento: Any = None) -> str:
    """Nome publico do titulo ("Tesouro Selic 2031"), ou o texto original.

    `vencimento` e opcional e serve so para o ano: "LFT" sozinho, sem ano em
    lugar nenhum, sai como "Tesouro Selic" -- sem ano inventado.
    """
    bruto = str(texto or "").strip()
    if not bruto:
        return ""
    base = familia(bruto)
    if base is None:
        return bruto
    ano = _ano(bruto, vencimento)
    return f"{base} {ano}".strip()

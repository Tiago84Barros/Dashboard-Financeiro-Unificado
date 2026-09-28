"""
core/lacunas/evento.py
Uma lacuna declarada pelo app, pronta para ser gravada.

"Lacuna" e tudo o que o app admite nao saber: limitacao devolvida por um motor,
aviso de tela que diz que falta dado, bloco <lacunas> do LLM, excecao na
fronteira de uma rota. Este modulo so monta o evento -- nao grava nada.

Duas decisoes carregam o modulo:

* A IMPRESSAO DIGITAL identifica o fato, nao o texto. Ela usa fonte, modulo,
  codigo e entidade; a mensagem fica de fora sempre que ha codigo, porque o
  texto traz numeros que mudam a cada execucao e cada variacao viraria uma
  lacuna nova. Sem codigo (LLM, aviso migrado sem codigo) a mensagem entra na
  chave NORMALIZADA: datas e numeros viram marcadores.
      memoria: chave-de-texto-nao-e-identidade-do-fato

* O evento e SANITIZADO na montagem, antes de qualquer destino. A mensagem sai
  sem URL (e com ela a string de conexao), sem e-mail e sem valor monetario da
  carteira; o contexto so aceita chaves de uma lista branca. Um log lido por
  agentes de IA e publicado em PR nao pode carregar o que a tela escondeu.
      ver tambem: core/erro_diagnostico.py
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

FONTES = ("motor", "llm", "tela", "excecao")

#: Unicas chaves de contexto aceitas. O resto e descartado em silencio.
CHAVES_CONTEXTO = frozenset({"tabela", "coluna", "periodo", "n_faltantes", "versao_motor"})

MAX_MENSAGEM = 500
MAX_VALOR_CONTEXTO = 100

_URL = re.compile(r"\b[a-z][a-z0-9+.\-]*://\S+", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_MOEDA = re.compile(r"(?:R|US)?\$\s?-?\d[\d.,]*", re.IGNORECASE)
_DATA = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
_ESPACOS = re.compile(r"\s+")


def sanitizar(texto: str) -> str:
    """Remove URL, e-mail e valor monetario. A ordem importa: a URL vem antes
    porque ``postgresql://u:s@host`` tambem casaria com o padrao de e-mail."""
    texto = _URL.sub("<url>", texto or "")
    texto = _EMAIL.sub("<email>", texto)
    return _MOEDA.sub("<valor>", texto)


def normalizar(texto: str) -> str:
    """Forma canonica de uma mensagem, para quando ela precisa ser chave."""
    texto = sanitizar(texto).lower()
    texto = _DATA.sub("<data>", texto)
    texto = _NUMERO.sub("#", texto)
    return _ESPACOS.sub(" ", texto).strip()


def _entidade(valor: str | None) -> str:
    return (valor or "").strip().upper()


def impressao_digital(fonte: str, modulo: str | None, codigo: str | None,
                      entidade: str | None, mensagem: str | None) -> str:
    """sha1 hex (40 caracteres) que identifica a lacuna entre execucoes."""
    chave = (codigo or "").strip() or normalizar(mensagem or "")
    partes = [fonte, (modulo or "").strip(), chave, _entidade(entidade)]
    return hashlib.sha1("|".join(partes).encode("utf-8")).hexdigest()


def filtrar_contexto(contexto: dict | None) -> dict:
    """Mantem so as chaves da lista branca, com valores primitivos e curtos."""
    saida: dict = {}
    for chave, valor in (contexto or {}).items():
        if chave not in CHAVES_CONTEXTO:
            continue
        if valor is None or isinstance(valor, (bool, int, float)):
            saida[chave] = valor
        else:
            saida[chave] = sanitizar(str(valor))[:MAX_VALOR_CONTEXTO]
    return saida


@dataclass(frozen=True)
class Lacuna:
    ts: str
    impressao: str
    fonte: str
    modulo: str
    codigo: str
    entidade: str
    mensagem: str
    contexto: dict = field(default_factory=dict)


def construir_lacuna(*, fonte: str, codigo: str | None, mensagem: str,
                     modulo: str, entidade: str | None = None,
                     contexto: dict | None = None,
                     agora: datetime | None = None) -> Lacuna:
    """Monta o evento. Levanta ``ValueError`` para fonte desconhecida -- quem
    chama pela porta publica (``registrar_lacuna``) engole o erro."""
    if fonte not in FONTES:
        raise ValueError(f"fonte de lacuna desconhecida: {fonte!r}")
    codigo = (codigo or "").strip()
    modulo = (modulo or "").strip()
    entidade_norm = _entidade(entidade)
    msg = sanitizar(mensagem or "")[:MAX_MENSAGEM]
    agora = agora or datetime.now(timezone.utc)
    return Lacuna(
        ts=agora.isoformat(),
        impressao=impressao_digital(fonte, modulo, codigo, entidade_norm, msg),
        fonte=fonte,
        modulo=modulo,
        codigo=codigo,
        entidade=entidade_norm,
        mensagem=msg,
        contexto=filtrar_contexto(contexto),
    )

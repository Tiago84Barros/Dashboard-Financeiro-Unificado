"""
core/lacunas/llm.py
Lacunas que a propria LLM declara, num bloco ``<lacunas>`` no fim da resposta.

O bloco e pedido pela ``INSTRUCAO_LACUNAS``, que o ponto unico
``core.llm_b3._chat_complete`` acrescenta ao system prompt das chamadas de
texto livre. A resposta volta SEM o bloco: quem exibe, grava no historico ou
confere aterramento nunca ve a marcacao.

Uma lacuna por linha, em JSON: ``{"codigo": "...", "mensagem": "...",
"entidade": "..."}``. Linha que nao e JSON valido nao se perde: vira a lacuna
``llm.lacuna_malformada`` -- o defeito passa a ser o formato, e ele tambem vai
para a fila.
"""
from __future__ import annotations

import json
import logging
import re

from core.lacunas.registro import registrar_lacuna

_log = logging.getLogger(__name__)

MAX_POR_RESPOSTA = 10
CODIGO_MALFORMADA = "llm.lacuna_malformada"

INSTRUCAO_LACUNAS = (
    "LACUNAS: se faltou dado, fonte ou informação para responder de forma "
    "mais completa, termine a resposta com um bloco <lacunas> ... </lacunas>, "
    "uma lacuna por linha em JSON: "
    '{"codigo": "area.dado_ausente", "mensagem": "o que faltou e por que '
    'importaria", "entidade": "TICKER ou vazio"}. '
    "Liste só o que realmente faltou, no máximo 5. Sem lacuna, não escreva o "
    "bloco. O bloco é removido antes de a pessoa ler a resposta: não o cite no "
    "texto."
)

# Bloco fechado, ou aberto e cortado no fim da resposta (limite de tokens).
_BLOCO = re.compile(r"<lacunas>(.*?)(?:</lacunas>|\Z)", re.IGNORECASE | re.DOTALL)
_CODIGO_OK = re.compile(r"[^a-z0-9_.]+")


def _codigo(bruto: object) -> str | None:
    """Normaliza o codigo que a LLM inventou: minusculo, ``[a-z0-9_.]``, 60
    caracteres, prefixo ``llm.``. Sem isso a mesma lacuna ganharia uma impressao
    digital por grafia."""
    texto = _CODIGO_OK.sub("_", str(bruto or "").strip().lower()).strip("_.")
    if not texto:
        return None
    if not texto.startswith("llm."):
        texto = f"llm.{texto}"
    return texto[:60]


def _itens(corpo: str) -> list[dict]:
    corpo = corpo.strip()
    if not corpo:
        return []
    # Aceita tambem uma lista JSON unica, que alguns modelos preferem.
    if corpo.startswith("["):
        try:
            lista = json.loads(corpo)
            if isinstance(lista, list):
                return [i if isinstance(i, dict) else {"_malformada": str(i)} for i in lista]
        except ValueError:
            pass
    itens = []
    for linha in corpo.splitlines():
        linha = linha.strip().strip(",").strip()
        if not linha or linha.startswith("```"):
            continue
        try:
            item = json.loads(linha)
        except ValueError:
            item = None
        itens.append(item if isinstance(item, dict) else {"_malformada": linha})
    return itens


def extrair_lacunas(texto: str) -> tuple[str, list[dict]]:
    """Separa a resposta do bloco. Devolve ``(texto_sem_bloco, itens)``."""
    if not texto or "<lacunas>" not in texto.lower():
        return texto, []
    itens: list[dict] = []
    for casado in _BLOCO.finditer(texto):
        itens.extend(_itens(casado.group(1)))
    limpo = _BLOCO.sub("", texto).rstrip()
    return limpo, itens[:MAX_POR_RESPOSTA]


def processar_resposta(texto: str, *, modulo: str) -> str:
    """Tira o bloco da resposta e registra cada lacuna. Nunca levanta: se algo
    falhar, devolve o texto recebido."""
    try:
        limpo, itens = extrair_lacunas(texto)
    except Exception:  # noqa: BLE001 - lacuna nunca derruba a resposta
        _log.warning("falha ao extrair lacunas da resposta da LLM", exc_info=True)
        return texto
    for item in itens:
        try:
            if "_malformada" in item:
                registrar_lacuna("llm", CODIGO_MALFORMADA,
                                 f"linha fora do formato: {item['_malformada']}",
                                 modulo=modulo)
                continue
            mensagem = str(item.get("mensagem") or "").strip()
            if not mensagem:
                continue
            registrar_lacuna("llm", _codigo(item.get("codigo")), mensagem,
                             modulo=modulo, entidade=(item.get("entidade") or None))
        except Exception:  # noqa: BLE001
            _log.warning("falha ao registrar lacuna da LLM", exc_info=True)
    return limpo

"""
core/llm_anthropic.py
Elo Claude (Anthropic) da cadeia de LLM de ``core.llm_b3``.

Os outros elos falam o protocolo ``chat.completions`` da OpenAI (o Gemini e o
OpenRouter aceitam o mesmo SDK); a Anthropic não. Este módulo traduz: as
mensagens viram ``system`` + ``messages`` da Messages API, as ferramentas viram
``input_schema``, e a resposta volta como texto. Quem entra na cadeia, e
quando, é decidido em ``core.llm_b3._provider_chain`` -- aqui só se fala com a
API.

Três diferenças do protocolo da OpenAI que o resto do app não vê:

- não há ``response_format``: o modo JSON vira instrução no system prompt e
  conferência do texto (JSON que não parseia é falha do elo, e a cadeia segue);
- o Sonnet 5.5 recusa ``temperature`` (400): a temperatura pedida pelo chamador
  não chega aqui;
- o pensamento adaptativo vem ligado, e o laço de ferramentas devolve o
  ``content`` da resposta intacto, como a API exige.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence

logger = logging.getLogger(__name__)

#: Teto de saída, pensamento incluído. A chamada é por streaming, então teto
#: alto não esbarra no timeout de leitura; paga-se só o que for gerado.
MAX_TOKENS = 32000

#: Abertura no lugar de histórico que começa pela assistente: a API exige que a
#: primeira mensagem seja do usuário.
_ABERTURA = "(início da conversa)"

#: Fecho de histórico que termina na assistente: no protocolo da OpenAI isso é
#: só contexto, mas o Sonnet 5.5 lê como resposta pré-preenchida e recusa (400).
_CONTINUAR = "(continue)"

_INSTRUCAO_JSON = (
    "FORMATO DA RESPOSTA: responda só com um único objeto JSON válido -- sem "
    "texto antes ou depois e sem cercas de código."
)

_CERCA = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def separar(messages: Sequence[dict]) -> tuple[str, list[dict]]:
    """``(system, mensagens)`` no formato da Messages API.

    Todo ``system`` vira um só, na ordem em que veio: a API não aceita system
    no meio da conversa. Mensagem vazia sai (a API recusa bloco de texto
    vazio), e papel desconhecido também. A conversa começa e termina no
    usuário.
    """
    sistemas: list[str] = []
    conversa: list[dict] = []
    for m in messages:
        papel = m.get("role")
        texto = m.get("content")
        texto = texto if isinstance(texto, str) else ("" if texto is None else str(texto))
        if not texto.strip():
            continue
        if papel == "system":
            sistemas.append(texto)
        elif papel in ("user", "assistant"):
            conversa.append({"role": papel, "content": texto})
    if not conversa or conversa[0]["role"] != "user":
        conversa.insert(0, {"role": "user", "content": _ABERTURA})
    if conversa[-1]["role"] != "user":
        conversa.append({"role": "user", "content": _CONTINUAR})
    return "\n\n".join(sistemas), conversa


def extrair_json(texto: str) -> str:
    """O objeto JSON da resposta, conferido; ``ValueError`` se não houver."""
    limpo = _CERCA.sub("", texto or "").strip()
    inicio, fim = limpo.find("{"), limpo.rfind("}")
    if inicio < 0 or fim <= inicio:
        raise ValueError("resposta sem objeto JSON")
    trecho = limpo[inicio:fim + 1]
    json.loads(trecho)
    return trecho


def _texto(resp) -> str:
    return "".join(getattr(b, "text", "") for b in resp.content
                   if getattr(b, "type", None) == "text")


def _registrar_uso(resp) -> None:
    """Tokens da chamada no log: o crédito é finito e o custo tem de ser legível."""
    uso = getattr(resp, "usage", None)
    if uso is None:
        return
    logger.info(
        "claude %s: entrada %s (cache lido %s, gravado %s), saída %s, parada %s",
        getattr(resp, "model", "?"), getattr(uso, "input_tokens", "?"),
        getattr(uso, "cache_read_input_tokens", 0) or 0,
        getattr(uso, "cache_creation_input_tokens", 0) or 0,
        getattr(uso, "output_tokens", "?"), getattr(resp, "stop_reason", "?"))


def _pedir(client, modelo: str, system: str, mensagens: list[dict], esforco: str,
           ferramentas: list[dict] | None = None, sem_ferramenta: bool = False):
    """Uma ida à API, por streaming; devolve a mensagem final.

    ``cache_control`` no topo liga o cache automático: no chat, a rodada
    seguinte relê o contexto de mercado do cache a um décimo do preço.
    """
    pedido: dict[str, Any] = {
        "model": modelo, "max_tokens": MAX_TOKENS, "messages": mensagens,
        "output_config": {"effort": esforco},
        "cache_control": {"type": "ephemeral"},
    }
    if system:
        pedido["system"] = system
    if ferramentas:
        pedido["tools"] = ferramentas
        if sem_ferramenta:
            pedido["tool_choice"] = {"type": "none"}
    with client.messages.stream(**pedido) as stream:
        resp = stream.get_final_message()
    _registrar_uso(resp)
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude recusou responder (stop_reason=refusal)")
    if resp.stop_reason == "max_tokens":
        logger.warning("claude %s: resposta cortada no teto de %d tokens",
                       modelo, MAX_TOKENS)
    return resp


def completar(client, modelo: str, messages: Sequence[dict], *,
              json_mode: bool = False, ferramentas: Sequence | None = None,
              esforco: str = "medium", usadas: list | None = None,
              max_rodadas: int = 3, max_por_rodada: int = 4) -> tuple[str, str]:
    """Responde ``messages`` (formato OpenAI) pelo Claude: ``(texto, modelo)``.

    ``ferramentas`` liga o function calling com o mesmo teto de rodadas e de
    chamadas por rodada dos outros elos; cada chamada executada entra em
    ``usadas`` como ``{"nome", "argumentos", "chars"}``. Esgotadas as rodadas,
    a última sai com ``tool_choice=none``.

    Levanta em resposta vazia, recusa ou JSON que não parseia: para a cadeia,
    é falha deste elo como qualquer outra.
    """
    system, conversa = separar(messages)
    if json_mode:
        system = (system + "\n\n" + _INSTRUCAO_JSON) if system else _INSTRUCAO_JSON

    if not ferramentas:
        resp = _pedir(client, modelo, system, conversa, esforco)
    else:
        from core.llm_ferramentas import REGRA_FERRAMENTAS, executar

        system = (system + "\n\n" + REGRA_FERRAMENTAS) if system else REGRA_FERRAMENTAS
        esquemas = [{"name": f.nome, "description": f.descricao,
                     "input_schema": dict(f.parametros)} for f in ferramentas]
        usadas = usadas if usadas is not None else []
        for rodada in range(max_rodadas + 1):
            final = rodada == max_rodadas
            resp = _pedir(client, modelo, system, conversa, esforco,
                          ferramentas=esquemas, sem_ferramenta=final)
            chamadas = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not chamadas or final:
                break
            # O ``content`` volta intacto: o bloco de pensamento só vale na
            # conversa que o produziu, e a API recusa a rodada se ele mudar.
            conversa.append({"role": "assistant", "content": resp.content})
            resultados = []
            for i, chamada in enumerate(chamadas):
                if i >= max_por_rodada:
                    resultado = (f"Não executada: teto de {max_por_rodada} "
                                 "chamadas por rodada.")
                else:
                    resultado = executar(chamada.name, chamada.input, ferramentas)
                    usadas.append({"nome": chamada.name,
                                   "argumentos": json.dumps(chamada.input,
                                                            ensure_ascii=False),
                                   "chars": len(resultado)})
                resultados.append({"type": "tool_result", "tool_use_id": chamada.id,
                                   "content": resultado or "(sem resultado)"})
            conversa.append({"role": "user", "content": resultados})

    texto = _texto(resp)
    if not texto.strip():
        raise ValueError("resposta vazia do Claude")
    if json_mode:
        texto = extrair_json(texto)
    return texto, getattr(resp, "model", None) or modelo

"""
core/jev.py
Cliente mínimo do Jev (TypeSafe AI) — modelo de DECISÃO, não de texto.

O Jev recebe evidência (`state`) e perguntas tipadas, e devolve para cada
pergunta um valor restrito ao formato pedido, com probabilidades e confiança.
Não gera prosa.

Duas portas, mesmo contrato ({model, state, questions} -> {answers}):
  - TypeSafe direto: POST /v1/systemone, modelo "jev-latest";
  - OpenRouter (beta, alpha API): POST /api/alpha/decisions, modelo
    "typesafe/jev-1.13", cobrado na conta do OpenRouter.
`destino_jev()` escolhe: a chave da TypeSafe ganha; sem ela, a do OpenRouter.

Sem Streamlit e sem banco. A chave nunca vai para log, exceção ou retorno: a
mensagem de erro cita só o status HTTP.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODELO_PADRAO = "jev-latest"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"


class JevErro(RuntimeError):
    """Falha na chamada ao Jev (rede, status HTTP ou resposta malformada)."""


@dataclass(frozen=True)
class RespostaJev:
    answers: dict[str, dict[str, Any]]
    modelo: str
    latencia_s: float
    tokens_entrada: int = 0
    custo_usd: float | None = None     # só o OpenRouter informa
    bruto: dict[str, Any] = field(repr=False, default_factory=dict)


@dataclass(frozen=True)
class DestinoJev:
    via: str          # "typesafe" | "openrouter"
    url: str
    api_key: str = field(repr=False)
    modelo: str


def destino_jev(cfg: Any) -> DestinoJev | None:
    """Porta de saída a partir das configurações; None se não houver chave."""
    chave_ts = getattr(cfg, "TYPESAFE_API_KEY", "") or ""
    if chave_ts:
        return DestinoJev("typesafe", JEV_URL, chave_ts,
                          getattr(cfg, "TYPESAFE_MODEL", "") or JEV_MODELO_PADRAO)
    chave_or = getattr(cfg, "OPENROUTER_API_KEY", "") or ""
    if chave_or:
        return DestinoJev("openrouter", OPENROUTER_DECISIONS_URL, chave_or,
                          getattr(cfg, "OPENROUTER_JEV_MODEL", "") or "typesafe/jev-1.13")
    return None


def choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    """Pergunta de escolha única (até 255 opções)."""
    if not criteria:
        raise ValueError("choice exige ao menos uma opção")
    if len(criteria) > 255:
        raise ValueError("choice aceita no máximo 255 opções")
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def system_one(
    state: Any,
    questions: dict[str, dict[str, Any]],
    *,
    api_key: str,
    model: str = JEV_MODELO_PADRAO,
    url: str = JEV_URL,
    timeout_s: float = 15.0,
    http_post: Callable[..., Any] | None = None,
) -> RespostaJev:
    """Faz uma chamada ao Jev e devolve as respostas por chave de pergunta.

    `http_post` existe para teste; o padrão é `requests.post`.
    """
    if not api_key:
        raise JevErro("chave do Jev não configurada")
    if http_post is None:
        import requests
        http_post = requests.post

    corpo = {"state": state, "questions": questions, "model": model}
    t0 = time.perf_counter()
    try:
        resp = http_post(
            url,
            json=corpo,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )
    except Exception as exc:  # rede: não ecoa a requisição (tem a chave no header)
        raise JevErro(f"falha de rede ao chamar o Jev: {type(exc).__name__}") from None
    latencia = time.perf_counter() - t0

    status = getattr(resp, "status_code", None)
    if status != 200:
        raise JevErro(f"Jev respondeu HTTP {status}")
    try:
        dados = resp.json()
    except ValueError:
        raise JevErro("Jev devolveu corpo que não é JSON") from None
    answers = dados.get("answers") if isinstance(dados, dict) else None
    if not isinstance(answers, dict):
        raise JevErro("resposta do Jev sem o campo 'answers'")
    uso = dados.get("usage") if isinstance(dados.get("usage"), dict) else {}
    custo = uso.get("cost")
    return RespostaJev(
        answers=answers,
        modelo=str(dados.get("model") or model),
        latencia_s=latencia,
        tokens_entrada=int(uso.get("input_tokens") or uso.get("prompt_tokens") or 0),
        custo_usd=float(custo) if isinstance(custo, (int, float)) else None,
        bruto=dados,
    )

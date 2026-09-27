"""
core/inteligencia_ativos/leitura_llm.py
Chamada à LLM para a leitura de Portfolio Fit (camada de I/O).

Tudo o que é decisão — contexto, prompt, validação — mora em
``portfolio_fit.py``, que é puro. Aqui ficam só as duas coisas que tocam o
mundo: o bloco de contexto de mercado e a chamada ao provedor.
Coberto por tests/test_inteligencia_ativos_portfolio_fit.py (LLM simulada).
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable

from core.inteligencia_ativos import modelos as m
from core.inteligencia_ativos import portfolio_fit as pf

log = logging.getLogger(__name__)

_TEMPERATURA = 0.1


def contexto_mercado_do_ativo(analise: m.AnaliseAtivo) -> str:
    """Bloco de mercado (macro, conjuntura do setor, notícias gerais) só do
    ativo analisado. Falha vira texto que diz que falhou, nunca silêncio."""
    try:
        from core.contexto_mercado import ativos_por_classe, bloco_contexto_mercado
        i = analise.ativo
        ativos = ativos_por_classe([{"ticker": i.ticker, "classe": i.classe,
                                     "setor": i.setor, "moeda": i.moeda}])
        return bloco_contexto_mercado(ativos, max_itens_por_classe=6)
    except Exception as exc:  # noqa: BLE001 — a leitura continua sem o bloco
        log.warning("contexto de mercado indisponível: %s", exc)
        return ("=== CONTEXTO DE MERCADO ===\nFalha ao montar "
                f"({type(exc).__name__}). Não trate como ausência de notícias "
                "nem como conjuntura neutra.")


def _chamar_padrao(mensagens: list[dict]) -> str:
    from core.llm_b3 import _chat_complete
    return _chat_complete(mensagens, temperature=_TEMPERATURA, json_mode=True)


def _modelo_que_respondeu(chamar) -> str | None:
    """Para a auditoria. Com ``chamar`` de teste, não há provedor real."""
    if chamar is not None:
        return getattr(chamar, "modelo", None)
    from core.llm_b3 import ultimo_modelo
    return ultimo_modelo()


def gerar(analise: m.AnaliseAtivo, ctx: m.ContextoInvestidor, *,
          chamar: Callable[[list[dict]], str] | None = None,
          mercado: str | None = None) -> pf.Leitura:
    """Monta o contexto, chama a LLM e valida a resposta.

    ``chamar`` e ``mercado`` existem para teste: sem eles, usa o provedor
    configurado e monta o bloco de mercado de verdade."""
    if mercado is None:
        mercado = contexto_mercado_do_ativo(analise)
    contexto = pf.contexto(analise, ctx, cenario_mercado=mercado)
    regras = pf.fit_por_regras(analise, ctx)
    try:
        bruto = (chamar or _chamar_padrao)(pf.mensagens(contexto, mercado))
    except Exception as exc:  # noqa: BLE001 — provedor fora vira leitura rejeitada
        log.warning("leitura de portfolio fit falhou: %s", exc)
        return pf.falha(f"A LLM não respondeu ({type(exc).__name__}: {exc}).",
                        regras)
    dado = pf.ler_json(bruto)
    if dado is None:
        return pf.falha("A resposta da LLM não é um JSON válido.", regras)
    leitura = pf.validar(dado, contexto, pf.texto_ancora(contexto, mercado))
    return replace(leitura, modelo=_modelo_que_respondeu(chamar))

"""
core/inteligencia_ativos/leitura_llm.py
Chamada à LLM para a leitura de Portfolio Fit (camada de I/O).

Tudo o que é decisão — contexto, prompt, validação — mora em
``portfolio_fit.py``, que é puro. Aqui ficam só as duas coisas que tocam o
mundo: o bloco de contexto de mercado (com o detalhe do armazém do ativo)
e a chamada ao provedor.
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


def armazem_do_ativo(analise: m.AnaliseAtivo) -> tuple[str, dict]:
    """O detalhe do armazém do ativo em duas formas, de UMA leitura: o texto
    que vai colado ao bloco de mercado e os números como campos do contexto
    estruturado (``armazem_fatos``)."""
    from core.inteligencia_ativos import armazem_fatos

    bruto: dict = {}
    texto = detalhe_armazem_do_ativo(analise, captura=bruto)
    return texto, armazem_fatos.fatos(_classe(analise),
                                      str(analise.ativo.ticker or ""),
                                      bruto, aviso=texto)


def _classe(analise: m.AnaliseAtivo) -> str | None:
    try:
        from core.contexto_mercado import classe_conjuntura

        i = analise.ativo
        return classe_conjuntura({"classe": i.classe, "moeda": i.moeda})
    except Exception:  # noqa: BLE001 — sem classe, sem fatos
        return None


def detalhe_armazem_do_ativo(analise: m.AnaliseAtivo, *,
                             captura: dict | None = None) -> str:
    """Liquidez, preço, proventos, trimestres e score mês a mês do ativo,
    lidos do armazém (direto ou pelo túnel) -- o mesmo detalhe que os chats
    da carteira recebem. Vazio para classe sem leitor (Tesouro, renda fixa).

    Vai colado ao bloco de mercado porque é esse texto que entra no prompt
    E na âncora da validação: número do detalhe citado pela LLM precisa ser
    reconhecido como vindo do contexto, senão a leitura é rejeitada.
    """
    i = analise.ativo
    try:
        from core.contexto_mercado import classe_conjuntura
        from core.llm_context_global_armazem import _leitores

        classe = classe_conjuntura({"classe": i.classe, "moeda": i.moeda})
        if classe is None or not str(i.ticker or "").strip():
            return ""
        leitor = _leitores()[classe]
        alvo = [str(i.ticker).strip().upper()]
        return leitor(alvo) if captura is None else leitor(alvo, captura=captura)
    except Exception as exc:  # noqa: BLE001 — a leitura continua sem o detalhe
        log.warning("detalhe do armazém indisponível: %s", exc)
        return ("DETALHE DO ARMAZÉM LOCAL: falha ao montar "
                f"({type(exc).__name__}); liquidez, preço e proventos do "
                "armazém não entraram. Não trate como dado zero.")


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
          mercado: str | None = None,
          referencia_modelo: dict | None = None) -> pf.Leitura:
    """Monta o contexto, chama a LLM e valida a resposta.

    ``chamar`` e ``mercado`` existem para teste: sem eles, usa o provedor
    configurado e monta o bloco de mercado de verdade. ``referencia_modelo``
    é a carteira recomendada do Portfólio Global (``referencia_modelo.
    ReferenciaModelo.para_llm``), só como comparação."""
    fatos = None
    if mercado is None:
        mercado = contexto_mercado_do_ativo(analise)
        detalhe, fatos = armazem_do_ativo(analise)
        if detalhe:
            mercado += "\n\n" + detalhe
    contexto = pf.contexto(analise, ctx, cenario_mercado=mercado,
                           mercado_armazem=fatos,
                           referencia_modelo=referencia_modelo)
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

# -*- coding: utf-8 -*-
"""A-152: qual evidência sustenta cada motor de score, dita na própria aba.

O App 4 tem **três** motores de score independentes sob uma casca visual única:
`core/fii_methodology.py`, `core/us_score.py` e o motor B3 em
`views/empresas_b3.py`. A casca comum faz as três notas parecerem igualmente
sustentadas. Não são, e até aqui só uma delas dizia isso ao usuário.

`design/componentes.aviso_escala_do_score` já declarava que a escala é local e
que as metodologias são independentes -- o que se lê como "diferentes porém
equivalentes". O que faltava era o segundo fato: **que evidência temporal
sustenta cada uma**. Medido em 27/08/2026, contra o Supabase de produção:

  FII  -- mostra "Validação PIT: Aprovada/Pendente" como KPI ao lado do score.
  B3   -- `core.b3_validation.validation_readiness` apura o estado e NOMEIA os
          bloqueadores ("PIT estrito sem published_at/revisões CVM"; "universo
          histórico de deslistadas incompleto"). Nenhuma tela consultava isso:
          o único chamador era o relatório de confiança. Motor de diagnóstico
          sem porta de entrada é decoração.
  EUA  -- declara, mas dentro de um expander colapsado da aba de Criação de
          Portfólio, e só sobre o painel de backtest. A seção de pontuação, que
          é onde a nota aparece, não dizia nada.

Este módulo não inventa nota nem grau: lê as fontes que já existem e devolve o
estado para a aba renderizar. Não valida nada -- só relata quem validou.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = ["EstadoValidacao", "validacao_b3", "validacao_fii", "validacao_us"]


_SEM_VINTAGES = ("vitrine publicada sem score_vintages e preços mensais: "
                 "nenhum retorno histórico foi simulado")


@dataclass(frozen=True)
class EstadoValidacao:
    """Estado da validação temporal de um motor de score.

    `aprovada` é tri-estado de propósito: `None` significa *não foi possível
    apurar*, e não pode virar "pendente" nem "aprovada". Apagar a diferença
    entre "medi e reprovou" e "não consegui medir" é o defeito que este módulo
    existe para não repetir.
    """
    classe: str
    versao: str
    aprovada: bool | None
    bloqueadores: tuple[str, ...] = ()
    detalhe: str = ""

    @property
    def rotulo(self) -> str:
        if self.aprovada is None:
            return "Não apurada"
        return "Aprovada" if self.aprovada else "Pendente"

    @property
    def texto(self) -> str:
        """Frase única para caption, com os bloqueadores nomeados."""
        base = f"Validação temporal (PIT): {self.rotulo.lower()}"
        if self.aprovada:
            return f"{base}. Metodologia {self.versao}."
        if self.bloqueadores:
            return (f"{base} — {'; '.join(self.bloqueadores)}. "
                    f"Metodologia {self.versao}. A nota ordena o universo; "
                    f"ela ainda não foi verificada fora da amostra.")
        return (f"{base}. Metodologia {self.versao}. A nota ordena o universo; "
                f"ela ainda não foi verificada fora da amostra.")


def _falha(classe: str, versao: str, exc: Exception) -> EstadoValidacao:
    logger.warning("validacao_motor %s: %s", classe, exc)
    return EstadoValidacao(classe, versao, None,
                           detalhe=f"não apurado: {type(exc).__name__}")


def validacao_b3(engine=None) -> EstadoValidacao:
    """Lê `core.b3_validation.validation_readiness` -- os bloqueadores são dele."""
    from core.b3_methodology import SCORE_VERSION
    try:
        from core.b3_validation import build_data_manifest, validation_readiness
        from core.database import get_engine
        pronto = validation_readiness(build_data_manifest(engine or get_engine()))
        bloq = tuple(str(b) for b in (pronto.get("blockers") or []))
        return EstadoValidacao("Empresas B3", SCORE_VERSION,
                               bool(pronto.get("ready")), bloq)
    except Exception as exc:  # noqa: BLE001
        return _falha("Empresas B3", SCORE_VERSION, exc)


def validacao_fii() -> EstadoValidacao:
    """Lê o certificado PIT persistido para a metodologia em uso."""
    from core.fii_methodology import METHODOLOGY_VERSION
    try:
        from core.market_read import load_fii_validation_status
        val = load_fii_validation_status(METHODOLOGY_VERSION) or {}
        bloq = tuple(str(b) for b in (val.get("blockers") or []))
        return EstadoValidacao("Seleção de FIIs", METHODOLOGY_VERSION,
                               str(val.get("status")) == "passed", bloq)
    except Exception as exc:  # noqa: BLE001
        return _falha("Seleção de FIIs", METHODOLOGY_VERSION, exc)


def validacao_us(history_available: object = None) -> EstadoValidacao:
    """Estado do motor americano.

    Sem `score_vintages` e preços mensais na vitrine não há Rank-IC fora da
    amostra -- é a mesma condição que a aba de Criação de Portfólio já usa para
    decidir se roda o painel PIT. Aceita o valor já apurado pela tela para não
    repetir a consulta; sem ele, apura por conta própria.
    """
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION as v
    if history_available is not None:
        pronto = bool(history_available)
        return EstadoValidacao(
            "Empresas Americanas", v, pronto,
            () if pronto else (_SEM_VINTAGES,))
    try:
        import core.us_data as us
        painel = us.score_panel()
        pronto = painel is not None and not painel.empty
        return EstadoValidacao(
            "Empresas Americanas", v, pronto,
            () if pronto else (_SEM_VINTAGES,))
    except Exception as exc:  # noqa: BLE001
        return _falha("Empresas Americanas", v, exc)

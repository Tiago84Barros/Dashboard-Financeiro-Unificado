"""Cenário macro observado que alimenta os padrões da tela e o prompt da LLM.

A tela de Seleção de FIIs partia de literais (Selic 15,0% e IPCA 4,5%) e o
contexto da LLM rotulava esse padrão como informado pelo usuário. O resultado
era um relatório que afirmava com confiança uma Selic que já não vigorava.

Aqui a observação vem de ``public.macro`` — série SGS 432 do BCB, gravada anual
pelo job ``data_pipeline.jobs.update_bcb`` com o último valor de cada ano. Sem
observação, o padrão é declarado como arbitrário em vez de passar por dado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

FONTE = "public.macro (BCB/SGS 432, último valor do ano)"

#: Partida de último recurso quando não há nenhuma observação de Selic. Não é
#: uma estimativa: existe só para o widget ter um número e vem acompanhada de
#: ``indisponivel``, que a tela e o prompt exibem.
PADRAO_SEM_OBSERVACAO = 10.0


@dataclass(frozen=True)
class CenarioObservado:
    """Cenário lido da base, com a origem separada do valor."""

    ano: int | None = None
    selic: float | None = None
    ipca: float | None = None
    selic_change_12m: float | None = None
    fonte: str = FONTE
    indisponivel: str | None = None

    @property
    def padrao_selic(self) -> float:
        return PADRAO_SEM_OBSERVACAO if self.selic is None else self.selic

    def procedencia(self, ajustado: bool) -> str:
        """Rótulo que o prompt usa para não atribuir ao usuário o que é padrão."""
        if ajustado:
            return "ajustado pelo usuário"
        if self.indisponivel:
            return f"padrão sem observação ({self.indisponivel})"
        return f"observado ({FONTE}, ano {self.ano})"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _selic_percent(value: Any) -> float | None:
    """``public.macro`` grava Selic ora em fração (0.14) ora em percentual."""
    number = _finite(value)
    if number is None:
        return None
    # round() evita que 0.14*100 vire 14.000000000000002 no widget e no prompt.
    return round(number * 100.0, 4) if abs(number) <= 1 else number


def cenario_macro_observado(
    history: Mapping[int, Mapping[str, Any]] | None,
) -> CenarioObservado:
    """Extrai Selic, IPCA e Δ12m do ano mais recente com Selic observada."""
    anos = {}
    for ano, valores in (history or {}).items():
        try:
            anos[int(ano)] = valores or {}
        except (TypeError, ValueError):
            continue

    com_selic = {ano: _selic_percent(valores.get("selic"))
                 for ano, valores in anos.items()}
    com_selic = {ano: selic for ano, selic in com_selic.items() if selic is not None}
    if not com_selic:
        return CenarioObservado(
            indisponivel="public.macro sem Selic observada",
        )

    ano = max(com_selic)
    anterior = com_selic.get(ano - 1)
    return CenarioObservado(
        ano=ano,
        selic=com_selic[ano],
        ipca=_finite(anos[ano].get("ipca")),
        selic_change_12m=(None if anterior is None
                          else round(com_selic[ano] - anterior, 4)),
    )

"""Piso absoluto de qualidade da carteira EUA, com substituição no mesmo grupo.

Por que existe. ``core/us_advanced_lab.build_entry_scores`` já produz um
veredito por empresa — ``entry_status`` ∈ {Aprovada, Observação, Excluída},
onde "Excluída" significa penalidade de risco ≥ 10 (Altman em aflição somado a
outro alerta, margem líquida negativa com FCF negativo, dívida/EBITDA elevada,
Piotroski fraco). Esse veredito **só aparecia na tela**: a Criação de Portfólio
ordena por ``entry_score``, um número, e a penalidade apenas abaixa a nota em
vez de barrar. Medido em 03/08/2026: **824 das 2.831 empresas com score (29%)
estão marcadas Excluída e podiam liderar sua indústria mesmo assim**, com
"margem líquida negativa; fluxo de caixa livre negativo" como motivo mais comum
(270 casos).

É o mesmo padrão corrigido no módulo B3 — motor de diagnóstico sem porta de
entrada na decisão — e a proporção é quase idêntica (27% lá, 29% aqui).

**O piso NÃO define limiares próprios.** Ele lê o veredito do laboratório
avançado, que já traz calibragem madura e específica dos EUA: Altman não se
aplica a bancos nem REITs (o Z-Score foi calibrado em indústrias de 1968 e
classifica mal empresas asset-light), payout alto não penaliza REIT (distribuir
FFO acima do lucro contábil é exigência legal, não alerta), e nenhum sinal
isolado exclui — peso 8 do Altman só barra somado a outro. Duplicar essa régua
aqui criaria duas fontes de verdade divergindo com o tempo, que é exatamente o
defeito que originou o piso.

Puro (sem banco, sem rede). Coberto por tests/test_us_quality_floor.py.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

VERSION = "us-quality-floor-1.0.0"

APROVADO = "aprovado"
REPROVADO = "reprovado"
SEM_EVIDENCIA = "sem_evidencia"

# Rótulos que `build_entry_scores` grava em `entry_status`.
_EXCLUIDA = "Excluída"
_OBSERVACAO = "Observação"


@dataclass(frozen=True)
class FloorPolicy:
    """O que o piso reprova.

    reprovar_excluidas: o veredito do laboratório. Ligado por padrão — é o
        motivo de o módulo existir.
    reprovar_observacao: desligado. "Observação" é a faixa NORMAL do universo
        americano: em 03/08/2026, 2.007 das 2.831 empresas caem nela e nenhuma
        alcança "Aprovada" (entry_score ≥ 60). Reprovar aí esvaziaria a carteira
        inteira, não a tornaria mais seletiva.
    """

    reprovar_excluidas: bool = True
    reprovar_observacao: bool = False


@dataclass(frozen=True)
class FloorVerdict:
    symbol: str
    situacao: str
    motivos: tuple[str, ...] = ()

    @property
    def reprovado(self) -> bool:
        return self.situacao == REPROVADO


def _texto(valor: object) -> str:
    if valor is None:
        return ""
    try:
        if pd.isna(valor):                     # type: ignore[arg-type]
            return ""
    except (TypeError, ValueError):
        pass
    return str(valor).strip()


def evaluate(frame: pd.DataFrame, symbols: Sequence[str] | None = None, *,
             policy: FloorPolicy | None = None) -> dict[str, FloorVerdict]:
    """Veredito do piso por símbolo, na régua de ``build_entry_scores``.

    Args:
        frame: saída de ``us_advanced_lab.build_entry_scores`` — precisa das
            colunas ``symbol``, ``entry_status`` e (opcional) ``risk_driver``.
        symbols: recorte a avaliar. None avalia o frame inteiro.

    Ausência de ``entry_status`` vira ``sem_evidencia``, nunca reprovação: sem o
    veredito do laboratório não há o que reprovar, e tratar lacuna como falha
    condenaria empresa por dado que não chegou.
    """
    policy = policy or FloorPolicy()
    if frame is None or frame.empty or "symbol" not in frame.columns:
        return {}

    alvos = ({str(s).upper() for s in symbols} if symbols is not None else None)
    veredito: dict[str, FloorVerdict] = {}
    for _, linha in frame.iterrows():
        symbol = _texto(linha.get("symbol")).upper()
        if not symbol or (alvos is not None and symbol not in alvos):
            continue
        status = _texto(linha.get("entry_status"))
        motivo = _texto(linha.get("risk_driver"))
        motivos = tuple(m.strip() for m in motivo.split(";") if m.strip()) if motivo else ()

        if not status:
            situacao = SEM_EVIDENCIA
            motivos = ("laboratório avançado não avaliou esta empresa",)
        elif (policy.reprovar_excluidas and status == _EXCLUIDA) or (
                policy.reprovar_observacao and status == _OBSERVACAO):
            situacao = REPROVADO
        else:
            situacao = APROVADO
        veredito[symbol] = FloorVerdict(symbol, situacao, motivos)
    return veredito


def apply_with_substitution(
    selecionados: Sequence[str],
    ranked: Sequence[tuple[str, float]],
    frame: pd.DataFrame,
    pesos: dict[str, float],
    grupo_label: str,
    log: dict,
    *,
    policy: FloorPolicy | None = None,
    max_substitutos: int = 3,
) -> list[str]:
    """Reprova pelo piso e chama o próximo do MESMO grupo, que herda o peso.

    A vaga do grupo é preservada de propósito: sem isso, exigir qualidade
    custaria diversificação, e a carteira ficaria menor toda vez que o piso
    agisse. Com a substituição, o grupo continua representado por outro nome.

    Quando nenhum candidato do grupo passa, a vaga fica VAZIA e o caso entra em
    ``log["sem_substituto"]`` — declarar é melhor que rebaixar em silêncio para
    o segundo pior.
    """
    policy = policy or FloorPolicy()
    universo = list(dict.fromkeys(
        [str(s).upper() for s in selecionados]
        + [str(c).upper() for c, _ in ranked]))
    veredito = evaluate(frame, universo, policy=policy)

    finais: list[str] = []
    for symbol in (str(s).upper() for s in selecionados):
        v = veredito.get(symbol)
        if v is None or not v.reprovado:
            finais.append(symbol)
            continue

        log.setdefault("reprovados", []).append({
            "symbol": symbol, "grupo": grupo_label,
            "motivo": "; ".join(v.motivos) or "reprovada no piso de qualidade"})

        substituto = None
        tentativas = 0
        for candidato, _score in ranked:
            candidato = str(candidato).upper()
            if candidato in finais or candidato in {str(s).upper() for s in selecionados}:
                continue
            if tentativas >= max_substitutos:
                break
            tentativas += 1
            vc = veredito.get(candidato)
            if vc is not None and not vc.reprovado:
                substituto = candidato
                break

        if substituto:
            finais.append(substituto)
            # O substituto herda o orçamento de peso da vaga — é o que mantém a
            # diversificação intacta quando o piso age.
            pesos[substituto] = pesos.get(substituto) or pesos.get(symbol, 0.0)
            log.setdefault("substituicoes", []).append({
                "entra": substituto, "sai": symbol, "grupo": grupo_label})
        else:
            log.setdefault("sem_substituto", []).append({
                "symbol": symbol, "grupo": grupo_label})
    return finais

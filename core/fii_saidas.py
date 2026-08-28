# -*- coding: utf-8 -*-
"""Saidas do universo de FIIs derivadas de snapshots -- com a guarda que importa.

`market.fii_universe_history` so recebe linhas `listed`, escritas por
`_refresh_pro_universe` a partir da listagem corrente da brapi. Um painel
alimentado so pelo que existe hoje **nunca pode perder um fundo**: e a mesma
assinatura de sobrevivencia ja documentada no modulo dos EUA, e ela infla de
graca a cobertura de retornos que `core/fii_validation.py` exige.

A saida e derivavel dos proprios snapshots: quem estava na foto de T1 e sumiu
da foto de T2 deixou o universo. O que torna isso perigoso -- e o motivo deste
modulo existir em vez de uma consulta solta -- e que **uma foto incompleta e
indistinguivel de uma onda de encerramentos**. Em 28/08/2026 a base tinha
exatamente esse caso: 1.029 tickers em 12/07 e 393 em 14/07. A derivacao ingenua
marcaria 636 fundos saudaveis como encerrados, com carimbo de evidencia.

Por isso a regra e conservadora nos dois sentidos:

* so compara fotos que passam no piso de cobertura (`COBERTURA_MINIMA` do
  tamanho da maior foto ate ali). Foto pequena nao gera saida -- e tambem nao
  serve de referencia para a proxima;
* a ausencia vira `delisted`, nunca `liquidated` nem `incorporated`. Sumir da
  listagem e o que foi observado; a causa (liquidacao, incorporacao, troca de
  ticker) exige documento e nao se inventa a partir de uma ausencia.

E quando nao da para derivar nada, o diagnostico diz **qual** dos dois casos
ocorreu: "nenhuma saida observada" e "nao havia como observar saida" sao coisas
diferentes, e confundi-las e o que faz um portao so poder dar `False`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)

# Piso de cobertura de uma foto para ser considerada completa, relativo a maior
# foto vista ate a data dela. 0,90 tolera oscilacao normal do universo e barra
# coleta truncada; a foto de 393 contra 1.029 (38%) fica fora com folga.
COBERTURA_MINIMA = 0.90

STATUS_SAIDA = "delisted"
FONTE_DERIVADA = "derivado:ausencia_entre_snapshots"


@dataclass
class Diagnostico:
    """Por que sairam (ou nao sairam) saidas -- em linguagem de auditoria."""

    saidas: list[dict] = field(default_factory=list)
    comparaveis: list[date] = field(default_factory=list)
    descartadas: list[tuple[date, int, float]] = field(default_factory=list)
    motivo: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.saidas)


def _fotos_comparaveis(fotos: dict[date, set[str]]
                       ) -> tuple[list[date], list[tuple[date, int, float]]]:
    """Separa as fotos completas das truncadas, em ordem cronologica.

    O piso e relativo a MAIOR foto vista ate ali, e nao a foto anterior: duas
    coletas truncadas seguidas se validariam uma a outra se a referencia fosse
    a vizinha, e a segunda ainda apagaria o que a primeira ja tinha apagado.
    """
    comparaveis: list[date] = []
    descartadas: list[tuple[date, int, float]] = []
    maior = 0
    for data in sorted(fotos):
        n = len(fotos[data])
        cobertura = 1.0 if maior == 0 else n / maior
        if maior == 0 or cobertura >= COBERTURA_MINIMA:
            comparaveis.append(data)
            maior = max(maior, n)
        else:
            descartadas.append((data, n, cobertura))
    return comparaveis, descartadas


def derivar_saidas(fotos: dict[date, set[str]]) -> Diagnostico:
    """Tickers que sumiram entre duas fotos completas consecutivas.

    Cada saida e datada na foto em que o ticker JA NAO aparece -- e a primeira
    data em que sabemos da ausencia, nao a data do encerramento, que a listagem
    nao informa. Datar na foto anterior faria o painel afirmar conhecimento que
    ele nao tinha, exatamente o vies point-in-time que o resto do sistema
    persegue.
    """
    if not fotos:
        return Diagnostico(motivo="nenhum snapshot de universo gravado")

    comparaveis, descartadas = _fotos_comparaveis(fotos)
    diag = Diagnostico(comparaveis=comparaveis, descartadas=descartadas)

    if len(comparaveis) < 2:
        cortadas = ", ".join(f"{d} ({n} tickers, {c:.0%} da maior foto)"
                             for d, n, c in descartadas) or "nenhuma"
        diag.motivo = (
            f"so {len(comparaveis)} foto completa do universo: sem duas fotos "
            f"comparaveis, ausencia nao e observavel. Fotos descartadas por "
            f"cobertura: {cortadas}")
        return diag

    # A foto truncada nao DATA saida -- ela nao sabe quem falta. Mas quem ela
    # mostra listado esta vivo, e essa metade da evidencia e boa. Ignora-la
    # datava o "visto por ultimo" cedo demais e, pior, mantinha como encerrado
    # o fundo que reaparecia numa coleta parcial posterior.
    todas = sorted(fotos)
    for anterior, atual in zip(comparaveis, comparaveis[1:]):
        for ticker in sorted(fotos[anterior] - fotos[atual]):
            if any(ticker in fotos[d] for d in todas if d > atual):
                continue  # reapareceu depois: a saida esta desmentida
            visto = max((d for d in todas
                         if d < atual and ticker in fotos[d]), default=anterior)
            diag.saidas.append({
                "ticker": ticker,
                "reference_date": atual,
                "active_status": STATUS_SAIDA,
                "successor_ticker": None,
                "source": FONTE_DERIVADA,
                "visto_por_ultimo_em": visto,
            })
    if not diag.saidas:
        diag.motivo = (
            f"{len(comparaveis)} fotos completas comparadas e nenhum ticker "
            f"desapareceu: ausencia de saida OBSERVADA, nao falta de observacao")
    return diag


def fotos_do_banco(conn) -> dict[date, set[str]]:
    """Le as fotos de `market.fii_universe_history` como {data: {tickers}}.

    So considera linhas `listed`/`active`: uma linha de saida ja gravada nao
    pode virar insumo para derivar outra saida. Fotos marcadas `limitado` pela
    ingestao tambem ficam de fora -- o piso de cobertura ja as barraria, mas uma
    coleta de teste que por acaso passe do piso continuaria sendo teste.
    """
    from sqlalchemy import text

    fotos: dict[date, set[str]] = {}
    linhas = conn.execute(text(
        "SELECT reference_date, ticker FROM market.fii_universe_history "
        "WHERE active_status IN ('listed','active') "
        "  AND coalesce(metadata_json->>'limitado', 'false') <> 'true'"))
    for referencia, ticker in linhas:
        fotos.setdefault(referencia, set()).add(str(ticker))
    return fotos

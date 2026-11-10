# -*- coding: utf-8 -*-
"""Vacância de FII: escala, agregação por fonte única e escolha da fonte do fundo.

Funções puras, sem banco nem rede. Nasceram de um defeito medido em 10/11/2026:
o XPML11 aparecia com 65,96% de vacância (sites: ~5%). Causas encadeadas:

* o mesmo imóvel chegava de duas fontes com nomes diferentes (statusinvest em
  fração, 0,01; brapi com 1,00 em quase todos os imóveis) e a média ponderada
  por área misturava as duas;
* a escala não era declarada: 1,00 pode ser 100% (fração) ou 1% (percentual);
* a data gravada era a da rodada, não a data-base.

Contrato de escala (explícito por chamada):

* ``"fracao"``: o valor é razão em [0, 1]. Valor acima de 1 e até 100 não pode
  ser fração; só pode ser percentual e é dividido por 100. Acima de 100: ausente.
* ``"percentual"``: o valor está em pontos percentuais [0, 100]; divide por 100.

Ambiguidade que nenhuma escala resolve: lista de imóveis em que a maior parte
da área vem exatamente 1,00. Pode ser fundo de fato vazio ou gestor que
preencheu o informe em percentual. Em vez de adivinhar, a agregação devolve
``None`` (ausente) e o fundo cai para a fonte oficial (CVM trimestral).
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping

ESCALAS = ("fracao", "percentual")
# Fração da área com vacância exatamente 1,00 a partir da qual a lista é
# ambígua, e número mínimo de imóveis para a regra valer.
LIMITE_AREA_TOTALMENTE_VAGA = 0.5
MIN_IMOVEIS_AMBIGUIDADE = 4
# A CVM trimestral é "recente" até este número de dias após a data-base
# (trimestre + 45 dias de prazo de entrega + folga de uma safra).
CVM_MAX_IDADE_DIAS = 200
# Piso de cardinalidade da poda: não apaga imóvel se a coleta nova trouxe menos
# da metade do que já havia (coleta truncada não prova que o imóvel saiu).
PISO_PODA = 0.5


def para_fracao(valor, escala: str) -> float | None:
    """Converte a vacância de UMA fonte para razão em [0, 1], ou None."""
    if escala not in ESCALAS:
        raise ValueError(f"escala desconhecida: {escala!r}")
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero != numero or numero in (float("inf"), float("-inf")) or numero < 0:
        return None
    if escala == "percentual":
        return numero / 100.0 if numero <= 100.0 else None
    if numero <= 1.0:
        return numero
    return numero / 100.0 if numero <= 100.0 else None


def lista_ambigua(linhas: Iterable[Mapping]) -> bool:
    """True se a maior parte da área vem com vacância exatamente 1,00."""
    com_dado = [(r.get("vacancia"), r.get("area_m2") or 0.0) for r in linhas
                if r.get("vacancia") is not None]
    if len(com_dado) < MIN_IMOVEIS_AMBIGUIDADE:
        return False
    total = sum(a for _, a in com_dado if a > 0)
    if total > 0:
        vaga = sum(a for v, a in com_dado if a > 0 and v >= 1.0)
        return vaga / total >= LIMITE_AREA_TOTALMENTE_VAGA
    return sum(1 for v, _ in com_dado if v >= 1.0) / len(com_dado) >= LIMITE_AREA_TOTALMENTE_VAGA


def agregar_imoveis(linhas: Iterable[Mapping], escala: str = "fracao") -> float | None:
    """Vacância do fundo = média ponderada pela área de UMA fonte. None se ambígua."""
    normal = []
    for r in linhas:
        v = para_fracao(r.get("vacancia"), escala)
        if v is not None:
            normal.append({"vacancia": v, "area_m2": r.get("area_m2")})
    if not normal or lista_ambigua(normal):
        return None
    com_area = [(r["vacancia"], float(r["area_m2"])) for r in normal
                if r.get("area_m2") and float(r["area_m2"]) > 0]
    if com_area:
        return round(sum(v * a for v, a in com_area) / sum(a for _, a in com_area), 4)
    return round(sum(r["vacancia"] for r in normal) / len(normal), 4)


def coleta_mais_recente(linhas: Iterable[Mapping]) -> list[Mapping]:
    """Mantém UMA fonte: a de upsert mais novo (empate: ordem alfabética).

    Todas as linhas da fonte escolhida entram. ``updated_at`` só muda quando o
    valor muda (o upsert usa IS DISTINCT FROM), então um imóvel inalterado tem
    carimbo antigo mesmo coletado hoje; filtrar por janela de tempo o trataria
    como fóssil. Fóssil de verdade (imóvel que saiu) é apagado por
    ``deve_podar`` na própria ingestão. Misturar fontes soma o mesmo m² duas vezes.
    """
    linhas = [r for r in linhas if r.get("updated_at") is not None]
    if not linhas:
        return []
    topo = max(r["updated_at"] for r in linhas)
    fonte = sorted({r.get("fonte") or "" for r in linhas if r["updated_at"] == topo})[0]
    return [r for r in linhas if (r.get("fonte") or "") == fonte]


def decidir_vacancia_fundo(imoveis: Iterable[Mapping], cvm: Mapping | None,
                           hoje: date) -> dict | None:
    """Escolhe UMA fonte e a data-base da vacância do fundo.

    ``cvm`` = ``{"valor": razão, "data_base": date}`` da última observação
    ``vacancia_fisica`` da CVM trimestral. Preferência: CVM recente (oficial,
    com data-base); senão a coleta de imóveis mais nova, datada pela coleta (as
    fontes de imóveis não publicam data-base). Nunca mistura fontes.
    """
    if cvm and cvm.get("valor") is not None and cvm.get("data_base") is not None:
        valor = para_fracao(cvm["valor"], "fracao")
        if valor is not None and valor <= 1.0 and (hoje - cvm["data_base"]).days <= CVM_MAX_IDADE_DIAS:
            return {"vacancia": round(valor, 4), "vacancia_ref_date": cvm["data_base"],
                    "origem": "cvm_informe_trimestral"}
    coleta = coleta_mais_recente(imoveis)
    valor = agregar_imoveis(coleta, "fracao")
    if valor is None:
        return None
    return {"vacancia": valor,
            "vacancia_ref_date": max(r["updated_at"] for r in coleta).date(),
            "origem": f"imoveis:{coleta[0].get('fonte')}"}


def deve_podar(existentes: int, novos: int, piso: float = PISO_PODA) -> bool:
    """Só apaga o que saiu se a coleta nova cobre ao menos ``piso`` do que havia."""
    if novos <= 0:
        return False
    return novos >= piso * existentes

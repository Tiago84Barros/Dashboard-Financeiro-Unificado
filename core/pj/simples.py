"""Estimativa do DAS do Simples Nacional (LC 123/2006, tabelas da LC 155/2016).

Alíquota efetiva = (RBT12 × alíquota nominal − parcela a deduzir) / RBT12,
com a faixa escolhida pela RBT12 (receita bruta dos 12 meses anteriores ao
mês de apuração). DAS estimado = receita do mês × alíquota efetiva.

Fator R (Anexo V): folha dos 12 meses / RBT12. Com Fator R de 28% ou mais, a
atividade do Anexo V é tributada pelo Anexo III.

É ESTIMATIVA. O valor oficial é o do PGDAS-D: ele considera atividades
mistas, ISS retido, segregação de receitas e o início de atividade com regra
própria, que esta conta só aproxima (média dos meses com receita × 12).
"""
from __future__ import annotations

from dataclasses import dataclass

LIMITES_FAIXA = (180_000.0, 360_000.0, 720_000.0, 1_800_000.0, 3_600_000.0, 4_800_000.0)

#: anexo -> [(alíquota nominal, parcela a deduzir)] por faixa.
TABELAS: dict[str, tuple[tuple[float, float], ...]] = {
    "I": ((0.04, 0.0), (0.073, 5_940.0), (0.095, 13_860.0),
          (0.107, 22_500.0), (0.143, 87_300.0), (0.19, 378_000.0)),
    "II": ((0.045, 0.0), (0.078, 5_940.0), (0.10, 13_860.0),
           (0.112, 22_500.0), (0.147, 85_500.0), (0.30, 720_000.0)),
    "III": ((0.06, 0.0), (0.112, 9_360.0), (0.135, 17_640.0),
            (0.16, 35_640.0), (0.21, 125_640.0), (0.33, 648_000.0)),
    "IV": ((0.045, 0.0), (0.09, 8_100.0), (0.102, 12_420.0),
           (0.14, 39_780.0), (0.22, 183_780.0), (0.33, 828_000.0)),
    "V": ((0.155, 0.0), (0.18, 4_500.0), (0.195, 9_900.0),
          (0.205, 17_100.0), (0.23, 62_100.0), (0.305, 540_000.0)),
}
ANEXOS_DESCRICAO = {
    "I": "Anexo I — Comércio",
    "II": "Anexo II — Indústria",
    "III": "Anexo III — Serviços (ex.: instalação, manutenção, agências)",
    "IV": "Anexo IV — Serviços (ex.: limpeza, vigilância, obras, advocacia)",
    "V": "Anexo V — Serviços sujeitos ao Fator R (ex.: TI, consultoria, engenharia)",
}
FATOR_R_MINIMO = 0.28


@dataclass(frozen=True)
class EstimativaDAS:
    anexo_aplicado: str
    rbt12: float
    faixa: int | None
    aliquota_nominal: float | None
    aliquota_efetiva: float | None
    receita_mes: float
    das_estimado: float | None
    fator_r: float | None
    rbt12_proporcional: bool
    aviso: str | None


def rbt12_de(receitas_12m: list[float]) -> tuple[float, bool]:
    """RBT12 a partir das receitas dos 12 meses ANTERIORES ao de apuração.

    Com menos de 12 meses de histórico, usa a média dos meses que existem × 12
    (aproximação do início de atividade) e marca como proporcional.
    """
    valores = [float(v or 0.0) for v in receitas_12m]
    if len(valores) >= 12:
        return round(sum(valores[-12:]), 2), False
    if not valores:
        return 0.0, True
    return round(sum(valores) / len(valores) * 12, 2), True


def anexo_efetivo(anexo: str, fator_r: float | None) -> str:
    if anexo == "V" and fator_r is not None and fator_r >= FATOR_R_MINIMO:
        return "III"
    return anexo


def aliquota_efetiva(anexo: str, rbt12: float) -> tuple[int | None, float | None, float | None]:
    """(faixa 1-6, alíquota nominal, alíquota efetiva). RBT12 acima do teto
    do Simples devolve ``(None, None, None)``."""
    tabela = TABELAS[anexo]
    if rbt12 <= 0:
        return 1, tabela[0][0], tabela[0][0]
    for i, limite in enumerate(LIMITES_FAIXA):
        if rbt12 <= limite:
            nominal, deduzir = tabela[i]
            return i + 1, nominal, round((rbt12 * nominal - deduzir) / rbt12, 6)
    return None, None, None


def fator_r_de(receitas_mensais: list[float], folhas_mensais: list[float]) -> float | None:
    """Folha ÷ receita no MESMO período (os últimos até 12 meses dos dois).

    No início de atividade a RBT12 é anualizada; anualizar só a receita e não
    a folha derrubaria o Fator R pela metade com seis meses de histórico. A
    razão entre somas do mesmo período não depende da anualização.
    """
    n = min(12, len(receitas_mensais), len(folhas_mensais))
    if n == 0:
        return None
    receita = sum(float(v or 0) for v in receitas_mensais[-n:])
    if receita <= 0:
        return None
    # Sem arredondar: 27,999% arredondado viraria 28% e mudaria o anexo.
    return sum(float(v or 0) for v in folhas_mensais[-n:]) / receita


def estimar_das(anexo: str, receitas_12m_anteriores: list[float], receita_mes: float,
                folhas_12m_anteriores: list[float] | None = None) -> EstimativaDAS:
    """``folhas_12m_anteriores``: folha mês a mês, alinhada às receitas."""
    rbt12, proporcional = rbt12_de(receitas_12m_anteriores)
    fator_r = (fator_r_de(receitas_12m_anteriores, folhas_12m_anteriores)
               if folhas_12m_anteriores is not None else None)
    aplicado = anexo_efetivo(anexo, fator_r)
    faixa, nominal, efetiva = aliquota_efetiva(aplicado, rbt12)
    aviso = None
    if faixa is None:
        aviso = "RBT12 acima de R$ 4,8 milhões: fora do limite do Simples Nacional."
    das = round(receita_mes * efetiva, 2) if efetiva is not None else None
    return EstimativaDAS(aplicado, rbt12, faixa, nominal, efetiva, round(receita_mes, 2),
                         das, fator_r, proporcional, aviso)

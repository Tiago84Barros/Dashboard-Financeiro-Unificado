"""Como a empresa americana atravessou recessão — medido, não inferido do setor.

Espelha ``core/b3_cycle_evidence`` com uma diferença que o dado impôs, e ela é a
decisão central deste módulo.

**Exige a crise de 2008.** No Brasil, 81% das empresas tinham as duas recessões
(2015-16 e 2020) e a medição era ampla. Aqui, medido em 03/08/2026 sobre 3.048
empresas com série anual: apenas **548 (18%) alcançam a crise de 2008**, contra
2.244 que só têm a COVID. A tentação seria medir com o que há — e seria errado.

A COVID sozinha não sustenta veredito de resiliência. Foi choque de oferta e de
serviços, curto e seguido de estímulo fiscal maciço; empresa de software ou de
bens duráveis atravessou 2020 com margem intacta e isso não diz nada sobre como
ela se comporta numa recessão de demanda. No módulo B3 esse mesmo formato
atípico fez commodity medir como mais defensiva que saneamento — e por isso lá a
resiliência informa sem reclassificar nada.

Então aqui: quem não tem 2008 fica **sem veredito**, e a tela diz isso. Medir
2.244 empresas com base fraca produziria número em toda linha e confiança em
nenhuma. Preferir silêncio a ruído é o que mantém o sinal útil onde ele existe.

A dimensão de "decisão de governo" do módulo B3 NÃO foi trazida, por escolha do
usuário e porque não traduz: os EUA quase não têm estatal listada, e o
equivalente (regulador de tarifa, reembolso público, orçamento de defesa) é
outro objeto, que mereceria calibragem própria.

Puro (sem banco, sem rede). Coberto por tests/test_us_cycle_evidence.py.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

VERSION = "us-cycle-evidence-1.0.0"

# Recessões americanas com demonstração anual no banco. A janela de 2008 abre em
# 2007 e fecha em 2010 para pegar o exercício inteiro das empresas cujo ano
# fiscal não fecha em dezembro.
CRISE_2008 = (2007, 2008, 2009, 2010)
CRISE_COVID = (2019, 2020, 2021)

# Mínimo de exercícios dentro da janela. Com um só ano, a leitura confunde
# recessão com evento isolado da empresa (recall, aquisição, greve).
MIN_ANOS_CRISE = 2

RESILIENTE = "resiliente"
INTERMEDIARIO = "intermediario"
FRAGIL = "fragil"
SEM_EVIDENCIA = "sem_evidencia"

# Mesmas faixas do módulo B3: a razão é adimensional (margem na crise ÷ margem
# normal), então não depende de moeda nem de nível de juros.
LIMITE_RESILIENTE = 0.80
LIMITE_FRAGIL = 0.40

# Teto de plausibilidade. Empresa não TRIPLICA margem operacional numa recessão;
# razão acima disso é artefato contábil — ganho não recorrente no ano de crise
# ou erro de dado. Medido em 03/08/2026: ESS aparecia com razão de 77,90 (margem
# normal de 32,6% contra "margem de crise" de 2.540%) e AZTA com 59,74.
#
# Sem o teto, esses casos entrariam como as empresas MAIS resilientes do
# universo — o pior tipo de falso positivo, porque premia o artefato.
#
# A cauda NEGATIVA não tem teto de propósito: margem que vira -42× a normal é
# colapso, e a leitura "frágil" está correta por qualquer corte. Truncar ali
# perderia informação verdadeira.
RAZAO_MAXIMA_PLAUSIVEL = 3.0

# Piso do DENOMINADOR. Margem normal perto de zero explode a razão sem que nada
# tenha acontecido com o negócio — AZTA tinha 0,10% de margem normal e saía com
# razão de 59,74. Custa 25 das 525 empresas que alcançam 2008.
MARGEM_NORMAL_MINIMA = 0.02


@dataclass(frozen=True)
class Resiliencia:
    symbol: str
    razao: float
    margem_normal: float
    margem_crise: float
    anos_2008: int
    anos_covid: int
    classe: str

    @property
    def confiavel(self) -> bool:
        """Só com a crise de 2008 — ver o porquê no topo do módulo."""
        return self.anos_2008 >= MIN_ANOS_CRISE and self.classe != SEM_EVIDENCIA

    @property
    def crises_medidas(self) -> str:
        partes = []
        if self.anos_2008 >= MIN_ANOS_CRISE:
            partes.append("2008-09")
        if self.anos_covid >= MIN_ANOS_CRISE:
            partes.append("2020")
        return " e ".join(partes) if partes else "nenhuma"


def classificar(razao: float | None, anos_2008: int) -> str:
    """Faixa da resiliência. Sem a crise de 2008 → sem evidência, nunca frágil."""
    if razao is None or razao != razao or anos_2008 < MIN_ANOS_CRISE:
        return SEM_EVIDENCIA
    if razao > RAZAO_MAXIMA_PLAUSIVEL:
        return SEM_EVIDENCIA          # artefato, não resiliência — ver a constante
    if razao >= LIMITE_RESILIENTE:
        return RESILIENTE
    if razao <= LIMITE_FRAGIL:
        return FRAGIL
    return INTERMEDIARIO


def montar(bruto: Mapping[str, Mapping]) -> dict[str, Resiliencia]:
    """Converte o retorno do loader em vereditos.

    bruto: {symbol: {razao, margem_normal, margem_crise, anos_2008, anos_covid}}
    """
    saida: dict[str, Resiliencia] = {}
    for symbol, d in (bruto or {}).items():
        s = str(symbol).upper()
        anos_2008 = int(d.get("anos_2008") or 0)
        razao = d.get("razao")
        saida[s] = Resiliencia(
            symbol=s,
            razao=float(razao) if razao is not None else float("nan"),
            margem_normal=float(d.get("margem_normal") or 0.0),
            margem_crise=float(d.get("margem_crise") or 0.0),
            anos_2008=anos_2008,
            anos_covid=int(d.get("anos_covid") or 0),
            classe=classificar(razao, anos_2008),
        )
    return saida


def cobertura(medidas: Mapping[str, Resiliencia]) -> dict:
    """Quanto do conjunto tem veredito — para a tela declarar a base.

    Sem isto, o usuário veria uma tabela curta e suporia que as ausentes são
    normais, quando na verdade elas simplesmente não alcançam 2008.
    """
    total = len(medidas)
    confiaveis = sum(1 for m in medidas.values() if m.confiavel)
    so_covid = sum(1 for m in medidas.values()
                   if not m.confiavel and m.anos_covid >= MIN_ANOS_CRISE)
    return {
        "total": total,
        "com_veredito": confiaveis,
        "so_covid": so_covid,
        "sem_crise": total - confiaveis - so_covid,
    }

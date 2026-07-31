"""Duas dimensões de risco que a taxonomia setorial sozinha não expõe.

**1. Resiliência medida.** ``classify_cycle`` lê o setor da B3, e setor é rótulo
estrutural: não sabe que ~40% da receita da Mahle vem de reposição, nem que a
WEG atravessou 2015-16 e 2020 sem perder margem. Aqui a resiliência é MEDIDA no
histórico da própria empresa — margem operacional mediana nos anos de recessão
contra a dos anos normais.

Por que ela NÃO sobrescreve o teto de cíclicos, e isto é decisão de projeto:
são apenas duas recessões (2015-16 e 2020), e a de 2020 teve formato atípico —
derrubou serviço e favoreceu exportador. O efeito aparece na medição por setor
(30/07/2026): **Materiais Básicos marca 0,89 e Utilidade Pública 0,87**, ou
seja, commodity mediria como mais defensiva que saneamento. Deixar isso
reclassificar afrouxaria justamente a proteção contra concentração em
commodity, que é o motivo de o teto existir. Evidência fina demais para
derrubar estrutura, boa o bastante para informar — então informa.

O que a medição de fato desmentiu, e não era o que se esperava: LEVE3 marca
0,59, PIOR que a mediana do seu próprio setor (0,78) — o colchão de reposição
não aparece nos números de crise. Quem contraria o rótulo é WEGE3 (0,85) e
SHUL4 (0,89), cíclicas por taxonomia e defensivas no histórico.

**2. Dependência regulatória.** Nenhum indicador contábil enxerga o risco de uma
decisão de tarifa, de marco setorial ou de acionista controlador estatal. Numa
carteira real de 30/07/2026, PETR4 + SBSP3 + ISAE4 somavam ~30% — três teses que
dependem de governo, num ano eleitoral, e nada na tela dizia isso.

Puro (sem banco, sem rede). Coberto por tests/test_b3_cycle_evidence.py.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

VERSION = "b3-cycle-evidence-1.0.0"

# Recessões brasileiras com demonstração anual publicada e cobertura ampla no
# banco. 2015-16 é recessão clássica de demanda; 2020 é choque de oferta e
# serviços. Formatos diferentes de propósito: uma empresa resiliente nas DUAS
# tem evidência melhor do que a resiliente numa só.
ANOS_DE_CRISE: tuple[int, ...] = (2015, 2016, 2020)

# Faixas da resiliência (margem na crise ÷ margem normal). Calibradas contra
# casos conhecidos medidos em 30/07/2026: AZUL4 -1,07 e GGBR4 -0,53 (margem
# virou negativa), PETR4 0,27, LEVE3 0,59, WEGE3 0,85, SBSP3 0,93, ABEV3 1,31,
# ISAE4 1,50 (margem SUBIU — assinatura de receita regulada).
RESILIENTE = "resiliente"          # segurou a margem
INTERMEDIARIO = "intermediario"
FRAGIL = "fragil"                  # margem desabou
SEM_EVIDENCIA = "sem_evidencia"

LIMITE_RESILIENTE = 0.80
LIMITE_FRAGIL = 0.40

# Mínimo de anos de crise medidos. Com um só ano a leitura confunde o ciclo com
# um evento isolado da empresa (greve, incêndio, aquisição).
MIN_ANOS_CRISE = 2


# ── Dependência regulatória ───────────────────────────────────────────────────
# Curada e explícita, como SETORES_CICLICOS. Critério: a RECEITA ou o PREÇO
# dependem de decisão de agência reguladora.
SETORES_REGULADOS = frozenset({
    "utilidade pública", "utilidade publica",     # ANEEL, ARSESP, agências estaduais
    "comunicações", "comunicacoes",               # ANATEL
})

# Controle ESTATAL é atributo da empresa, não do setor, e o schema não o guarda.
# Lista curada e deliberadamente curta: só companhias cujo controlador é a União
# ou um estado, onde a política de preços já foi instrumento de governo. Pode
# ficar desatualizada — por isso o sinal é de ATENÇÃO, nunca de veto, e a origem
# aparece na tela.
ESTATAIS = frozenset({
    "PETR3", "PETR4",          # União
    "BBAS3",                   # União
    "CMIG3", "CMIG4",          # Minas Gerais
    "CPLE3", "CPLE5", "CPLE6",  # Paraná
    "SAPR3", "SAPR4", "SAPR11",  # Paraná
    "CSMG3",                   # Minas Gerais
    "ELET3", "ELET6",          # capital pulverizado desde 2022, União ainda relevante
    "BRSR3", "BRSR5", "BRSR6",  # Rio Grande do Sul
    "TRPL3", "TRPL4",          # ISA CTEEP, controle privado; concessão federal
})

REGULADO_TARIFA = "tarifa"
REGULADO_ESTATAL = "estatal"
LIVRE = "livre"


@dataclass(frozen=True)
class Resiliencia:
    """Comportamento medido de UMA empresa nas recessões."""
    ticker: str
    razao: float                  # margem_crise / margem_normal
    margem_normal: float
    margem_crise: float
    anos_medidos: int
    classe: str

    @property
    def confiavel(self) -> bool:
        return self.anos_medidos >= MIN_ANOS_CRISE and self.classe != SEM_EVIDENCIA


def classificar_resiliencia(razao: float | None, anos_medidos: int) -> str:
    """Faixa da resiliência. Sem anos suficientes → sem evidência, nunca frágil."""
    if razao is None or razao != razao or anos_medidos < MIN_ANOS_CRISE:
        return SEM_EVIDENCIA
    if razao >= LIMITE_RESILIENTE:
        return RESILIENTE
    if razao <= LIMITE_FRAGIL:
        return FRAGIL
    return INTERMEDIARIO


def classify_regulation(setor: object, ticker: object = None) -> str:
    """``tarifa`` | ``estatal`` | ``livre``.

    Estatal tem precedência: quando o controlador é governo, o risco de política
    de preços não some por a empresa atuar em setor livre — foi exatamente o
    caso da Petrobras.
    """
    tk = str(ticker or "").upper().replace(".SA", "")
    if tk and tk in ESTATAIS:
        return REGULADO_ESTATAL
    if str(setor or "").strip().lower() in SETORES_REGULADOS:
        return REGULADO_TARIFA
    return LIVRE


def peso_regulado(pesos: Mapping[str, float],
                  setores: Mapping[str, str]) -> tuple[float, dict[str, str]]:
    """Fração do PESO da carteira exposta a decisão de governo, e o mapa por ticker.

    Peso, não contagem: é o mesmo erro já corrigido no card de cíclicos — a
    concentração que importa é a do dinheiro, não a do número de nomes.
    """
    total = sum(float(p or 0.0) for p in pesos.values())
    mapa = {str(t).upper(): classify_regulation(setores.get(str(t).upper()), t)
            for t in pesos}
    if total <= 0:
        return 0.0, mapa
    exposto = sum(float(pesos[t] or 0.0) for t in pesos
                  if mapa[str(t).upper()] != LIVRE)
    return exposto / total, mapa


def divergencias_de_ciclo(
    classes_taxonomia: Mapping[str, str],
    resiliencias: Mapping[str, Resiliencia],
) -> list[tuple[str, str, Resiliencia]]:
    """Onde o rótulo setorial e o histórico discordam, para EXIBIR.

    Devolve (ticker, classe da taxonomia, resiliência medida) apenas nos casos
    confiáveis e de discordância forte: cíclica que segurou a margem, ou
    defensiva que desabou. Não altera nada — a decisão de peso segue na
    taxonomia, pelo motivo no topo do módulo.
    """
    saida: list[tuple[str, str, Resiliencia]] = []
    for ticker in sorted(classes_taxonomia):          # ordenação total
        classe = str(classes_taxonomia[ticker])
        r = resiliencias.get(str(ticker).upper())
        if r is None or not r.confiavel:
            continue
        if classe == "ciclico" and r.classe == RESILIENTE:
            saida.append((ticker, classe, r))
        elif classe == "defensivo" and r.classe == FRAGIL:
            saida.append((ticker, classe, r))
    return saida

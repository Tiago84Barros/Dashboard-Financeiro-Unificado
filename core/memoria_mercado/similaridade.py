"""Fator de Similaridade do Cenário: 0 a 100, e o que ele NÃO significa.

A pergunta que este módulo responde é "o mundo de hoje se parece com o mundo em
que aqueles oito eventos aconteceram?". Ele **não** responde "a estimativa está
certa" -- similaridade 90 sobre uma amostra de 8 eventos continua sendo uma
amostra de 8 eventos. As duas coisas viajam separadas até o fim (ver
:mod:`core.memoria_mercado.estimativa`), porque juntá-las num só número é
exatamente o "impacto de 72%" que o requisito do Motor Conjuntural proibiu.

Catorze dimensões, e nenhuma obrigatória
----------------------------------------
O requisito lista as dimensões "conforme disponibilidade". A disciplina do
repositório para isso já está escrita: **ausente fica fora da média, nunca entra
como zero**. Uma dimensão não medida com peso zero é neutra; com valor 0,0 ela é
punitiva, e puniria justamente o ativo com pior cobertura de dados -- o defeito
de ``memoria: medicao-que-pune-a-evidencia``.

Por isso toda saída traz ``cobertura``: a fração do peso total efetivamente
medida. Similaridade 80 com cobertura 0,25 e similaridade 80 com cobertura 0,95
são afirmações muito diferentes, e a segunda linha da saída é a que diz qual das
duas é.

Escalas declaradas, não escolhidas no olho
------------------------------------------
Cada dimensão numérica compara *hoje* com *então* e converte a distância em
similaridade com uma escala própria, em :data:`ESCALAS`. A escala é a distância
na qual a similaridade daquela dimensão cai a zero. Elas são priores
declaradas, não medições -- e é por isso que :mod:`core.memoria_mercado.calibracao`
existe: o requisito diz "não fixe pesos arbitrários como definitivos", e a saída
de :func:`calcular` carrega ``pesos_calibrados`` dizendo se os pesos em uso vêm
de evidência ou ainda são o prior.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

# ── as catorze dimensões do requisito ────────────────────────────────────────
DIM_TIPO_EVENTO = "tipo_evento"
DIM_INTENSIDADE = "intensidade_evento"
DIM_JUROS_BR = "juros_br"
DIM_JUROS_US = "juros_us"
DIM_INFLACAO = "inflacao"
DIM_CAMBIO = "cambio"
DIM_COMMODITY = "commodity"
DIM_VALUATION = "valuation"
DIM_ENDIVIDAMENTO = "endividamento"
DIM_EXPECTATIVA_LUCRO = "expectativa_lucro"
DIM_LIQUIDEZ = "liquidez"
DIM_VOLATILIDADE = "volatilidade"
DIM_POLITICO_REGULATORIO = "politico_regulatorio"
DIM_SETORIAL = "situacao_setorial"
DIM_JA_PRECIFICADO = "parcela_ja_precificada"

DIMENSOES = (
    DIM_TIPO_EVENTO, DIM_INTENSIDADE, DIM_JUROS_BR, DIM_JUROS_US, DIM_INFLACAO,
    DIM_CAMBIO, DIM_COMMODITY, DIM_VALUATION, DIM_ENDIVIDAMENTO,
    DIM_EXPECTATIVA_LUCRO, DIM_LIQUIDEZ, DIM_VOLATILIDADE,
    DIM_POLITICO_REGULATORIO, DIM_SETORIAL, DIM_JA_PRECIFICADO,
)

#: Pesos-prior. O tipo do evento pesa mais do que qualquer variável macro porque
#: comparar uma fusão com uma fusão sob juros diferentes ainda é comparar fusões,
#: enquanto comparar uma fusão com um pedido de recuperação judicial sob os
#: mesmos juros não compara nada. Somam 1,00.
PESOS_PRIOR: dict[str, float] = {
    DIM_TIPO_EVENTO: 0.20,
    DIM_INTENSIDADE: 0.10,
    DIM_JUROS_BR: 0.07,
    DIM_JUROS_US: 0.05,
    DIM_INFLACAO: 0.05,
    DIM_CAMBIO: 0.05,
    DIM_COMMODITY: 0.04,
    DIM_VALUATION: 0.08,
    DIM_ENDIVIDAMENTO: 0.07,
    DIM_EXPECTATIVA_LUCRO: 0.06,
    DIM_LIQUIDEZ: 0.05,
    DIM_VOLATILIDADE: 0.06,
    DIM_POLITICO_REGULATORIO: 0.04,
    DIM_SETORIAL: 0.04,
    DIM_JA_PRECIFICADO: 0.04,
}

#: Distância na qual cada dimensão numérica zera. Unidades: juros e inflação em
#: pontos percentuais ao ano; câmbio, commodity, valuation, endividamento,
#: expectativa de lucro, liquidez e volatilidade em variação relativa (0,50 =
#: 50% de diferença); já precificado em fração.
ESCALAS: dict[str, float] = {
    DIM_INTENSIDADE: 1.00,
    DIM_JUROS_BR: 8.00,
    DIM_JUROS_US: 4.00,
    DIM_INFLACAO: 6.00,
    DIM_CAMBIO: 0.50,
    DIM_COMMODITY: 0.60,
    DIM_VALUATION: 0.60,
    DIM_ENDIVIDAMENTO: 1.50,
    DIM_EXPECTATIVA_LUCRO: 0.50,
    DIM_LIQUIDEZ: 1.00,
    DIM_VOLATILIDADE: 0.60,
    DIM_JA_PRECIFICADO: 1.00,
}

#: Abaixo desta cobertura a similaridade é publicada, mas não sustenta ajuste:
#: comparar cenários por 20% do peso é comparar quase nada.
COBERTURA_MINIMA = 0.40

#: Abaixo desta similaridade a comparação histórica é declarada inválida. Não é
#: "similaridade baixa, cuidado": é "estes eventos não são comparáveis com o de
#: agora", e a estimativa não sai.
SIMILARIDADE_INVALIDANTE = 25.0


@dataclass(frozen=True)
class Dimensao:
    """Uma dimensão comparada, com o par que gerou o número.

    ``valor`` é ``None`` quando a dimensão não pôde ser medida -- e nesse caso
    ela sai da média em vez de entrar como zero. ``motivo`` diz por quê, para a
    tela conseguir listar o que faltou em vez de só mostrar uma cobertura baixa.
    """

    chave: str
    valor: float | None          # 0..1
    peso: float
    hoje: object = None
    historico: object = None
    motivo: str | None = None

    @property
    def medida(self) -> bool:
        return self.valor is not None


@dataclass(frozen=True)
class Similaridade:
    """O fator, a cobertura e as catorze linhas que o produziram."""

    fator: float | None                    # 0..100
    cobertura: float                       # 0..1
    dimensoes: tuple[Dimensao, ...] = ()
    pesos_calibrados: bool = False
    limitacoes: tuple[str, ...] = ()
    invalidantes: tuple[str, ...] = ()

    @property
    def utilizavel(self) -> bool:
        """Sustenta ajuste da referência histórica ao cenário de hoje."""
        return (self.fator is not None
                and self.cobertura >= COBERTURA_MINIMA
                and not self.invalidantes)

    @property
    def medidas(self) -> tuple[Dimensao, ...]:
        return tuple(d for d in self.dimensoes if d.medida)

    @property
    def ausentes(self) -> tuple[Dimensao, ...]:
        return tuple(d for d in self.dimensoes if not d.medida)

    def valor(self, chave: str) -> float | None:
        for d in self.dimensoes:
            if d.chave == chave:
                return d.valor
        return None

    def texto(self) -> str:
        if self.fator is None:
            return ("similaridade do cenario nao pode ser calculada: nenhuma "
                    "dimensao mensuravel")
        return (f"similaridade do cenario: {self.fator:.0f}/100 "
                f"(cobertura de {self.cobertura * 100:.0f}% das dimensoes)")


def _num(valor) -> float | None:
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    return f if isfinite(f) else None


def _por_distancia(chave: str, hoje, historico) -> tuple[float | None, str | None]:
    """Similaridade linear decrescente: ``1 - |a-b| / escala``, com piso em 0."""
    a, b = _num(hoje), _num(historico)
    if a is None or b is None:
        faltando = "hoje" if a is None else "historico"
        return None, f"valor {faltando} indisponivel"
    escala = ESCALAS.get(chave)
    if not escala or escala <= 0:
        return None, "escala nao definida para esta dimensao"
    return max(0.0, 1.0 - abs(a - b) / escala), None


def _por_razao(chave: str, hoje, historico) -> tuple[float | None, str | None]:
    """Para grandezas de nível (câmbio, múltiplo, volume): compara em variação.

    ``|a/b - 1|`` em vez de ``|a - b|`` porque um dólar a 5,20 contra 5,00 é
    outra coisa que um múltiplo 5,20 contra 5,00. Comparar níveis absolutos
    entre grandezas de unidades diferentes é como somar reais com vezes-lucro.
    """
    a, b = _num(hoje), _num(historico)
    if a is None or b is None:
        faltando = "hoje" if a is None else "historico"
        return None, f"valor {faltando} indisponivel"
    if b == 0:
        return None, "referencia historica zero: razao indefinida"
    escala = ESCALAS.get(chave)
    if not escala or escala <= 0:
        return None, "escala nao definida para esta dimensao"
    return max(0.0, 1.0 - abs(a / b - 1.0) / escala), None


def _por_rotulo(hoje, historico) -> tuple[float | None, str | None]:
    """Categórica: igual vale 1, diferente vale 0, ausente vale ``None``."""
    if hoje is None or historico is None:
        return None, "classificacao indisponivel"
    a, b = str(hoje).strip().lower(), str(historico).strip().lower()
    if not a or not b:
        return None, "classificacao vazia"
    return (1.0 if a == b else 0.0), None


#: Como cada dimensão é comparada. Explicitar isto numa tabela, em vez de num
#: encadeamento de ``if``, é o que permite acrescentar uma dimensão sem
#: reescrever :func:`calcular` -- e é o que permite a calibração mexer só nos
#: pesos sem tocar na forma de comparar.
COMPARADORES: dict[str, str] = {
    DIM_TIPO_EVENTO: "rotulo",
    DIM_POLITICO_REGULATORIO: "rotulo",
    DIM_SETORIAL: "rotulo",
    DIM_INTENSIDADE: "distancia",
    DIM_JUROS_BR: "distancia",
    DIM_JUROS_US: "distancia",
    DIM_INFLACAO: "distancia",
    DIM_JA_PRECIFICADO: "distancia",
    DIM_CAMBIO: "razao",
    DIM_COMMODITY: "razao",
    DIM_VALUATION: "razao",
    DIM_ENDIVIDAMENTO: "razao",
    DIM_EXPECTATIVA_LUCRO: "razao",
    DIM_LIQUIDEZ: "razao",
    DIM_VOLATILIDADE: "razao",
}


def calcular(cenario_hoje: dict, cenario_historico: dict, *,
             pesos: dict[str, float] | None = None,
             pesos_calibrados: bool = False) -> Similaridade:
    """Compara dois cenários dimensão a dimensão.

    Os dois dicionários usam as chaves ``DIM_*``. Chave ausente em qualquer um
    dos lados torna a dimensão não medida -- e o peso dela é retirado do
    denominador, não creditado nem debitado.

    O ``cenario_historico`` é normalmente o **cenário médio da amostra**, não o
    de um evento só. Passar o de um evento isolado funciona, mas reintroduz por
    outra porta o viés que :mod:`core.memoria_mercado.amostra` existe para
    fechar.
    """
    pesos = dict(pesos or PESOS_PRIOR)
    hoje = dict(cenario_hoje or {})
    antes = dict(cenario_historico or {})

    linhas: list[Dimensao] = []
    for chave in DIMENSOES:
        peso = float(pesos.get(chave, 0.0))
        forma = COMPARADORES.get(chave, "distancia")
        if forma == "rotulo":
            valor, motivo = _por_rotulo(hoje.get(chave), antes.get(chave))
        elif forma == "razao":
            valor, motivo = _por_razao(chave, hoje.get(chave), antes.get(chave))
        else:
            valor, motivo = _por_distancia(chave, hoje.get(chave),
                                           antes.get(chave))
        linhas.append(Dimensao(chave=chave, valor=valor, peso=peso,
                               hoje=hoje.get(chave), historico=antes.get(chave),
                               motivo=motivo))

    peso_total = sum(d.peso for d in linhas)
    peso_medido = sum(d.peso for d in linhas if d.medida)
    cobertura = (peso_medido / peso_total) if peso_total > 0 else 0.0

    limitacoes: list[str] = []
    invalidantes: list[str] = []

    if peso_medido <= 0:
        limitacoes.append(
            "nenhuma dimensao do cenario pode ser comparada: fator de "
            "similaridade nao calculado")
        return Similaridade(fator=None, cobertura=0.0, dimensoes=tuple(linhas),
                            pesos_calibrados=pesos_calibrados,
                            limitacoes=tuple(limitacoes),
                            invalidantes=("cenario atual sem dados",))

    # Renormalização sobre o peso MEDIDO -- a regra central do módulo.
    fator = 100.0 * sum(d.valor * d.peso for d in linhas if d.medida) / peso_medido

    ausentes = [d.chave for d in linhas if not d.medida and d.peso > 0]
    if ausentes:
        limitacoes.append(
            f"dimensoes nao comparadas ({len(ausentes)} de {len(linhas)}): "
            + ", ".join(sorted(ausentes)))
    if cobertura < COBERTURA_MINIMA:
        limitacoes.append(
            f"cobertura de {cobertura * 100:.0f}% das dimensoes, abaixo do "
            f"minimo de {COBERTURA_MINIMA * 100:.0f}%: similaridade publicada "
            "como indicativa, sem ajustar a referencia historica")
    if not pesos_calibrados:
        limitacoes.append(
            "pesos das dimensoes ainda sao os priores declarados, nao "
            "calibrados por evidencia")

    tipo = next(d for d in linhas if d.chave == DIM_TIPO_EVENTO)
    if tipo.medida and tipo.valor == 0.0:
        invalidantes.append(
            "os eventos historicos sao de tipo diferente do evento atual")
    if fator < SIMILARIDADE_INVALIDANTE:
        invalidantes.append(
            f"similaridade de {fator:.0f}/100 abaixo do minimo de "
            f"{SIMILARIDADE_INVALIDANTE:.0f}: cenarios nao comparaveis")

    return Similaridade(
        fator=round(fator, 1),
        cobertura=round(cobertura, 4),
        dimensoes=tuple(linhas),
        pesos_calibrados=pesos_calibrados,
        limitacoes=tuple(limitacoes),
        invalidantes=tuple(invalidantes),
    )


def cenario_medio(cenarios) -> dict:
    """Cenário representativo de uma amostra: mediana por dimensão numérica.

    Mediana, e não média, pela mesma razão de
    :mod:`core.memoria_mercado.amostra`: um episódio de hiperinflação numa
    amostra de dez desloca a média do cenário e não desloca a mediana. Para
    dimensões categóricas, vale a moda; empate resolve pela ordem alfabética,
    para o resultado não depender da ordem de chegada -- ``memoria:
    determinismo-carteira-b3``.
    """
    lista = [dict(c or {}) for c in (cenarios or ())]
    if not lista:
        return {}

    saida: dict[str, object] = {}
    for chave in DIMENSOES:
        if COMPARADORES.get(chave) == "rotulo":
            rotulos = [str(c[chave]).strip().lower() for c in lista
                       if c.get(chave) not in (None, "")]
            if not rotulos:
                continue
            contagem: dict[str, int] = {}
            for r in rotulos:
                contagem[r] = contagem.get(r, 0) + 1
            saida[chave] = min(sorted(contagem), key=lambda r: (-contagem[r], r))
            continue
        valores = sorted(v for v in (_num(c.get(chave)) for c in lista)
                         if v is not None)
        if not valores:
            continue
        meio = len(valores) // 2
        saida[chave] = (valores[meio] if len(valores) % 2
                        else (valores[meio - 1] + valores[meio]) / 2.0)
    return saida

"""Verificação de ancoragem numérica das respostas da LLM.

A auditoria percentual de 2026-07 classificou "validação da saída" em 55%: os
prompts exigem usar só os números do contexto, mas nada verificava se a resposta
cumpriu. Este módulo faz essa checagem de forma determinística e offline.

Ideia: toda afirmação numérica de uma resposta financeira deve ser rastreável ao
contexto que a originou — seja porque o número aparece lá, seja porque é
derivável dele (soma, diferença, razão, variação percentual). O que não se
ancora é candidato a alucinação e é reportado.

O módulo é puro (sem rede, sem banco, sem LLM) e conservador: na dúvida, trata
o número como ancorado — o objetivo é sinalizar invenção evidente, não reprovar
texto legítimo. Coberto por tests/test_llm_grounding.py.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Número em pt-BR ou en-US: 1.234,56 | 1,234.56 | 1234.56 | 12,5 | 45%
_NUMBER_RE = re.compile(r"-?\d[\d.,]*")

# Escalas textuais comuns em texto financeiro brasileiro.
_SCALES = {
    "mil": 1e3, "mi": 1e6, "milhão": 1e6, "milhao": 1e6, "milhões": 1e6,
    "milhoes": 1e6, "bi": 1e9, "bilhão": 1e9, "bilhao": 1e9, "bilhões": 1e9,
    "bilhoes": 1e9, "tri": 1e12, "trilhão": 1e12, "trilhao": 1e12,
}

# Números que não carregam afirmação factual sobre os dados do usuário:
# anos, contagens pequenas, numeração de listas, percentuais triviais.
_YEAR_RANGE = (1900, 2100)
_SMALL_INT_MAX = 12


@dataclass(frozen=True)
class Claim:
    """Uma afirmação numérica extraída do texto da resposta."""
    value: float
    raw: str
    grounded: bool
    reason: str


@dataclass(frozen=True)
class GroundingReport:
    claims: tuple[Claim, ...] = field(default_factory=tuple)

    @property
    def checked(self) -> int:
        return len(self.claims)

    @property
    def grounded(self) -> int:
        return sum(1 for claim in self.claims if claim.grounded)

    @property
    def ungrounded(self) -> tuple[Claim, ...]:
        return tuple(claim for claim in self.claims if not claim.grounded)

    @property
    def ratio(self) -> float:
        """Fração ancorada em [0,1]. Sem afirmações numéricas, 1,0."""
        return 1.0 if not self.claims else self.grounded / len(self.claims)


def _separador_ambiguo(text: str, sep: str) -> bool:
    """True quando um separador único pode ser milhar OU decimal.

    ``12,500`` vale 12.500 na notação americana e 12,5 na brasileira, e nada no
    token decide. Antes, o ponto tinha essa guarda e a vírgula NÃO: o código
    fazia ``replace(',', '.')`` direto e devolvia 12,5 — **erro de fator 1.000**,
    silencioso. Como a LLM responde ora numa notação ora noutra, um valor real de
    R$ 12.500 virava 12,5, não ancorava, e a resposta correta era acusada de
    inventar dado.

    Três casas decimais é o único caso ambíguo: ``24,8`` e ``1.234,56`` têm 1 e 2
    casas, e valor monetário em real sempre traz 2. Inteiro ``0`` também não é
    ambíguo (``0,500`` é meio, não quinhentos).

    Ambíguo → o chamador devolve None e o número é IGNORADO. Ignorar não cria
    falso positivo nem falso negativo; chutar cria os dois.
    """
    integer, _, fraction = text.partition(sep)
    return len(fraction) == 3 and len(integer) <= 3 and integer != "0"


def parse_number(token: str) -> float | None:
    """Converte um token numérico pt-BR/en-US em float. None se ambíguo."""
    text = token.strip().rstrip(".,")
    if not text or not any(char.isdigit() for char in text):
        return None
    negative = text.startswith("-")
    text = text.lstrip("-")
    if "," in text and "." in text:
        # o separador decimal é o ÚLTIMO a aparecer
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousand_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousand_sep, "").replace(decimal_sep, ".")
    elif text.count(",") > 1:
        text = text.replace(",", "")           # 1,234,567 (milhar en-US)
    elif "," in text:
        if _separador_ambiguo(text, ","):
            return None                        # 12,500 é milhar ou decimal?
        text = text.replace(",", ".")          # 12,5 → decimal pt-BR
    elif text.count(".") > 1:
        text = text.replace(".", "")           # 1.234.567
    elif "." in text:
        if _separador_ambiguo(text, "."):
            return None                        # 1.234 é milhar ou decimal?
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def _scaled(value: float, suffix: str) -> list[float]:
    """Valor e suas leituras com escala textual ('2,5 milhões' → 2.5 e 2.5e6)."""
    factor = _SCALES.get(suffix.lower().strip())
    return [value] if factor is None else [value, value * factor]


def extract_numbers(text: str) -> list[tuple[float, str]]:
    """Todos os números do texto, já com escala textual aplicada."""
    return [(value, raw) for value, raw, _ in extract_numbers_typed(text)]


def extract_numbers_typed(text: str) -> list[tuple[float, str, bool]]:
    """Como ``extract_numbers``, marcando se o número é percentual.

    A unidade importa: uma variação de 780,95% não pode servir de âncora para
    "R$ 780,00". Percentuais só casam com percentuais.
    """
    out: list[tuple[float, str, bool]] = []
    for match in _NUMBER_RE.finditer(text or ""):
        value = parse_number(match.group())
        if value is None:
            continue
        raw = match.group().rstrip(".,")
        tail = (text[match.end():match.end() + 12] or "")
        is_percent = tail.lstrip().startswith("%") or tail.startswith("%")
        suffix = "" if is_percent else (tail.strip().split()[0] if tail.strip() else "")
        for candidate in _scaled(value, suffix.strip(".,")):
            out.append((candidate, raw, is_percent))
    return out


def _is_trivial(value: float) -> bool:
    """Números sem conteúdo factual sobre os dados (anos, itens de lista)."""
    if value != value or math.isinf(value):
        return True
    if float(value).is_integer():
        integer = int(value)
        if _YEAR_RANGE[0] <= integer <= _YEAR_RANGE[1]:
            return True                      # ano
        if abs(integer) <= _SMALL_INT_MAX:
            return True                      # contagem/enumeração
        if integer == 100:
            # Base da conversão para porcentagem. Aparece em toda resposta que
            # mostra a conta ("(3.100 / 12.500) × 100"), e cobrá-la como se
            # fosse dado gerava falso positivo em TODO cálculo de percentual —
            # o caso mais comum do chat. Valor monetário real vem formatado com
            # centavos ("R$ 100,00"), que é outro token.
            return True
    return False


# Piso da folga, mesmo para número escrito com todas as casas. Existe porque
# arredondar ao real mais próximo é legítimo — "cerca de R$ 2.146,00" para
# R$ 2.145,90 erra 0,005% e não é invenção. Já o caso adversarial (R$ 7.777,00
# contra R$ 7.800,00) erra 0,29%, quase sessenta vezes mais. 0,1% separa os dois
# com folga dos dois lados.
_TOLERANCIA_MINIMA = 0.001


def _tolerancia_declarada(raw: str, teto: float) -> float:
    """Folga compatível com a PRECISÃO que o próprio número afirma.

    Uma tolerância fixa de 1% serve à linguagem aproximada ("cerca de 7,8 mil"),
    mas aplicada a um valor escrito com centavos abre 78 reais de folga sobre
    R$ 7.800 — e foi assim que "R$ 7.777,00", inventado do nada, passou como
    ancorado no teste adversarial de 02/08/2026.

    Quem escreve ``7.777,00`` afirma seis dígitos de precisão e deve ser cobrado
    neles; quem escreve ``7,8 mil`` afirma dois e merece a folga. A régua passa a
    sair do texto, não de uma constante.

    Devolve no máximo ``teto`` — o chamador segue no comando do limite superior.
    """
    digitos = "".join(c for c in str(raw or "") if c.isdigit()).lstrip("0")
    significativos = len(digitos) or 1
    return min(teto, max(_TOLERANCIA_MINIMA, 0.5 * 10.0 ** (1 - significativos)))


def _matches(value: float, pool: list[float], *, tolerance: float,
             percent_equivalence: bool = True) -> bool:
    for reference in pool:
        if reference == 0:
            if abs(value) <= tolerance:
                return True
            continue
        if abs(value - reference) <= abs(reference) * tolerance:
            return True
        if not percent_equivalence:
            continue
        # a resposta pode expressar a mesma grandeza em % ou em fração
        if abs(value - reference * 100.0) <= abs(reference * 100.0) * tolerance:
            return True
        if abs(value * 100.0 - reference) <= abs(reference) * tolerance:
            return True
    return False


# Derivação é aritmética exata, não leitura arredondada de uma fonte: exige
# casamento mais apertado. Com a folga de 1% dos valores originais, o conjunto
# de pares somados cobriria quase toda a reta e "ancoraria" números inventados.
DERIVED_TOLERANCE = 0.003


def _derivations(pool: list[float], *, limit: int = 40) -> tuple[list[float], list[float]]:
    """Combinações simples do contexto, separadas por unidade.

    Uma resposta que soma dois gastos ou calcula uma variação não está
    inventando dado — está fazendo a conta que o prompt pediu que fizesse.

    Returns:
        (absolutas, percentuais) — somas/diferenças em valor; razões e
        variações em pontos percentuais. Misturar as duas ancoraria "R$ 780"
        numa variação de 780,95%.

    Só entram grandezas financeiras: anos e contagens (triviais) gerariam
    razões absurdas que ancorariam qualquer coisa.
    """
    values = [value for value in pool if not _is_trivial(value)][:limit]
    absolutes: list[float] = []
    percents: list[float] = []
    for index, first in enumerate(values):
        for second in values[index + 1:]:
            absolutes.extend((first + second, first - second, second - first))
            if second:
                percents.append(first / second * 100.0)
                percents.append((first - second) / abs(second) * 100.0)
            if first:
                percents.append(second / first * 100.0)
                percents.append((second - first) / abs(first) * 100.0)
    return absolutes, percents


def _encadeados(pool: list[float], percentuais: list[float], *,
                limit: int = 60) -> list[float]:
    """Somas, diferenças e ``p%`` sobre valores JÁ ancorados.

    Difere de ``_derivations`` em dois pontos: opera sobre o acumulado da
    resposta (contexto + o que já se sustentou), e conhece "percentual de", que
    é a operação da qual saem as projeções. Devolve só absolutos — percentual de
    percentual não é conta que apareça no chat.
    """
    valores = [v for v in pool if not _is_trivial(v)][-limit:]
    saida: list[float] = []
    for indice, primeiro in enumerate(valores):
        for segundo in valores[indice + 1:]:
            saida.extend((primeiro + segundo, primeiro - segundo,
                          segundo - primeiro))
        for pct in percentuais:
            fatia = primeiro * pct / 100.0
            saida.extend((fatia, primeiro - fatia, primeiro + fatia))
    return saida


def check_grounding(response: str, context: str, *,
                    pergunta: str | None = None,
                    tolerance: float = 0.01,
                    allow_derived: bool = True) -> GroundingReport:
    """Verifica se os números da resposta se ancoram no contexto fornecido.

    Args:
        response: texto devolvido pela LLM.
        context: contexto factual enviado no prompt (os dados do usuário).
        pergunta: texto da pergunta, quando houver. Números que o usuário
            propõe ("cortar 20%") são parâmetros do cenário e ancoram a
            resposta — repetir o que o usuário deu não é inventar dado.
        tolerance: TETO da folga relativa. A folga efetiva sai da precisão que
            cada número declara (ver ``_tolerancia_declarada``).
        allow_derived: aceita somas/diferenças/razões do contexto como ancoradas.

    Returns:
        GroundingReport com uma Claim por número não trivial da resposta.
    """
    # A PERGUNTA ancora tanto quanto o contexto: em "e se eu cortar 20% dos
    # supérfluos?", o 20 é parâmetro do cenário, não afirmação sobre os dados.
    # Cobrá-lo como invenção acusaria o assistente de alucinar por repetir o
    # número que o próprio usuário deu.
    context_pool = [value for value, _ in extract_numbers(context)]
    if pergunta:
        context_pool.extend(value for value, _ in extract_numbers(pergunta))
    derived_abs, derived_pct = (_derivations(context_pool) if allow_derived
                                else ([], []))
    derived_tolerance = min(tolerance, DERIVED_TOLERANCE)
    # Um MESMO valor pode aparecer nas duas leituras dentro da resposta: a LLM
    # escreve "≈ 24,8\%" no texto e "24,8" solto ao fechar a fórmula LaTeX. A
    # versão anterior punha `is_percent` na chave de deduplicação e julgava as
    # duas ocorrências separadamente — a percentual ancorava em derived_pct e a
    # solta era cobrada contra derived_abs, onde não existe. O relatório saía
    # com o mesmo 24,8 aprovado E reprovado, e a resposta (correta) contava como
    # inventada. Medido em 02/08/2026: sozinho, este defeito respondia por
    # metade dos "dados inventados" do golden set.
    #
    # Agora cada VALOR distinto é julgado uma vez, contra as duas leituras que
    # ele de fato assume no texto. Ancorar sob qualquer leitura presente basta.
    # Agrupado pelo TOKEN, não pelo valor: "8,3 mil" produz duas leituras (8,3 e
    # 8.300) e "24,8\%" produz a percentual e a absoluta. São interpretações
    # alternativas da MESMA afirmação — julgar cada uma isolada fazia o mesmo
    # texto sair aprovado numa leitura e reprovado noutra. Ancorar sob qualquer
    # leitura que o token admite basta.
    ocorrencias: dict[str, dict] = {}
    for value, raw, is_percent in extract_numbers_typed(response):
        if _is_trivial(value):
            continue
        registro = ocorrencias.setdefault(
            raw, {"raw": raw, "leituras": [], "percent": False, "abs": False})
        registro["leituras"].append(value)
        registro["percent" if is_percent else "abs"] = True

    # Percentuais CITADOS na resposta, para as operações "p% de X". Sem elas, a
    # conta mais comum de projeção ("cortar 20% dos supérfluos") ficava fora do
    # alcance: 20% de 1.210 = 242 não é soma, diferença nem razão do contexto.
    percentuais = [r["leituras"][0] for r in ocorrencias.values() if r["percent"]]

    # Cadeia: um valor JÁ ANCORADO da resposta pode servir de insumo ao passo
    # seguinte. É como se confere uma conta no papel — 1.210 ancora, 242 sai de
    # 20% dele, 8.058 sai de 8.300 − 242. Só entram valores ancorados, e o passo
    # é registrado com motivo próprio para o relatório não misturar o que veio
    # direto do contexto com o que veio de encadeamento.
    #
    # O custo é real e assumido: cada operação a mais aumenta a chance de um
    # número inventado casar por acaso. Por isso a cadeia parte apenas do que já
    # se sustenta, e não de qualquer número que a resposta cite.
    corrente: list[float] = list(context_pool)

    claims: list[Claim] = []
    for registro in ocorrencias.values():
        raw = registro["raw"]
        leituras = registro["leituras"]
        principal = leituras[0]
        pools: list[list[float]] = []
        if registro["percent"]:
            pools.append(derived_pct)
        if registro["abs"]:
            pools.append(derived_abs)

        tol = _tolerancia_declarada(raw, tolerance)
        tol_derivada = min(derived_tolerance, tol)

        ancora = None
        for leitura in leituras:
            if _matches(leitura, context_pool, tolerance=tol):
                ancora = (leitura, "presente no contexto")
                break
            if any(pool and _matches(leitura, pool, tolerance=tol_derivada,
                                     percent_equivalence=False)
                   for pool in pools):
                ancora = (leitura, "derivado do contexto")
                break
            if allow_derived and registro["abs"] and _matches(
                    leitura, _encadeados(corrente, percentuais),
                    tolerance=tol_derivada, percent_equivalence=False):
                ancora = (leitura, "derivado em cadeia")
                break

        if ancora is None:
            claims.append(Claim(principal, raw, False, "sem âncora no contexto"))
            continue
        valor, motivo = ancora
        claims.append(Claim(valor, raw, True, motivo))
        corrente.append(valor)
    return GroundingReport(tuple(claims))

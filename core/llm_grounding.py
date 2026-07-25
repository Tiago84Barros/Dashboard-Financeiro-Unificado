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

from dataclasses import dataclass, field
import math
import re

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
    elif "," in text:
        # vírgula única: decimal (12,5) ou milhar (1,234)? pt-BR: decimal
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")           # 1.234.567
    elif "." in text:
        integer, _, fraction = text.partition(".")
        if len(fraction) == 3 and len(integer) <= 3 and integer != "0":
            return None                        # 1.234 é ambíguo: milhar ou decimal
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
    return False


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


def check_grounding(response: str, context: str, *,
                    tolerance: float = 0.01,
                    allow_derived: bool = True) -> GroundingReport:
    """Verifica se os números da resposta se ancoram no contexto fornecido.

    Args:
        response: texto devolvido pela LLM.
        context: contexto factual enviado no prompt (os dados do usuário).
        tolerance: folga relativa para arredondamento (1% por padrão).
        allow_derived: aceita somas/diferenças/razões do contexto como ancoradas.

    Returns:
        GroundingReport com uma Claim por número não trivial da resposta.
    """
    context_pool = [value for value, _ in extract_numbers(context)]
    derived_abs, derived_pct = (_derivations(context_pool) if allow_derived
                                else ([], []))
    derived_tolerance = min(tolerance, DERIVED_TOLERANCE)
    seen: set[str] = set()
    claims: list[Claim] = []
    for value, raw, is_percent in extract_numbers_typed(response):
        key = f"{value:.6g}|{raw}|{is_percent}"
        if key in seen:
            continue
        seen.add(key)
        if _is_trivial(value):
            continue
        derived_pool = derived_pct if is_percent else derived_abs
        if _matches(value, context_pool, tolerance=tolerance):
            claims.append(Claim(value, raw, True, "presente no contexto"))
        elif derived_pool and _matches(value, derived_pool,
                                       tolerance=derived_tolerance,
                                       percent_equivalence=False):
            claims.append(Claim(value, raw, True, "derivado do contexto"))
        else:
            claims.append(Claim(value, raw, False, "sem âncora no contexto"))
    return GroundingReport(tuple(claims))

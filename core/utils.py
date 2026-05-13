"""
core/utils.py
Formatadores e helpers de apresentação reutilizados por páginas e componentes.
Sem dependências de Streamlit — testável de forma isolada.
"""


def fmt_moeda(valor: float, simbolo: str = "R$", casas: int = 2) -> str:
    """
    Formata um número como moeda brasileira.
    Exemplo: fmt_moeda(1234.56) → 'R$ 1.234,56'
    """
    if valor is None:
        return f"{simbolo} --"
    formatted = f"{abs(valor):_.{casas}f}".replace(".", ",").replace("_", ".")
    sinal = "-" if valor < 0 else ""
    return f"{sinal}{simbolo} {formatted}"


def fmt_percentual(valor: float, casas: int = 2, sinal: bool = True) -> str:
    """
    Formata um número como percentual.
    Exemplo: fmt_percentual(12.34) → '+12,34%'
             fmt_percentual(-5.0) → '-5,00%'
    """
    if valor is None:
        return "--%"
    prefixo = "+" if sinal and valor > 0 else ""
    return f"{prefixo}{valor:.{casas}f}%".replace(".", ",")


def cor_valor(valor: float) -> str:
    """
    Retorna a cor semântica para um valor numérico.
    Positivo → 'green', negativo → 'red', neutro → 'gray'.
    """
    if valor is None or valor == 0:
        return "gray"
    return "green" if valor > 0 else "red"


def fmt_numero_curto(valor: float, simbolo: str = "") -> str:
    """
    Abrevia números grandes com sufixo K/M.
    Exemplo: fmt_numero_curto(1250000) → '1,25M'
             fmt_numero_curto(850000)  → '850,0K'
    """
    if valor is None:
        return "--"
    prefixo = simbolo + " " if simbolo else ""
    sinal = "-" if valor < 0 else ""
    v = abs(valor)
    if v >= 1_000_000:
        return f"{sinal}{prefixo}{v / 1_000_000:.2f}M".replace(".", ",")
    if v >= 1_000:
        return f"{sinal}{prefixo}{v / 1_000:.1f}K".replace(".", ",")
    return f"{sinal}{prefixo}{v:.2f}".replace(".", ",")


def delta_str(valor_atual: float, valor_anterior: float, fmt: str = "percentual") -> tuple:
    """
    Calcula variação entre dois valores e retorna (texto_delta, positivo).
    fmt: 'percentual' ou 'moeda'
    """
    if not valor_anterior:
        return None, None
    diff = valor_atual - valor_anterior
    pct = (diff / abs(valor_anterior)) * 100
    positivo = diff >= 0
    if fmt == "percentual":
        texto = fmt_percentual(pct)
    else:
        texto = fmt_moeda(diff)
    return texto, positivo

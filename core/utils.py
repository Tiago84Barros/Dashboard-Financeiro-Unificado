"""
core/utils.py
Formatadores e helpers de apresentação reutilizados por páginas e componentes.
Sem dependências de Streamlit — testável de forma isolada.
"""
import re as _re


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


# ─────────────────────────────────────────────────────────────────────────────
# Cifrão em texto livre de chat
# ─────────────────────────────────────────────────────────────────────────────

_TRECHO_CODIGO = _re.compile(r"```.*?```|``.+?``|`[^`\n]+`", _re.DOTALL)


def escapar_cifrao(texto: str) -> str:
    """Impede que ``R$ 1.000,00 ... R$ 23,92`` vire fórmula LaTeX na tela.

    O ``st.markdown`` do Streamlit entrega ``$...$`` ao KaTeX. Num app em reais
    isso não é caso de borda: qualquer resposta que cite dois valores fecha um
    par de delimitadores, e o trecho entre eles — o valor, o nome do papel, o
    que estiver ali — desaparece do texto e reaparece como matemática. O
    sintoma não parece erro de renderização; parece a LLM tendo escrito outra
    coisa.

    Escapa só fora de código: dentro de crase o cifrão é literal para o
    Markdown e nunca chegou ao KaTeX, então acrescentar a barra ali seria
    inventar um caractere que o usuário veria na tela.

    ``$`` já escapado fica como está — escapar duas vezes imprime a barra.
    """
    if not texto:
        return texto or ""

    def _fora(trecho: str) -> str:
        return _re.sub(r"(?<!\\)\$", r"\\$", trecho)

    saida, fim = [], 0
    for achado in _TRECHO_CODIGO.finditer(texto):
        saida.append(_fora(texto[fim:achado.start()]))
        saida.append(achado.group(0))
        fim = achado.end()
    saida.append(_fora(texto[fim:]))
    return "".join(saida)

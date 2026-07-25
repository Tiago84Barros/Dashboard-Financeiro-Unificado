"""Cálculos determinísticos e regras de suficiência para Tesouro Direto.

O módulo mantém separados:

* retorno de mercado sobre o custo histórico da posição;
* ganho/perda de marcação a mercado (MtM) contra o preço teórico de hoje
  calculado pela taxa contratada na compra;
* imposto estimado, que exige o prazo de cada lote.

Nenhuma função deste módulo emite recomendação automática de compra ou venda.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TesouroMeta:
    tipo: str
    label: str
    ano_referencia: int | None
    papel_ano: str


def tesouro_meta(ticker: str) -> TesouroMeta:
    """Classifica o ticker sem inferir datas que não estejam codificadas nele."""
    codigo = (ticker or "").upper().strip()
    digitos = "".join(c for c in codigo if c.isdigit())
    ano = int(digitos[-4:]) if len(digitos) >= 4 else None

    if codigo.startswith("TSELIC"):
        return TesouroMeta("Selic", "Tesouro Selic", ano, "vencimento")
    if codigo.startswith("TIPCA"):
        return TesouroMeta("IPCA+", "Tesouro IPCA+", ano, "vencimento")
    if codigo.startswith("TPRE"):
        return TesouroMeta("Prefixado", "Tesouro Prefixado", ano, "vencimento")
    if codigo.startswith("TEDUCA"):
        return TesouroMeta("Educa+", "Tesouro Educa+", ano, "conversao")
    return TesouroMeta("Outro", "Tesouro", ano, "referencia")


def retorno_mercado_sobre_custo(valor_mercado: float, valor_custo: float) -> float | None:
    """Retorno decimal de preço/mercado sobre custo; não é MtM isolado."""
    if valor_custo is None or valor_custo <= 0:
        return None
    if valor_mercado is None or valor_mercado < 0:
        return None
    return valor_mercado / valor_custo - 1.0


def ganho_mtm_por_precos(
    preco_mercado_hoje: float | None,
    preco_curva_taxa_compra_hoje: float | None,
) -> float | None:
    """Calcula MtM decimal contra a curva da taxa contratada.

    ``preco_curva_taxa_compra_hoje`` é o preço teórico do mesmo título na data
    de avaliação mantendo a taxa contratada na compra. Sem esse dado, o
    resultado é explicitamente indisponível.
    """
    if preco_mercado_hoje is None or preco_curva_taxa_compra_hoje is None:
        return None
    if preco_mercado_hoje < 0 or preco_curva_taxa_compra_hoje <= 0:
        return None
    return preco_mercado_hoje / preco_curva_taxa_compra_hoje - 1.0


def aliquota_ir_tesouro(dias_corridos: int | None) -> float | None:
    """Alíquota regressiva de IR em decimal, contada por lote liquidado."""
    if dias_corridos is None or dias_corridos < 0:
        return None
    if dias_corridos <= 180:
        return 0.225
    if dias_corridos <= 360:
        return 0.20
    if dias_corridos <= 720:
        return 0.175
    return 0.15


def ganho_liquido_estimado(
    valor_mercado: float,
    valor_custo: float,
    dias_corridos: int | None,
) -> float | None:
    """Ganho após IR estimado; não inclui IOF, custódia ou taxas da instituição."""
    aliquota = aliquota_ir_tesouro(dias_corridos)
    if aliquota is None or valor_custo is None or valor_custo < 0:
        return None
    if valor_mercado is None or valor_mercado < 0:
        return None
    ganho_bruto = valor_mercado - valor_custo
    if ganho_bruto <= 0:
        return ganho_bruto
    return ganho_bruto * (1.0 - aliquota)


def analise_suficiencia_tesouro(tipo: str, mtm: float | None) -> dict[str, str]:
    """Retorna orientação neutra baseada em suficiência, nunca sinal de venda."""
    if tipo == "Selic":
        return {
            "icone": "⚪",
            "label": "SEM SINAL DE TIMING",
            "nivel": "neutro",
            "msg": (
                "Tesouro Selic tem baixa sensibilidade de preço. O retorno acumulado "
                "não deve ser interpretado como ganho de MtM nem como sinal de venda."
            ),
        }
    if tipo == "Educa+":
        return {
            "icone": "🔵",
            "label": "REVISÃO DO OBJETIVO",
            "nivel": "info",
            "msg": (
                "O Educa+ possui data de conversão e fluxo mensal próprio. Qualquer "
                "venda antecipada exige revisar objetivo, carência, taxas e imposto por lote."
            ),
        }
    if mtm is None:
        return {
            "icone": "⚪",
            "label": "MTM INDISPONÍVEL",
            "nivel": "neutro",
            "msg": (
                "A taxa contratada por lote não está disponível. Sem ela, não é possível "
                "isolar o efeito das taxas de mercado nem emitir sinal de venda."
            ),
        }
    return {
        "icone": "🟡",
        "label": "REVISÃO HUMANA",
        "nivel": "alerta",
        "msg": (
            "O MtM foi calculado, mas não há limiares de decisão validados. Considere "
            "imposto, custos, liquidez, objetivo e alternativa de reinvestimento."
        ),
    }

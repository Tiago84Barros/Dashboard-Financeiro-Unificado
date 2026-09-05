"""Política inicial, explícita e revisável de exposição macro por setor."""

from __future__ import annotations

SECTOR_EXPOSURES: tuple[tuple[str, str, str, float, str], ...] = (
    ("b3", "Financeiro", "monetary_policy", 0.45, "margem financeira"),
    ("b3", "Financeiro", "credit_liquidity", 0.55, "qualidade de crédito"),
    ("b3", "Utilidade Pública", "monetary_policy", -0.45, "custo de capital"),
    ("b3", "Materiais Básicos", "economic_activity", 0.45, "demanda cíclica"),
    ("b3", "Materiais Básicos", "commodities", 0.65, "preço de insumos"),
    ("b3", "Petróleo, Gás e Biocombustíveis", "commodities", 0.75, "preço de energia"),
    ("b3", "Petróleo, Gás e Biocombustíveis", "currencies", 0.30, "receita em moeda forte"),
    ("b3", "Consumo Cíclico", "economic_activity", 0.60, "renda e demanda"),
    ("b3", "Consumo Cíclico", "monetary_policy", -0.35, "crédito ao consumidor"),
    ("b3", "Comunicações", "economic_activity", 0.25, "receita recorrente"),
    ("b3", "Bens Industriais", "economic_activity", 0.55, "demanda por capital"),
    ("b3", "Bens Industriais", "monetary_policy", -0.25, "custo de capital"),
    ("b3", "Bens Industriais", "currencies", 0.20, "receita exportadora"),
    ("fii", "papel", "monetary_policy", -0.70, "taxa de desconto"),
    ("fii", "papel", "credit_liquidity", -0.65, "risco de crédito"),
    ("fii", "papel", "inflation", 0.35, "indexação contratual"),
    ("fii", "tijolo", "monetary_policy", -0.60, "taxa de desconto"),
    ("fii", "tijolo", "economic_activity", 0.40, "ocupação e aluguel"),
    ("fii", "hibrido", "monetary_policy", -0.50, "taxa de desconto"),
    ("fii", "fof", "monetary_policy", -0.45, "marcação de cotas"),
    ("us", "Tecnologia", "monetary_policy", -0.60, "duração de fluxos"),
    ("us", "Tecnologia", "economic_activity", 0.35, "investimento corporativo"),
    ("us", "Serviços Financeiros", "monetary_policy", 0.45, "margem financeira"),
    ("us", "Serviços Financeiros", "credit_liquidity", 0.55, "qualidade de crédito"),
    ("us", "Saúde", "economic_activity", 0.15, "demanda defensiva"),
    ("us", "Consumo Cíclico", "economic_activity", 0.60, "renda e demanda"),
    ("us", "Consumo Cíclico", "monetary_policy", -0.35, "crédito ao consumidor"),
    ("us", "Indústria", "economic_activity", 0.50, "ciclo industrial"),
)

DEFAULT_CONFIDENCE = 0.55


def validated_sector_exposures() -> tuple[tuple[str, str, str, float, float, str], ...]:
    """Entrega somente coeficientes válidos para persistência parametrizada."""
    rows = []
    for asset_class, sector, factor, sensitivity, channel in SECTOR_EXPOSURES:
        if not -1 <= sensitivity <= 1:
            raise ValueError("sensibilidade fora do intervalo")
        rows.append((asset_class, sector, factor, sensitivity, DEFAULT_CONFIDENCE, channel))
    return tuple(rows)

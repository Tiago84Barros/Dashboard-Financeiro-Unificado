"""Setor canonico comum a B3, mercado americano e FIIs.

Sem um vocabulario unico, "concentracao por setor" mistura escalas
incompativeis e produz um numero enganoso: a B3 usa os setores economicos
proprios, o mercado americano usa GICS e o FII usa segmento de imovel.

As chaves canonicas sao as mesmas ja usadas em core/empresas.py::_SETOR_LABEL —
o projeto ja tinha um vocabulario, nao criamos outro.

Coberto por tests/test_global_taxonomy.py.
"""
from __future__ import annotations

import unicodedata

SETORES_CANONICOS: tuple[str, ...] = (
    "consumer", "consumer_staples", "energy", "financials", "health_care",
    "industrials", "materials", "other", "real_estate", "technology",
    "telecom", "utilities",
)

# ROTULOS divergem propositalmente de core/empresas.py::_SETOR_LABEL em dois pontos:
#   - consumer: "Consumo Cíclico" (vs "Consumo") — distingue claro de "Consumo Básico"
#     na interface global e responde aos nomes oficiais da B3;
#   - real_estate: "Imóveis" (vs "Imóveis / FII") — correto aqui porque real_estate
#     absorbe REITs americanos, não apenas FIIs. Contexto FII é claro fora deste mapa.
ROTULOS: dict[str, str] = {
    "consumer": "Consumo Cíclico",
    "consumer_staples": "Consumo Básico",
    "energy": "Energia",
    "financials": "Financeiro",
    "health_care": "Saúde",
    "industrials": "Industrial",
    "materials": "Materiais",
    "other": "Outros",
    "real_estate": "Imóveis",
    "technology": "Tecnologia",
    "telecom": "Telecom",
    "utilities": "Utilidades",
}


def _chave(texto) -> str:
    """Minusculo, sem acento e sem espaco nas pontas, para comparar rotulos."""
    bruto = str(texto or "").strip().lower()
    sem_acento = unicodedata.normalize("NFKD", bruto)
    return "".join(c for c in sem_acento if not unicodedata.combining(c))


# Setores economicos da B3 (11 oficiais) -> canonico.
_B3: dict[str, str] = {
    _chave("Petróleo, Gás e Biocombustíveis"): "energy",
    _chave("Materiais Básicos"): "materials",
    _chave("Bens Industriais"): "industrials",
    _chave("Consumo não Cíclico"): "consumer_staples",
    _chave("Consumo Cíclico"): "consumer",
    _chave("Saúde"): "health_care",
    _chave("Tecnologia da Informação"): "technology",
    _chave("Comunicações"): "telecom",
    _chave("Utilidade Pública"): "utilities",
    _chave("Financeiro"): "financials",
    _chave("Outros"): "other",
}

# Setores GICS como o yfinance os devolve -> canonico.
_US: dict[str, str] = {
    _chave("Energy"): "energy",
    _chave("Basic Materials"): "materials",
    _chave("Materials"): "materials",
    _chave("Industrials"): "industrials",
    _chave("Consumer Defensive"): "consumer_staples",
    _chave("Consumer Staples"): "consumer_staples",
    _chave("Consumer Cyclical"): "consumer",
    _chave("Consumer Discretionary"): "consumer",
    _chave("Healthcare"): "health_care",
    _chave("Health Care"): "health_care",
    _chave("Technology"): "technology",
    _chave("Information Technology"): "technology",
    _chave("Communication Services"): "telecom",
    _chave("Utilities"): "utilities",
    _chave("Financial Services"): "financials",
    _chave("Financials"): "financials",
    _chave("Real Estate"): "real_estate",
}

_POR_CLASSE: dict[str, dict[str, str]] = {"b3": _B3, "us": _US}


def setor_canonico(asset_class: str, setor: str | None,
                   segmento: str | None = None) -> str:
    """Traduz o setor da classe para a chave canonica. Desconhecido vira 'other'.

    FII nao usa `setor`/`segmento` para esta decisao: todo FII e exposicao
    imobiliaria. O segmento (tijolo, papel, hibrido) e detalhe de subsetor e
    e preservado a parte, no proprio snapshot. O parametro `segmento` e aceito
    para simetria na interface e uso futuro em subsetores (nao consultado hoje).
    """
    classe = str(asset_class or "").strip().lower()
    if classe == "fii":
        return "real_estate"
    return _POR_CLASSE.get(classe, {}).get(_chave(setor), "other")


def nao_mapeados(linhas: list[dict]) -> list[tuple[str, str]]:
    """Pares (classe, setor) que cairam em 'other' tendo valor preenchido.

    Diagnostico de cobertura: se um setor real da carteira aparece aqui, o mapa
    precisa crescer — 'other' silencioso e o modo de falha a evitar.
    """
    achados: set[tuple[str, str]] = set()
    for linha in linhas:
        classe = str(linha.get("asset_class") or "").strip().lower()
        setor = linha.get("sector")
        if not str(setor or "").strip():
            continue
        if setor_canonico(classe, setor) == "other":
            achados.add((classe, str(setor)))
    return sorted(achados)

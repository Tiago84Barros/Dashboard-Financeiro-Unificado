"""Taxonomia determinística de mercado para a vitrine de FIIs.

``sector``/``segmento`` do provedor frequentemente descreve o ramo do
locatário (alimentação, bancos, tecnologia), não o segmento do fundo. A
classificação usa evidências cadastrais: override curado para casos conhecidos,
termos inequívocos no nome/mandato e, por último, o tipo normalizado.
"""
from __future__ import annotations

import re
import unicodedata

ORDEM_CATEGORIAS_FII = (
    "Logística",
    "Shoppings",
    "Renda Urbana",
    "Lajes Corporativas",
    "Residencial",
    "Hotelaria",
    "Saúde",
    "Educacional",
    "Agronegócio",
    "Energia",
    "Desenvolvimento",
    "Tijolo",
    "Papel/CRI",
    "Fundo de Fundos",
    "Híbrido",
    "Não classificado",
)


# Ativos cujo nome público abreviado ou cadastro do provedor não revela o
# segmento convencional. O mapa é deliberadamente pequeno e auditável.
_CATEGORIA_POR_TICKER = {
    # Logística
    "BRCO11": "Logística", "BTLG11": "Logística", "HGLG11": "Logística",
    "LVBI11": "Logística", "PATL11": "Logística", "RBRL11": "Logística",
    "RZZR11": "Logística", "VILG11": "Logística", "XPLG11": "Logística",
    # Shoppings
    "ABCP11": "Shoppings", "ATSA11": "Shoppings", "FIGS11": "Shoppings",
    "FVPQ11": "Shoppings", "HGBS11": "Shoppings", "HSML11": "Shoppings",
    "MALL11": "Shoppings", "VISC11": "Shoppings", "XPML11": "Shoppings",
    # Renda urbana
    "CXAG11": "Renda Urbana", "FIVN11": "Renda Urbana",
    "HGRU11": "Renda Urbana", "RBVA11": "Renda Urbana",
    "TRXF11": "Renda Urbana",
    # Lajes corporativas
    "BRCR11": "Lajes Corporativas", "JSRE11": "Lajes Corporativas",
    "PVBI11": "Lajes Corporativas", "VINO11": "Lajes Corporativas",
    # Outros casos sem rótulo confiável no cadastro atual
    "KNRI11": "Híbrido", "RZTR11": "Agronegócio", "ZAVI11": "Híbrido",
}


_REGRAS_NOME = (
    ("Fundo de Fundos", r"\bfundo de fundos\b|\bfund of funds\b|\bfof(?:ii)?\b"),
    ("Papel/CRI", r"\brecebiveis?\b|\bcredito imobiliario\b|\bcri\b|\bpapeis imobiliarios\b|\bsecurities\b"),
    ("Logística", r"\blogistico\b|\blogistica\b|\bgalpoes?\b|\bindustrial(?:/logistico)?\b"),
    ("Shoppings", r"\bshoppings?\b|\bshopping centers?\b|\bmalls?\b"),
    ("Lajes Corporativas", r"\blajes?\b|\boffices?\b|\bescritorios?\b|\bedificios?\b|\bcorporate towers?\b"),
    ("Residencial", r"\bresidencial\b|\bhabitacoes?\b|\bhousing\b|\bhousi\b|\byuca\b|\bmoradia\b"),
    ("Hotelaria", r"\bhoteis?\b|\bhotelaria\b|\bhotels?\b"),
    ("Saúde", r"\bhospital(?:ar(?:es)?)?\b|\bsaude\b|\bmedical\b|\bmedico hospitalar\b"),
    ("Educacional", r"\beducacao\b|\beducacional\b|\bensino\b|\bfaculdades?\b|\buniversidades?\b"),
    ("Agronegócio", r"\bagronegocio\b|\bagro\b|\bterrax\b|\bfazendas?\b|\brural\b"),
    ("Energia", r"\benergia\b|\bsolar\b|\bfotovoltaica\b"),
    ("Renda Urbana", r"\brenda urbana\b|\bvarejo\b|\bretail\b|\bagencias?\b|\bmax retail\b"),
    ("Desenvolvimento", r"\bdesenvolvimento\b|\bdevelopment\b"),
)


_REGRAS_SEGMENTO = (
    ("Logística", r"\blogistico\b|\blogistica\b|\boperador logistico\b|\barmazenagem\b"),
    ("Shoppings", r"\bshoppings?\b"),
    ("Lajes Corporativas", r"\bescritorios?\b|\blajes?\b|\bcoworking\b"),
    ("Residencial", r"\bresidencial\b|\blocacao de curta estadia\b"),
    ("Hotelaria", r"\bhoteleiro\b|\bhotelaria\b|\bhoteis?\b"),
    ("Saúde", r"\bhospital(?:ar(?:es)?)?\b|\bsaude\b|\bmedicina diagnostica\b|\bmedico hospitalares?\b"),
    ("Educacional", r"\beducacao\b|\bensino\b"),
    ("Agronegócio", r"\bagronegocio\b|\brural\b"),
    ("Energia", r"\benergia\b|\bfotovoltaica\b"),
    # Ramos de ocupantes de imóveis de varejo/serviços colapsam em renda urbana.
    ("Renda Urbana", r"\bagencia bancaria\b|\bbancari[oa]\b|\bvarejista?\b|\bcomercio varejista\b|\blojas?\b|\balimentacao\b|\balimenticios?\b|\balimentos processados\b|\bfood & beverage\b|\bqsr\b"),
)


_CATEGORIA_POR_TIPO = {
    "tijolo": "Tijolo",
    "papel": "Papel/CRI",
    "fof": "Fundo de Fundos",
    "hibrido": "Híbrido",
}


def _texto_normalizado(valor: object) -> str:
    texto = str(valor or "").strip().lower()
    sem_acento = "".join(
        caractere for caractere in unicodedata.normalize("NFKD", texto)
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", sem_acento)


def _primeira_regra(texto: str, regras: tuple[tuple[str, str], ...]) -> str | None:
    for categoria, padrao in regras:
        if re.search(padrao, texto):
            return categoria
    return None


def categoria_fii(
    tipo: object,
    *,
    ticker: object = None,
    segmento: object = None,
    nome: object = None,
    mandato: object = None,
) -> str:
    """Classifica um FII na taxonomia setorial conhecida do mercado.

    Prioridade: ticker curado > nome/mandato > segmento convencional > tipo
    amplo. Não há inferência por métricas financeiras ou LLM.
    """
    ticker_normalizado = str(ticker or "").strip().upper().replace(".SA", "")
    if ticker_normalizado in _CATEGORIA_POR_TICKER:
        return _CATEGORIA_POR_TICKER[ticker_normalizado]

    nome_mandato = _texto_normalizado(f"{nome or ''} {mandato or ''}")
    categoria = _primeira_regra(nome_mandato, _REGRAS_NOME)
    if categoria:
        return categoria

    tipo_normalizado = _texto_normalizado(tipo)
    # Papel e FoF são classes estruturais. Quando o nome não traz evidência
    # mais forte, não podem ser reclassificados pelo ramo de um imóvel/ocupante
    # informado em ``segmento`` (caso real: MXRF11 aparecia como Logística).
    if tipo_normalizado in {"papel", "fof"}:
        return _CATEGORIA_POR_TIPO[tipo_normalizado]

    segmento_normalizado = _texto_normalizado(segmento)
    categoria = _primeira_regra(segmento_normalizado, _REGRAS_SEGMENTO)
    if categoria:
        return categoria

    return _CATEGORIA_POR_TIPO.get(tipo_normalizado, "Não classificado")

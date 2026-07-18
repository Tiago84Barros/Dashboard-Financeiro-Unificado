"""Contrato comum para vitrines de empresas B3 e Estados Unidos.

Mantém a normalização e os filtros fora da interface. Cada linha normalizada
representa um ticker negociável; classes diferentes nunca são consolidadas.
"""
from __future__ import annotations

import re
import unicodedata

import pandas as pd


US_SECTOR_LABELS = {
    "Basic Materials": "Materiais Básicos",
    "Materials": "Materiais Básicos",
    "Communication Services": "Comunicações",
    "Telecommunications": "Comunicações",
    "Consumer Cyclical": "Consumo Cíclico",
    "Consumer Discretionary": "Consumo Cíclico",
    "Consumer Defensive": "Consumo Defensivo",
    "Consumer Staples": "Consumo Defensivo",
    "Energy": "Energia",
    "Financial Services": "Serviços Financeiros",
    "Financials": "Serviços Financeiros",
    "Healthcare": "Saúde",
    "Health Care": "Saúde",
    "Industrials": "Indústria",
    "Real Estate": "Imobiliário",
    "Technology": "Tecnologia",
    "Information Technology": "Tecnologia",
    "Utilities": "Serviços Públicos",
}

_US_INDUSTRY_EXACT = {
    "aerospace & defense": "Aeroespacial e Defesa",
    "airlines": "Companhias Aéreas",
    "airports & air services": "Aeroportos e Serviços Aéreos",
    "aluminum": "Alumínio",
    "asset management": "Gestão de Ativos",
    "auto manufacturers": "Fabricantes de Automóveis",
    "auto parts": "Autopeças",
    "banks - diversified": "Bancos Diversificados",
    "banks - regional": "Bancos Regionais",
    "beverages - brewers": "Cervejarias",
    "beverages - non-alcoholic": "Bebidas Não Alcoólicas",
    "biotechnology": "Biotecnologia",
    "blank checks": "Empresas de Cheque em Branco",
    "building materials": "Materiais de Construção",
    "capital markets": "Mercado de Capitais",
    "chemicals": "Produtos Químicos",
    "communication equipment": "Equipamentos de Comunicação",
    "computer hardware": "Equipamentos de Informática",
    "consumer electronics": "Eletrônicos de Consumo",
    "credit services": "Serviços de Crédito",
    "diagnostics & research": "Diagnósticos e Pesquisa",
    "drug manufacturers - general": "Fabricantes de Medicamentos",
    "drug manufacturers - specialty & generic": "Medicamentos Especializados e Genéricos",
    "electrical equipment & parts": "Equipamentos e Componentes Elétricos",
    "electronic components": "Componentes Eletrônicos",
    "engineering & construction": "Engenharia e Construção",
    "entertainment": "Entretenimento",
    "farm products": "Produtos Agrícolas",
    "financial data & stock exchanges": "Dados Financeiros e Bolsas de Valores",
    "food distribution": "Distribuição de Alimentos",
    "grocery stores": "Supermercados",
    "health information services": "Serviços de Informação em Saúde",
    "healthcare plans": "Planos de Saúde",
    "household & personal products": "Produtos Domésticos e Pessoais",
    "industrial distribution": "Distribuição Industrial",
    "information technology services": "Serviços de Tecnologia da Informação",
    "insurance - diversified": "Seguros Diversificados",
    "insurance - life": "Seguro de Vida",
    "insurance - property & casualty": "Seguros Patrimoniais e de Acidentes",
    "insurance - reinsurance": "Resseguros",
    "insurance - specialty": "Seguros Especializados",
    "insurance brokers": "Corretoras de Seguros",
    "integrated freight & logistics": "Transporte e Logística Integrados",
    "internet content & information": "Conteúdo e Informação na Internet",
    "internet content": "Conteúdo na Internet",
    "internet retail": "Varejo pela Internet",
    "laboratory analytical instruments": "Instrumentos Analíticos de Laboratório",
    "lodging": "Hospedagem",
    "medical devices": "Dispositivos Médicos",
    "medical instruments & supplies": "Instrumentos e Suprimentos Médicos",
    "mortgage finance": "Financiamento Imobiliário",
    "oil & gas drilling": "Perfuração de Petróleo e Gás",
    "oil & gas equipment & services": "Equipamentos e Serviços de Petróleo e Gás",
    "oil & gas exploration & production": "Exploração e Produção de Petróleo e Gás",
    "oil & gas integrated": "Petróleo e Gás Integrados",
    "oil & gas midstream": "Transporte e Armazenamento de Petróleo e Gás",
    "oil & gas refining & marketing": "Refino e Comercialização de Petróleo e Gás",
    "packaged foods": "Alimentos Embalados",
    "packaging & containers": "Embalagens e Recipientes",
    "primary production of aluminum": "Produção Primária de Alumínio",
    "railroads": "Ferrovias",
    "real estate services": "Serviços Imobiliários",
    "recreational vehicles": "Veículos Recreativos",
    "restaurants": "Restaurantes",
    "semiconductor equipment & materials": "Equipamentos e Materiais para Semicondutores",
    "semiconductors": "Semicondutores",
    "software - application": "Software de Aplicação",
    "software - infrastructure": "Software de Infraestrutura",
    "solar": "Energia Solar",
    "specialty retail": "Varejo Especializado",
    "telecom services": "Serviços de Telecomunicações",
    "utilities - diversified": "Serviços Públicos Diversificados",
    "utilities - independent power producers": "Produtores Independentes de Energia",
    "utilities - regulated electric": "Energia Elétrica Regulada",
    "utilities - regulated gas": "Distribuição de Gás Regulada",
    "utilities - regulated water": "Abastecimento de Água Regulado",
    "waste management": "Gestão de Resíduos",
}

_US_INDUSTRY_PHRASES = (
    (r"\breal estate investment trusts?\b", "fundos de investimento imobiliário"),
    (r"\bcrude petroleum and natural gas\b", "petróleo bruto e gás natural"),
    (r"\boil and gas field services\b", "serviços para campos de petróleo e gás"),
    (r"\bcommercial banks?\b", "bancos comerciais"),
    (r"\bsavings institutions?\b", "instituições de poupança"),
    (r"\bcomputer processing and data preparation\b", "processamento de dados"),
    (r"\bprepackaged software\b", "software pronto para uso"),
    (r"\bprinted circuit boards?\b", "placas de circuito impresso"),
    (r"\belectronic components?\b", "componentes eletrônicos"),
    (r"\bmedical instruments?\b", "instrumentos médicos"),
    (r"\bsurgical appliances?\b", "produtos cirúrgicos"),
    (r"\bpharmaceutical preparations?\b", "preparações farmacêuticas"),
    (r"\bbiological products?\b", "produtos biológicos"),
    (r"\bhealth services?\b", "serviços de saúde"),
    (r"\binvestment advice\b", "consultoria de investimentos"),
    (r"\bsecurity brokers? and dealers?\b", "corretoras e distribuidoras de valores"),
    (r"\bmetal mining\b", "mineração de metais"),
    (r"\bbituminous coal\b", "carvão betuminoso"),
    (r"\bnatural gas transmission\b", "transmissão de gás natural"),
    (r"\belectric services?\b", "serviços de energia elétrica"),
    (r"\bwater supply\b", "abastecimento de água"),
    (r"\bsanitary services?\b", "serviços de saneamento"),
    (r"\bdepartment stores?\b", "lojas de departamentos"),
    (r"\beating places?\b", "estabelecimentos de alimentação"),
    (r"\bmotor vehicles?\b", "veículos automotores"),
    (r"\bair transportation\b", "transporte aéreo"),
    (r"\brailroad transportation\b", "transporte ferroviário"),
    (r"\btrucking and courier services?\b", "transporte rodoviário e entregas"),
    (r"\bcommunications?\b", "comunicações"),
    (r"\bbroadcasting\b", "radiodifusão"),
    (r"\bmotion pictures?\b", "produção audiovisual"),
    (r"\bconstruction\b", "construção"),
    (r"\bmanufacturing\b", "fabricação"),
    (r"\bmachinery\b", "máquinas"),
    (r"\bequipment\b", "equipamentos"),
    (r"\binstruments?\b", "instrumentos"),
    (r"\bproducts?\b", "produtos"),
    (r"\bservices?\b", "serviços"),
    (r"\bretail\b", "varejo"),
    (r"\bwholesale\b", "atacado"),
    (r"\bstores?\b", "lojas"),
    (r"\bfood\b", "alimentos"),
    (r"\bbeverages?\b", "bebidas"),
    (r"\bchemicals?\b", "produtos químicos"),
    (r"\bpaper\b", "papel"),
    (r"\blumber\b", "madeira"),
    (r"\bsteel\b", "aço"),
    (r"\bmining\b", "mineração"),
    (r"\binsurance\b", "seguros"),
    (r"\bfinance\b", "finanças"),
    (r"\btransportation\b", "transporte"),
    (r"\bmiscellaneous\b", "diversos"),
    (r"\bgeneral\b", "geral"),
    (r"\bindustrial\b", "industrial"),
    (r"\belectronic\b", "eletrônico"),
    (r"\belectrical\b", "elétrico"),
    (r"\bmedical\b", "médico"),
    (r"\blaboratory\b", "laboratório"),
    (r"\banalytical\b", "analítico"),
    (r"\bprimary\b", "primário"),
    (r"\bproduction\b", "produção"),
    (r"\baluminum\b", "alumínio"),
    (r"\bof\b", "de"),
    (r"\band\b|&", "e"),
)

_US_SECTOR_KEYWORDS = (
    ("Saúde", ("health", "medical", "pharma", "drug", "biolog", "diagnostic",
               "hospital", "nursing", "dental", "surgical", "laboratory", "ophthalmic")),
    ("Imobiliário", ("real estate", "reit", "land subdivider")),
    ("Serviços Financeiros", ("bank", "finance", "financial", "insurance", "credit",
                              "broker", "securities", "investment", "blank check", "commodity")),
    ("Energia", ("petroleum", "natural gas", "oil ", "oil &", "drilling", "coal", "pipeline")),
    ("Serviços Públicos", ("electric services", "gas transmission", "gas distribution",
                           "water supply", "sanitary services", "utilities")),
    ("Tecnologia", ("computer", "software", "semiconductor", "electronic component",
                    "data processing", "information technology")),
    ("Comunicações", ("telecom", "telephone", "communication", "broadcast", "radio",
                      "television", "cable", "motion picture", "media")),
    ("Materiais Básicos", ("mining", "metal", "steel", "aluminum", "chemical", "paper",
                           "lumber", "forestry", "cement", "glass", "mineral")),
    ("Consumo Defensivo", ("food", "beverage", "grocery", "tobacco", "soap", "agricultur",
                           "household", "personal product")),
    ("Consumo Cíclico", ("retail", "restaurant", "hotel", "lodging", "apparel", "footwear",
                         "furniture", "automobile", "motor vehicle", "recreation", "entertainment")),
    ("Indústria", ("manufactur", "machinery", "transport", "aircraft", "railroad", "trucking",
                   "construction", "engineering", "industrial", "equipment", "instruments")),
)

_US_EXCHANGE_TOKENS = ("NASDAQ", "NYSE", "AMEX", "NEW YORK STOCK EXCHANGE")
_NON_EQUITY_TOKENS = (
    "ETF", "FUND", "INDEX", "OPTION", "WARRANT", "RIGHT", "UNIT",
    "NOTE", "BOND", "PREFERRED ETF", "SPAC WARRANT",
)


def _text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _fold(value) -> str:
    normalized = unicodedata.normalize("NFKD", _text(value).casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def us_logo_url(ticker: str) -> str:
    """URL pública; a interface sempre mantém um placeholder por baixo."""
    safe = re.sub(r"[^A-Z0-9-]", "-", _text(ticker).upper().replace(".", "-"))
    return f"https://companiesmarketcap.com/img/company-logos/64/{safe}.png"


def translate_us_industry(value) -> str:
    """Traduz classificações setoriais de provedores americanos para PT-BR."""
    raw = _text(value)
    if not raw:
        return "Não classificada"
    exact = _US_INDUSTRY_EXACT.get(raw.casefold())
    if exact:
        return exact
    translated = raw
    for pattern, replacement in _US_INDUSTRY_PHRASES:
        translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)
    translated = re.sub(r"\s+", " ", translated).strip(" -·")
    return translated[:1].upper() + translated[1:] if translated else "Não classificada"


def translate_us_sector(sector, industry=None) -> str:
    """Consolida classificações SEC/SIC nos setores macro usados pela interface."""
    raw_sector = _text(sector)
    for source, translated in US_SECTOR_LABELS.items():
        if raw_sector.casefold() == source.casefold():
            return translated
    haystack = f"{raw_sector} {_text(industry)}".casefold()
    for translated, keywords in _US_SECTOR_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return translated
    return "Outros setores"


def localize_us_company_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Localiza setor/indústria para exibição sem alterar as métricas do quadro."""
    if df is None or df.empty:
        return df.copy() if df is not None else pd.DataFrame()
    out = df.copy()
    raw_sector = out.get("sector", pd.Series("", index=out.index)).map(_text)
    raw_industry = out.get("industry", pd.Series("", index=out.index)).map(_text)
    out["sector_raw"] = raw_sector
    out["industry_raw"] = raw_industry
    out["sector"] = [translate_us_sector(sector, industry)
                     for sector, industry in zip(raw_sector, raw_industry)]
    out["industry"] = [translate_us_industry(industry or sector)
                       for sector, industry in zip(raw_sector, raw_industry)]
    return out


def is_valid_us_equity(row: pd.Series | dict) -> bool:
    symbol = _text(row.get("symbol")).upper()
    name = _text(row.get("name")).upper()
    exchange = _text(row.get("exchange")).upper()
    security_type = _text(row.get("security_type")).upper()
    active = row.get("is_active", True)
    if not symbol or len(symbol) > 12 or not re.fullmatch(r"[A-Z0-9.-]+", symbol):
        return False
    if active is False or (isinstance(active, str) and active.lower() in {"false", "0", "no"}):
        return False
    if exchange and not any(token in exchange for token in _US_EXCHANGE_TOKENS):
        return False
    haystack = f"{security_type} {name}"
    if any(re.search(rf"\b{re.escape(token)}\b", haystack) for token in _NON_EQUITY_TOKENS):
        return False
    return True


def normalize_us_companies(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "ticker", "company_name", "sector", "sector_raw", "industry", "industry_raw",
        "card_tag", "logo_url", "exchange", "currency", "country",
        "market_cap", "asset_type",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    source = df.copy()
    source = source[source.apply(is_valid_us_equity, axis=1)].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(index=source.index)
    out["ticker"] = source["symbol"].map(_text).str.upper()
    out["company_name"] = source.get("name", source["symbol"]).map(_text)
    out["sector_raw"] = source.get("sector", pd.Series("", index=source.index)).map(_text)
    out["industry_raw"] = source.get("industry", pd.Series("", index=source.index)).map(_text)
    out["sector"] = [translate_us_sector(sector, industry)
                     for sector, industry in zip(out["sector_raw"], out["industry_raw"])]
    out["industry"] = [translate_us_industry(industry or sector)
                       for sector, industry in zip(out["sector_raw"], out["industry_raw"])]
    out["card_tag"] = out.apply(
        lambda row: " · ".join(p for p in (row["sector"], row["industry"]) if p) or "—",
        axis=1,
    )
    if "logo_url" in source:
        out["logo_url"] = source["logo_url"].map(_text)
        out.loc[out["logo_url"] == "", "logo_url"] = out.loc[
            out["logo_url"] == "", "ticker"].map(us_logo_url)
    else:
        out["logo_url"] = out["ticker"].map(us_logo_url)
    out["exchange"] = source.get("exchange", pd.Series("", index=source.index)).map(_text)
    out["currency"] = "USD"
    out["country"] = "Estados Unidos"
    out["market_cap"] = pd.to_numeric(
        source.get("_market_cap", source.get("market_cap", pd.Series(float("nan"), index=source.index))),
        errors="coerce",
    )
    out["asset_type"] = source.get(
        "security_type", pd.Series("Ação", index=source.index)).map(_text).replace({
            "Stock": "Ação", "Common Stock": "Ação ordinária",
            "Preferred Stock": "Ação preferencial", "REIT": "Fundo imobiliário americano",
        })
    return out.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


def normalize_b3_companies(df: pd.DataFrame, logo_builder) -> pd.DataFrame:
    columns = [
        "ticker", "company_name", "sector", "sector_raw", "industry",
        "card_tag", "logo_url", "exchange", "currency", "country",
        "market_cap", "asset_type",
    ]
    if df is None or df.empty:
        return pd.DataFrame(columns=columns)
    source = df.copy()
    out = pd.DataFrame(index=source.index)
    out["ticker"] = source["ticker"].map(_text).str.upper().str.replace(".SA", "", regex=False)
    out["company_name"] = source.get("nome_empresa", source["ticker"]).map(_text)
    out["sector_raw"] = source.get("SETOR", pd.Series("", index=source.index)).map(_text)
    out["sector"] = out["sector_raw"]
    subsetor = source.get("SUBSETOR", pd.Series("", index=source.index)).map(_text)
    segmento = source.get("SEGMENTO", pd.Series("", index=source.index)).map(_text)
    out["industry"] = segmento.where(segmento != "", subsetor)
    out["card_tag"] = [
        " · ".join(p for p in (sub, seg) if p) or "—"
        for sub, seg in zip(subsetor, segmento)
    ]
    out["logo_url"] = out["ticker"].map(logo_builder)
    out["exchange"] = "B3"
    out["currency"] = "BRL"
    out["country"] = "Brazil"
    out["market_cap"] = float("nan")
    out["asset_type"] = "stock"
    return out.drop_duplicates(subset=["ticker"], keep="first").reset_index(drop=True)


def filter_market_companies(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """Busca case/accent-insensitive por ticker, nome, setor ou indústria."""
    if df is None or df.empty or not _text(query):
        return df.copy() if df is not None else pd.DataFrame()
    q = _fold(query)
    mask = pd.Series(False, index=df.index)
    for col in ("ticker", "company_name", "sector", "sector_raw", "industry", "industry_raw"):
        if col in df:
            mask |= df[col].fillna("").map(_fold).str.contains(q, regex=False)
    return df[mask].reset_index(drop=True)

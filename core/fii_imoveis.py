"""
core/fii_imoveis.py
Coleta (best-effort) da carteira de IMÓVEIS de um FII de tijolo/híbrido: nome do
imóvel, área (m²), vacância e localização (cidade/UF/região) — dados que NÃO
existem em fonte estruturada (CVM/BRAPI). Fonte: scraping do Status Invest
(seção "Imóveis/Terrenos"), com Fundamentus como fallback.

A região (Norte/Nordeste/Centro-Oeste/Sudeste/Sul) é derivada da UF — útil para
fundos de logística/tijolo ("em qual região estão os galpões"). A UF vem do
atributo do card quando preenchido; senão é inferida do nome do imóvel (que
costuma trazer "... - RJ" ou "... - São Paulo").

Parsing PURO e testável (parse_imoveis recebe o HTML); o IO fica em fetch_*.
Cobertura varia por fundo e pode quebrar se o HTML mudar — ausência de dado é
tolerada (linhas parciais), nunca levanta exceção para o chamador.
"""
from __future__ import annotations

import re

# UF -> região do IBGE (cobre as 27 unidades federativas).
UF_REGIAO = {
    "AC": "Norte", "AP": "Norte", "AM": "Norte", "PA": "Norte", "RO": "Norte",
    "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste",
    "PB": "Nordeste", "PE": "Nordeste", "PI": "Nordeste", "RN": "Nordeste",
    "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MT": "Centro-Oeste",
    "MS": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}
_UFS = set(UF_REGIAO)

# Nome do estado (normalizado, sem acento/espaço) -> UF. Permite inferir a UF
# quando o imóvel traz "... - São Paulo" em vez da sigla.
_ESTADOS = {
    "Acre": "AC", "Alagoas": "AL", "Amapa": "AP", "Amazonas": "AM", "Bahia": "BA",
    "Ceara": "CE", "Distrito Federal": "DF", "Espirito Santo": "ES", "Goias": "GO",
    "Maranhao": "MA", "Mato Grosso": "MT", "Mato Grosso do Sul": "MS",
    "Minas Gerais": "MG", "Para": "PA", "Paraiba": "PB", "Parana": "PR",
    "Pernambuco": "PE", "Piaui": "PI", "Rio de Janeiro": "RJ",
    "Rio Grande do Norte": "RN", "Rio Grande do Sul": "RS", "Rondonia": "RO",
    "Roraima": "RR", "Santa Catarina": "SC", "Sao Paulo": "SP", "Sergipe": "SE",
    "Tocantins": "TO",
}


def _norm_label(s: str) -> str:
    s = (s or "").upper()
    for a, b in (("Á", "A"), ("À", "A"), ("Â", "A"), ("Ã", "A"), ("É", "E"),
                 ("Ê", "E"), ("Í", "I"), ("Ó", "O"), ("Ô", "O"), ("Õ", "O"),
                 ("Ú", "U"), ("Ç", "C")):
        s = s.replace(a, b)
    return re.sub(r"[^A-Z0-9]", "", s)


_ESTADO_NORM = {_norm_label(k): v for k, v in _ESTADOS.items()}

# Cidades-polo (capitais + hubs de logística/varejo) -> UF, p/ inferir a região
# quando o imóvel é nomeado pela cidade (ex.: "UBERLANDIA", "Itupeva G300").
# Cobertura best-effort; ampliável conforme aparecem novos imóveis.
_CIDADES = {
    # capitais
    "Sao Paulo": "SP", "Rio de Janeiro": "RJ", "Brasilia": "DF", "Salvador": "BA",
    "Fortaleza": "CE", "Belo Horizonte": "MG", "Manaus": "AM", "Curitiba": "PR",
    "Recife": "PE", "Porto Alegre": "RS", "Belem": "PA", "Goiania": "GO",
    "Sao Luis": "MA", "Maceio": "AL", "Natal": "RN", "Teresina": "PI",
    "Joao Pessoa": "PB", "Aracaju": "SE", "Cuiaba": "MT", "Campo Grande": "MS",
    "Florianopolis": "SC", "Vitoria": "ES", "Porto Velho": "RO", "Macapa": "AP",
    "Rio Branco": "AC", "Boa Vista": "RR", "Palmas": "TO",
    # hubs SP
    "Guarulhos": "SP", "Campinas": "SP", "Cajamar": "SP", "Cravinhos": "SP",
    "Louveira": "SP", "Itupeva": "SP", "Embu": "SP", "Embu das Artes": "SP",
    "Maua": "SP", "Rio Claro": "SP", "Atibaia": "SP", "Jundiai": "SP",
    "Hortolandia": "SP", "Piracicaba": "SP", "Sumare": "SP", "Ribeirao Preto": "SP",
    "Sorocaba": "SP", "Sao Bernardo do Campo": "SP", "Sao Bernado": "SP",
    "Santo Andre": "SP", "Osasco": "SP", "Barueri": "SP", "Jandira": "SP",
    "Cabreuva": "SP", "Itaquaquecetuba": "SP", "Guararema": "SP", "Jaguariuna": "SP",
    "Vinhedo": "SP", "Indaiatuba": "SP", "Americana": "SP", "Limeira": "SP",
    "Sao Jose dos Campos": "SP", "Jacarei": "SP", "Taubate": "SP",
    "Pindamonhangaba": "SP", "Diadema": "SP", "Aruja": "SP", "Itapevi": "SP",
    "Pederneiras": "SP", "Bernardino de Campos": "SP",
    # hubs MG
    "Extrema": "MG", "Uberlandia": "MG", "Uberaba": "MG", "Betim": "MG",
    "Contagem": "MG", "Juiz de Fora": "MG", "Pouso Alegre": "MG", "Para de Minas": "MG",
    # hubs RJ
    "Nova Iguacu": "RJ", "Duque de Caxias": "RJ", "Resende": "RJ", "Itaborai": "RJ",
    "Macae": "RJ", "Sao Goncalo": "RJ", "Queimados": "RJ",
    # hubs Sul
    "Canoas": "RS", "Gravatai": "RS", "Caxias do Sul": "RS", "Novo Hamburgo": "RS",
    "Londrina": "PR", "Maringa": "PR", "Sao Jose dos Pinhais": "PR",
    "Joinville": "SC", "Itajai": "SC", "Blumenau": "SC",
    # hubs Nordeste/Centro-Oeste/ES
    "Camacari": "BA", "Simoes Filho": "BA", "Lauro de Freitas": "BA",
    "Feira de Santana": "BA", "Maracanau": "CE", "Jaboatao dos Guararapes": "PE",
    "Cabo de Santo Agostinho": "PE", "Anapolis": "GO", "Senador Canedo": "GO",
    "Aparecida de Goiania": "GO", "Serra": "ES", "Cariacica": "ES", "Vila Velha": "ES",
}
_CIDADE_NORM = {_norm_label(k): v for k, v in _CIDADES.items()}


def regiao_por_uf(uf: str | None) -> str | None:
    """Região do IBGE a partir da sigla da UF (case-insensitive). None se desconhecida."""
    if not uf:
        return None
    return UF_REGIAO.get(str(uf).strip().upper())


def _uf_from_token(token: str | None) -> str | None:
    """UF a partir de uma sigla ('SP') ou nome de estado ('São Paulo')."""
    if not token:
        return None
    t = token.strip()
    if t.upper() in _UFS:
        return t.upper()
    return _ESTADO_NORM.get(_norm_label(t))


def _num(s) -> float | None:
    """Número no formato BR (milhar=ponto, decimal=vírgula), tolera %, m², R$, ícones."""
    if s is None:
        return None
    m = re.search(r"-?\d[\d.]*(?:,\d+)?", str(s))
    if not m:
        return None
    clean = m.group(0).replace(".", "").replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def _pct(s) -> float | None:
    """Percentual -> decimal (7,0% -> 0.07). None se vazio."""
    v = _num(s)
    return v / 100.0 if v is not None else None


def _cidade_uf(txt: str | None) -> str | None:
    """UF a partir de um nome de cidade-polo (tolera sufixos como 'I', 'II', '300')."""
    if not txt:
        return None
    key = _norm_label(txt)
    if key in _CIDADE_NORM:
        return _CIDADE_NORM[key]
    parts = txt.split()
    while parts and re.fullmatch(r"(?:[IVXLC]+|[A-Za-z]?\d+[A-Za-z0-9]*|[A-Za-z])", parts[-1]):
        parts.pop()
        k = _norm_label(" ".join(parts))
        if k in _CIDADE_NORM:
            return _CIDADE_NORM[k]
    return None


def localizar(nome: str | None, uf_attr: str | None = None) -> tuple[str | None, str | None]:
    """
    (cidade, UF) de um imóvel. Prioriza a UF do atributo do card; senão infere do
    nome: 'Cidade/UF', '... - São Paulo', '... - Bangu RJ' ou cidade-polo conhecida
    ('UBERLANDIA', 'Itupeva G300'). cidade só é preenchida quando há confiança.
    """
    uf = uf_attr.strip().upper() if uf_attr and uf_attr.strip().upper() in _UFS else None
    cidade = None
    if not uf and nome:
        if " - " in nome:
            seg = nome.rsplit(" - ", 1)[-1].strip()
            cidade, uf = split_cidade_uf(seg)
            if not uf:
                uf = _cidade_uf(cidade)          # último segmento é uma cidade-polo?
        else:                                    # o nome inteiro pode SER a cidade
            u = _cidade_uf(nome) or _uf_from_token(nome)
            if u:
                cidade, uf = nome.strip(), u
    return cidade, uf


def split_cidade_uf(local: str | None) -> tuple[str | None, str | None]:
    """
    Extrai (cidade, UF) de 'Cidade/UF', 'Cidade - UF', 'Cidade, UF' ou texto com a
    UF/nome de estado ao final. Usado pelo parser genérico (fallback).
    """
    if not local:
        return None, None
    txt = str(local).strip()
    m = re.search(r"[/\-,]\s*([A-Za-z]{2})\s*$", txt) or re.search(r"\b([A-Za-z]{2})\s*$", txt)
    if m and m.group(1).upper() in _UFS:
        uf = m.group(1).upper()
        cidade = txt[:m.start()].strip(" /-,") or None
        return cidade, uf
    u = _uf_from_token(txt)
    if u:
        return None, u
    return (txt or None), None


def _imovel(nome, area=None, vacancia=None, uf=None, cidade=None,
            segmento=None, pct_receita=None) -> dict:
    """Monta o registro normalizado de um imóvel, derivando a região da UF."""
    uf = (str(uf).strip().upper() or None) if uf else None
    uf = uf if uf in _UFS else None
    return {
        "nome_imovel": (str(nome).strip() or None) if nome else None,
        "area_m2": _num(area) if not isinstance(area, (int, float)) else area,
        "vacancia": _pct(vacancia) if not isinstance(vacancia, (int, float)) and vacancia is not None
                    else (vacancia if isinstance(vacancia, (int, float)) else None),
        "cidade": (str(cidade).strip() or None) if cidade else None,
        "uf": uf,
        "regiao": regiao_por_uf(uf),
        "segmento_imovel": (str(segmento).strip() or None) if segmento else None,
        "pct_receita": _pct(pct_receita) if pct_receita is not None else None,
    }


# ── Parser específico do Status Invest (estrutura .property) ───────────────────

def _parse_statusinvest(soup) -> list[dict]:
    """Lê a lista de imóveis da seção 'Imóveis/Terrenos' do Status Invest."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in soup.select("div.property"):
        name_el = p.select_one(".name")
        nome = name_el.get_text(" ", strip=True) if name_el else None
        # descarta nomes-lixo (sem letras, ex.: "0,0700", ou curtos demais)
        if not nome or not re.search(r"[A-Za-zÀ-ÿ]", nome) or len(nome.strip()) < 3:
            continue
        key = nome.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        size_el = p.select_one('strong[title="Tamanho"]')
        area = size_el.get_text(" ", strip=True) if size_el else None
        uf_el = p.select_one("strong.uf")
        uf_attr = uf_el.get_text(strip=True) if uf_el else None
        obj_el = p.select_one(".objective .value")
        vac = None
        for lab in p.select("small.label, .label"):
            if "VAC" in _norm_label(lab.get_text()):
                v = lab.find_next("strong")
                vac = v.get_text(strip=True) if v else None
                break
        cidade, uf = localizar(nome, uf_attr)
        out.append(_imovel(nome, area=area, vacancia=vac, uf=uf, cidade=cidade,
                           segmento=(obj_el.get_text(strip=True) if obj_el else None)))
    return out


# ── Parser genérico (fallback: cards rótulo->valor) ───────────────────────────

_LABEL_MAP = [
    (("AREA", "AREABRUTALOCAVEL", "ABL", "M2"), "area"),
    (("VACANCIA", "VACANCIAFISICA"), "vacancia"),
    (("LOCALIZACAO", "ENDERECO", "CIDADE", "LOCAL", "UF", "ESTADO"), "local"),
    (("PARTICIPACAO", "RECEITA", "PERCENTUALRECEITA"), "pct_receita"),
    (("TIPO", "SEGMENTO", "CATEGORIA"), "segmento"),
]


def _field_for_label(label: str) -> str | None:
    nl = _norm_label(label)
    for keys, field in _LABEL_MAP:
        if any(k in nl for k in keys):
            return field
    return None


def _parse_generic(soup) -> list[dict]:
    """Fallback tolerante: cards com pares rótulo->valor (.title/.value, dt/dd)."""
    section = None
    for tag in soup.find_all(["section", "div"]):
        idc = " ".join(filter(None, [tag.get("id", ""), " ".join(tag.get("class", []))])).lower()
        if "propert" in idc or "imove" in idc or "imóve" in idc:
            section = tag
            break
    scope = section or soup
    out: list[dict] = []
    seen: set[str] = set()
    for card in scope.find_all(["article", "li", "div", "tr"], recursive=True):
        cls = " ".join(card.get("class", [])).lower()
        if not any(k in cls for k in ("card", "property", "imove", "item", "imovel")):
            continue
        rec: dict = {}
        head = (card.find(["h2", "h3", "h4", "h5"]) or card.find(class_=re.compile("title", re.I))
                or card.find("strong"))
        if head:
            rec["nome"] = head.get_text(" ", strip=True)
        for lab in card.find_all(class_=re.compile(r"\b(title|label)\b", re.I)):
            val = lab.find_next_sibling(class_=re.compile(r"\b(value|sub-?value)\b", re.I)) \
                or lab.find_next(class_=re.compile(r"\b(value|sub-?value)\b", re.I))
            field = _field_for_label(lab.get_text(" ", strip=True))
            if field and val is not None and field not in rec:
                rec[field] = val.get_text(" ", strip=True)
        for dt in card.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            field = _field_for_label(dt.get_text(" ", strip=True))
            if field and dd is not None and field not in rec:
                rec[field] = dd.get_text(" ", strip=True)
        nm = (rec.get("nome") or "").strip().lower()
        if not nm or nm in seen:
            continue
        seen.add(nm)
        cidade, uf = split_cidade_uf(rec.get("local"))
        norm = _imovel(rec.get("nome"), area=rec.get("area"), vacancia=rec.get("vacancia"),
                       uf=uf, cidade=cidade, segmento=rec.get("segmento"),
                       pct_receita=rec.get("pct_receita"))
        if norm["nome_imovel"]:
            out.append(norm)
    return out


def parse_imoveis(html: str | bytes) -> list[dict]:
    """
    Extrai a lista de imóveis do HTML da página de FII (best-effort). Tenta a
    estrutura do Status Invest (cards .property) e, se vazia, um parser genérico.
    Retorna lista de dicts: {nome_imovel, area_m2, vacancia, cidade, uf, regiao,
    segmento_imovel, pct_receita}. [] se nada for encontrado.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    soup = BeautifulSoup(html, "html.parser")
    si = _parse_statusinvest(soup)
    return si if si else _parse_generic(soup)


# ── IO ────────────────────────────────────────────────────────────────────────

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _fetch_html(url: str, timeout: int = 20) -> str | None:
    import requests
    try:
        resp = requests.get(url, headers={**_HEADERS, "Referer": url.rsplit("/", 1)[0]},
                            timeout=timeout)
    except Exception:
        return None
    return resp.text if resp.status_code == 200 else None


def fetch_fii_imoveis(ticker: str) -> list[dict]:
    """
    Imóveis de um FII (best-effort). Tenta Status Invest e, se vazio, Fundamentus.
    Marca a fonte em cada registro ('fonte'). [] se nada for encontrado.
    """
    tk = str(ticker).strip().upper().replace(".SA", "")
    for fonte, url in (
        ("statusinvest", f"https://statusinvest.com.br/fundos-imobiliarios/{tk.lower()}"),
        ("fundamentus", f"https://www.fundamentus.com.br/detalhes.php?papel={tk}"),
    ):
        html = _fetch_html(url)
        if not html:
            continue
        imoveis = parse_imoveis(html)
        if imoveis:
            return [{**im, "fonte": fonte} for im in imoveis]
    return []


def vacancia_media(imoveis: list[dict]) -> float | None:
    """Vacância do fundo = média das vacâncias dos imóveis ponderada pela área."""
    num = den = 0.0
    flat = []
    for im in imoveis or []:
        v = im.get("vacancia")
        if v is None:
            continue
        a = im.get("area_m2") or 0.0
        if a > 0:
            num += v * a
            den += a
        flat.append(v)
    if den > 0:
        return round(num / den, 4)
    return round(sum(flat) / len(flat), 4) if flat else None

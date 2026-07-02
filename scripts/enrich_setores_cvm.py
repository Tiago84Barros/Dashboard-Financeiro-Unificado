"""
Enriquece a classificação setorial B3 (public.setores) para as empresas do
universo market.* que ainda não têm setor — resolvendo a lacuna que fazia a
paleta de Empresas B3 mostrar um grande grupo "Sem classificação".

Estratégia (A + C):
  A) CVM cadastro (cad_cia_aberta.SETOR_ATIV): resolve a empresa por codigo_cvm
     (public.cvm_to_ticker) ou por NOME (DENOM_SOCIAL/COMERC), e traduz o setor
     de atividade da CVM para a taxonomia B3 (Setor/Subsetor/Segmento).
  C) Mapa curado B3 para as poucas empresas notáveis sem match na CVM.

Escreve linhas de referência em public.setores (mesma tabela que load_setores lê),
usando o ticker representativo do emissor. Idempotente: só insere raízes ainda
ausentes. Dry-run por padrão; --apply grava.

Uso:
  python scripts/enrich_setores_cvm.py            # dry-run (mostra o que faria)
  python scripts/enrich_setores_cvm.py --apply    # grava em public.setores
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import unicodedata
from pathlib import Path

import requests
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.b3_db import _resolve_url  # noqa: E402

logger = logging.getLogger("enrich_setores_cvm")

CAD_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv"

# CVM SETOR_ATIV → (SETOR, SUBSETOR, SEGMENTO) na taxonomia B3. O prefixo
# "Emp. Adm. Part. - " (holdings) é removido antes do lookup.
_CVM_TO_B3: dict[str, tuple[str, str, str]] = {
    "energia eletrica": ("Utilidade Pública", "Energia Elétrica", "Energia Elétrica"),
    "saneamento, serv. agua e gas": ("Utilidade Pública", "Água e Saneamento", "Água e Saneamento"),
    "agua e saneamento": ("Utilidade Pública", "Água e Saneamento", "Água e Saneamento"),
    "petroleo e gas": ("Petróleo, Gás e Biocombustíveis", "Petróleo, Gás e Biocombustíveis", "Exploração, Refino e Distribuição"),
    "petroquimicos e borracha": ("Materiais Básicos", "Químicos", "Petroquímicos"),
    "quimica": ("Materiais Básicos", "Químicos", "Químicos Diversos"),
    "metalurgia e siderurgia": ("Materiais Básicos", "Siderurgia e Metalurgia", "Siderurgia"),
    "siderurgia e metalurgia": ("Materiais Básicos", "Siderurgia e Metalurgia", "Siderurgia"),
    "extracao mineral": ("Materiais Básicos", "Mineração", "Minerais Metálicos"),
    "mineracao": ("Materiais Básicos", "Mineração", "Minerais Metálicos"),
    "papel e celulose": ("Materiais Básicos", "Madeira e Papel", "Papel e Celulose"),
    "bancos": ("Financeiro", "Intermediários Financeiros", "Bancos"),
    "intermediacao financeira": ("Financeiro", "Intermediários Financeiros", "Bancos"),
    "seguradoras e corretoras": ("Financeiro", "Previdência e Seguros", "Seguradoras"),
    "seguros": ("Financeiro", "Previdência e Seguros", "Seguradoras"),
    "securitizacao de recebiveis": ("Financeiro", "Serviços Financeiros Diversos", "Securitizadoras"),
    "arrendamento mercantil": ("Financeiro", "Serviços Financeiros Diversos", "Serviços Financeiros"),
    "telecomunicacoes": ("Comunicações", "Telecomunicações", "Telecomunicações"),
    "comunicacao e informatica": ("Tecnologia da Informação", "Programas e Serviços", "Programas e Serviços"),
    "comunicacao, informatica e conexos": ("Tecnologia da Informação", "Programas e Serviços", "Programas e Serviços"),
    "informatica": ("Tecnologia da Informação", "Programas e Serviços", "Programas e Serviços"),
    "comercio (atacado e varejo)": ("Consumo Cíclico", "Comércio", "Comércio Varejista"),
    "textil e vestuario": ("Consumo Cíclico", "Tecidos, Vestuário e Calçados", "Vestuário"),
    "construcao civil, mat. constr. e decoracao": ("Consumo Cíclico", "Construção Civil", "Incorporações"),
    "construcao civil": ("Consumo Cíclico", "Construção Civil", "Incorporações"),
    "brinquedos e lazer": ("Consumo Cíclico", "Viagens e Lazer", "Lazer"),
    "hospedagem e turismo": ("Consumo Cíclico", "Viagens e Lazer", "Hotelaria e Restaurantes"),
    "alimentos": ("Consumo não Cíclico", "Alimentos Processados", "Alimentos Diversos"),
    "bebidas e fumo": ("Consumo não Cíclico", "Bebidas", "Cervejas e Refrigerantes"),
    "agricultura (acucar, alcool e cana)": ("Consumo não Cíclico", "Agropecuária", "Açúcar e Álcool"),
    "agricultura": ("Consumo não Cíclico", "Agropecuária", "Agricultura"),
    "educacao": ("Consumo Cíclico", "Diversos", "Serviços Educacionais"),
    "embalagens": ("Materiais Básicos", "Madeira e Papel", "Papel e Embalagens"),
    "farmaceutico e higiene": ("Saúde", "Comércio e Distribuição", "Medicamentos e Outros Produtos"),
    "servicos medicos": ("Saúde", "Serv. Méd. Hospit., Análises e Diagnósticos", "Serviços Médico-Hospitalares"),
    "maquinas, equipamentos, veiculos e pecas": ("Bens Industriais", "Máquinas e Equipamentos", "Máq. e Equip. Industriais"),
    "veiculos e pecas": ("Bens Industriais", "Material de Transporte", "Material Rodoviário"),
    "servicos transporte e logistica": ("Bens Industriais", "Transporte", "Serviços de Transporte e Logística"),
    "transporte e logistica": ("Bens Industriais", "Transporte", "Serviços de Transporte e Logística"),
    "emp. adm. part.": ("Financeiro", "Holdings Diversificadas", "Holdings Diversificadas"),
    "holdings": ("Financeiro", "Holdings Diversificadas", "Holdings Diversificadas"),
}

# C — mapa curado (raiz de 4 letras) para empresas notáveis sem match na CVM.
_CURATED: dict[str, tuple[str, str, str]] = {
    "RAIL": ("Bens Industriais", "Transporte", "Transporte Ferroviário"),          # Rumo
    "JSLG": ("Bens Industriais", "Transporte", "Serviços de Transporte e Logística"),  # JSL
    "LWSA": ("Tecnologia da Informação", "Programas e Serviços", "Programas e Serviços"),  # Locaweb
    "RIAA": ("Consumo Cíclico", "Comércio", "Tecidos, Vestuário e Calçados"),      # Riachuelo/Guararapes
    "CTNM": ("Consumo Cíclico", "Tecidos, Vestuário e Calçados", "Fios e Tecidos"),  # Coteminas
    "SAUD": ("Saúde", "Serv. Méd. Hospit., Análises e Diagnósticos", "Planos de Saúde"),  # Odontoprev
    "SCAR": ("Financeiro", "Exploração de Imóveis", "Exploração de Imóveis"),      # São Carlos
    "CEGR": ("Utilidade Pública", "Gás", "Gás"),                                   # CEG / Distribuidora de Gás
    "PRBC": ("Financeiro", "Intermediários Financeiros", "Bancos"),               # Paraná Banco
    "SLED": ("Consumo Cíclico", "Comércio", "Livrarias e Papelarias"),            # Saraiva
    "SOND": ("Bens Industriais", "Construção e Engenharia", "Engenharia Consultiva"),  # Sondotécnica
    "VTRU": ("Consumo Cíclico", "Diversos", "Serviços Educacionais"),             # Vitru (Uniasselvi)
    "MLAS": ("Consumo Cíclico", "Utilidades Domésticas", "Eletrodomésticos"),     # Multilaser
    "BEEF": ("Consumo não Cíclico", "Alimentos Processados", "Carnes e Derivados"),  # Minerva
    "ALUP": ("Utilidade Pública", "Energia Elétrica", "Energia Elétrica"),           # Alupar (transmissão)
}


def _norm(x: str) -> str:
    x = unicodedata.normalize("NFKD", str(x or "")).encode("ascii", "ignore").decode().upper()
    x = re.sub(r"\b(S\.?A\.?|LTDA|CIA|COMPANHIA|PARTICIPACOES|PART|HOLDING|"
               r"DO BRASIL|BRASIL|GROUP|GRUPO|ON|PN|UNT)\b", "", x)
    return re.sub(r"[^A-Z0-9]", "", x)


def _map_cvm_setor(setor_ativ: str) -> tuple[str, str, str] | None:
    s = _norm_key(setor_ativ)
    if s in _CVM_TO_B3:
        return _CVM_TO_B3[s]
    if s.startswith("emp. adm. part. - "):
        return _CVM_TO_B3.get(s.split(" - ", 1)[1].strip(), _CVM_TO_B3["emp. adm. part."])
    if s.startswith("emp. adm. part."):
        return _CVM_TO_B3["emp. adm. part."]
    return None


def _norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().strip()
    return re.sub(r"\s+", " ", s)


def _load_cad() -> tuple[dict[int, str], dict[str, str]]:
    """(codigo_cvm → SETOR_ATIV, nome_normalizado → SETOR_ATIV)."""
    txt = requests.get(CAD_URL, headers={"User-Agent": "DashboardFinanceiro/1.0"},
                       timeout=90).content.decode("latin-1")
    cd2s: dict[int, str] = {}
    name2s: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(txt), delimiter=";"):
        s = (row.get("SETOR_ATIV") or "").strip()
        if not s:
            continue
        try:
            cd2s[int(row["CD_CVM"])] = s
        except (TypeError, ValueError, KeyError):
            pass
        for nm in (row.get("DENOM_SOCIAL"), row.get("DENOM_COMERC")):
            k = _norm(nm)
            if len(k) >= 5:
                name2s.setdefault(k, s)
    return cd2s, name2s


def run(apply: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = _resolve_url()
    if not url:
        logger.error("Banco não configurado.")
        return 1
    _ssl = {} if ("localhost" in url or "127.0.0.1" in url) else {"sslmode": "require"}
    eng = create_engine(url, connect_args={"connect_timeout": 20, **_ssl})

    with eng.connect() as conn:
        roots = conn.execute(text("""
            WITH ass AS (
                SELECT DISTINCT LEFT(UPPER(a.ticker), 4) raiz, MIN(a.ticker) tk
                FROM market.assets a
                -- ações ON/PN (3-8) e units (11); FIIs/ETFs sem match na CVM
                -- caem em "faltando" e não são inseridos.
                WHERE a.ticker ~ '([3-8]|11)$'
                  AND NOT EXISTS (SELECT 1 FROM public.setores s
                       WHERE LEFT(UPPER(REPLACE(s.ticker, '.SA', '')), 4) = LEFT(UPPER(a.ticker), 4))
                GROUP BY 1)
            SELECT ass.raiz, ass.tk, COALESCE(c.name, '') nome, cc."CVM" cvm
            FROM ass
            LEFT JOIN market.assets a2 ON a2.ticker = ass.tk
            LEFT JOIN market.companies c ON c.id = a2.company_id
            LEFT JOIN public.cvm_to_ticker cc ON LEFT(UPPER(cc."Ticker"), 4) = ass.raiz
        """)).fetchall()
    logger.info("raízes sem classificação (ações): %d", len(roots))

    cd2s, name2s = _load_cad()

    resolvidos: list[dict] = []
    unmapped_setor: dict[str, int] = {}
    faltando: list[tuple] = []
    for raiz, tk, nome, cvm in roots:
        # C tem prioridade (curadoria explícita)
        b3 = _CURATED.get(raiz)
        origem = "curado"
        if not b3:
            setor_ativ = cd2s.get(cvm) if cvm else None
            if not setor_ativ:
                k = _norm(nome)
                setor_ativ = name2s.get(k)
                if not setor_ativ and len(k) >= 6:
                    for nk, nv in name2s.items():
                        if k in nk or nk in k:
                            setor_ativ = nv
                            break
            if setor_ativ:
                b3 = _map_cvm_setor(setor_ativ)
                origem = f"cvm:{setor_ativ}"
                if not b3:
                    unmapped_setor[setor_ativ] = unmapped_setor.get(setor_ativ, 0) + 1
        if not b3:
            faltando.append((raiz, (nome or tk)[:34]))
            continue
        resolvidos.append({"ticker": tk, "nome": nome or tk,
                           "SETOR": b3[0], "SUBSETOR": b3[1], "SEGMENTO": b3[2], "origem": origem})

    logger.info("resolvidos: %d | sem mapeamento: %d", len(resolvidos), len(faltando))
    if unmapped_setor:
        logger.info("SETOR_ATIV da CVM ainda sem tradução B3 (some p/ _CVM_TO_B3):")
        for s, n in sorted(unmapped_setor.items(), key=lambda x: -x[1]):
            logger.info("   %3d  %s", n, s)
    if faltando:
        logger.info("raízes ainda sem setor (candidatas a _CURATED):")
        for raiz, nome in sorted(faltando):
            logger.info("   %s  %s", raiz, nome)

    if not apply:
        logger.info("DRY-RUN — nada gravado. Use --apply para inserir em public.setores.")
        return 0

    ins = 0
    with eng.begin() as conn:
        for r in resolvidos:
            done = conn.execute(text("""
                INSERT INTO public.setores (ticker, nome_empresa, "SETOR", "SUBSETOR", "SEGMENTO")
                VALUES (:tk, :nm, :se, :su, :sg)
                ON CONFLICT (ticker) DO NOTHING
                RETURNING ticker
            """), {"tk": r["ticker"], "nm": r["nome"][:200],
                   "se": r["SETOR"], "su": r["SUBSETOR"], "sg": r["SEGMENTO"]}).scalar()
            if done:
                ins += 1
    logger.info("inseridos em public.setores: %d", ins)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Enriquece public.setores via CVM cadastro + curadoria B3.")
    ap.add_argument("--apply", action="store_true", help="Grava em public.setores (senão dry-run).")
    return run(ap.parse_args().apply)


if __name__ == "__main__":
    raise SystemExit(main())

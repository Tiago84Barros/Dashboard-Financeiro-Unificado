"""
core/cvm_fii.py
Coletor do "Informe Mensal de FIIs" da CVM (dados.cvm.gov.br) — enriquece os
FIIs com dados que a BRAPI não tem: segmento real, patrimônio líquido, valor
patrimonial da cota (VPA), nº de cotistas, DY patrimonial e composição de ativos
(imóveis × papel/CRI × caixa × cotas de FII) → base p/ diversificação.

NÃO traz vacância (não consta no informe estruturado; fica nos relatórios
gerenciais em PDF). Join com os tickers via CNPJ (normalizado p/ dígitos).

Parsing PURO e testável (parse_informe recebe os bytes do zip).
"""
from __future__ import annotations

import io
import logging
import re
import zipfile

logger = logging.getLogger(__name__)

INF_ZIP = "https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/inf_mensal_fii_{year}.zip"

# composição de ativos (colunas do inf_mensal_fii_ativo_passivo)
_IMOVEIS = ("Direitos_Bens_Imoveis", "Terrenos", "Imoveis_Renda_Acabados",
            "Imoveis_Renda_Construcao", "Imoveis_Venda_Acabados",
            "Imoveis_Venda_Construcao", "Outros_Direitos_Reais")
_PAPEL = ("CRI", "CRI_CRA", "LCI", "LCI_LCA", "LIG", "Debentures",
          "Letras_Hipotecarias", "Notas_Promissorias", "Cedulas_Debentures")
_FUNDOS = ("FII", "FIP", "FDIC", "Fundo_Acoes", "Outras_Cotas_FI")
_CAIXA = ("Disponibilidades", "Titulos_Publicos", "Titulos_Privados", "Fundos_Renda_Fixa")


def only_digits(cnpj) -> str:
    return re.sub(r"\D", "", str(cnpj or ""))


def _s(v) -> str:
    """String limpa de campo CVM (trata NaN/None do pandas)."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v).strip()


def _num(v):
    """float de campo CVM (aceita vírgula decimal). None em falha/vazio."""
    if v is None:
        return None
    s = str(v).strip().replace(".", "").replace(",", ".") if "," in str(v) else str(v).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    try:
        x = float(s)
        return x if x == x else None
    except ValueError:
        return None


def _read_csv(zf: zipfile.ZipFile, name: str):
    import pandas as pd
    with zf.open(name) as f:
        return pd.read_csv(f, sep=";", encoding="latin-1", dtype=str)


def _latest_per_cnpj(df, cnpj_col="CNPJ_Fundo_Classe", date_col="Data_Referencia"):
    """Mantém a linha mais recente por CNPJ (maior Data_Referencia)."""
    if df is None or df.empty:
        return {}
    d = df.sort_values(date_col).drop_duplicates(subset=[cnpj_col], keep="last")
    return {only_digits(r[cnpj_col]): r.to_dict() for _, r in d.iterrows()}


def _by_cnpj_month(df, cnpj_col="CNPJ_Fundo_Classe", date_col="Data_Referencia"):
    """Indexa por (cnpj_digits, Data_Referencia) -> linha (dict). Para a série mensal."""
    if df is None or df.empty:
        return {}
    out: dict[tuple, dict] = {}
    for _, r in df.iterrows():
        out[(only_digits(r[cnpj_col]), _s(r.get(date_col)))] = r.to_dict()
    return out


def _composition(row) -> dict:
    """Percentuais de imóveis/papel/fundos/caixa sobre o ativo total."""
    def soma(cols):
        return sum((_num(row.get(c)) or 0.0) for c in cols)
    imoveis, papel, fundos, caixa = soma(_IMOVEIS), soma(_PAPEL), soma(_FUNDOS), soma(_CAIXA)
    total = _num(row.get("Valor_Ativo")) or (imoveis + papel + fundos + caixa)
    if not total or total <= 0:
        return {"pct_imoveis": None, "pct_papel": None, "pct_caixa": None, "pct_fundos": None}
    return {"pct_imoveis": round(imoveis / total, 4), "pct_papel": round(papel / total, 4),
            "pct_caixa": round(caixa / total, 4), "pct_fundos": round(fundos / total, 4)}


def classify_tipo(comp: dict) -> str:
    """tijolo | papel | fof | hibrido a partir da composição de ativos."""
    im, pa, fu = comp.get("pct_imoveis") or 0, comp.get("pct_papel") or 0, comp.get("pct_fundos") or 0
    if max(im, pa, fu) < 0.5:
        return "hibrido"
    if im >= pa and im >= fu:
        return "tijolo"
    if pa >= fu:
        return "papel"
    return "fof"


def parse_informe(zip_bytes: bytes, year: int) -> dict[str, dict]:
    """
    bytes do zip -> {cnpj_digits: {segmento, patrimonio_liquido, vpa, num_cotistas,
    dy_patrimonial_mes, tipo_gestao, publico_alvo, isin, nome, tipo, pct_*}}.
    Usa o mês mais recente disponível por fundo.
    """
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    geral = _latest_per_cnpj(_read_csv(zf, f"inf_mensal_fii_geral_{year}.csv"))
    comp = _latest_per_cnpj(_read_csv(zf, f"inf_mensal_fii_complemento_{year}.csv"))
    ativo = _latest_per_cnpj(_read_csv(zf, f"inf_mensal_fii_ativo_passivo_{year}.csv"))

    out: dict[str, dict] = {}
    for cnpj, g in geral.items():
        c = comp.get(cnpj, {})
        a = ativo.get(cnpj, {})
        composition = _composition(a) if a is not None and len(a) else {
            "pct_imoveis": None, "pct_papel": None, "pct_caixa": None, "pct_fundos": None}
        rec = {
            "cnpj": cnpj,
            "nome": _s(g.get("Nome_Fundo_Classe"))[:200],
            "isin": _s(g.get("Codigo_ISIN")) or None,
            "segmento": _s(g.get("Segmento_Atuacao")) or None,
            "tipo_gestao": _s(g.get("Tipo_Gestao")) or None,
            "publico_alvo": _s(g.get("Publico_Alvo")) or None,
            "ref_date": _s(g.get("Data_Referencia")) or None,
            "patrimonio_liquido": _num((c or {}).get("Patrimonio_Liquido")),
            "vpa": _num((c or {}).get("Valor_Patrimonial_Cotas")),
            "num_cotistas": _num((c or {}).get("Total_Numero_Cotistas")),
            "dy_patrimonial_mes": _num((c or {}).get("Percentual_Dividend_Yield_Mes")),
            **composition,
        }
        rec["tipo"] = classify_tipo(composition)
        out[cnpj] = rec
    return out


def _ref_month(data_referencia: str) -> str | None:
    """Normaliza Data_Referencia ('YYYY-MM-DD'/'YYYY-MM') p/ 1º dia do mês 'YYYY-MM-01'."""
    s = _s(data_referencia)
    if len(s) >= 7 and s[4] == "-" and s[:4].isdigit() and s[5:7].isdigit():
        return f"{s[:7]}-01"
    return None


def parse_informe_monthly(zip_bytes: bytes, year: int) -> dict[str, list[dict]]:
    """
    bytes do zip -> {cnpj_digits: [ {ref_month, vpa, patrimonio_liquido, num_cotistas,
    dy_patrimonial_mes, pct_imoveis, pct_papel, pct_caixa, pct_fundos}, ... ]}.

    Diferente de parse_informe (que pega só o mês mais recente), mantém TODAS as
    Data_Referencia disponíveis no ano -> série mensal (P/VP e VPA históricos).
    Junta complemento (VPA/PL/cotistas/DY) com ativo_passivo (composição) por
    (cnpj, mês). Ordena por ref_month asc.
    """
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    comp = _by_cnpj_month(_read_csv(zf, f"inf_mensal_fii_complemento_{year}.csv"))
    ativo = _by_cnpj_month(_read_csv(zf, f"inf_mensal_fii_ativo_passivo_{year}.csv"))

    out: dict[str, list[dict]] = {}
    for (cnpj, ref), c in comp.items():
        ref_month = _ref_month(ref)
        if not ref_month:
            continue
        a = ativo.get((cnpj, ref))
        composition = _composition(a) if a else {
            "pct_imoveis": None, "pct_papel": None, "pct_caixa": None, "pct_fundos": None}
        rec = {
            "ref_month": ref_month,
            "patrimonio_liquido": _num((c or {}).get("Patrimonio_Liquido")),
            "vpa": _num((c or {}).get("Valor_Patrimonial_Cotas")),
            "num_cotistas": _num((c or {}).get("Total_Numero_Cotistas")),
            "dy_patrimonial_mes": _num((c or {}).get("Percentual_Dividend_Yield_Mes")),
            **composition,
        }
        out.setdefault(cnpj, []).append(rec)
    for cnpj in out:
        out[cnpj].sort(key=lambda r: r["ref_month"])
    return out


def fetch_informe(year: int, timeout: int = 120) -> bytes | None:
    import requests
    try:
        r = requests.get(INF_ZIP.format(year=year), timeout=timeout,
                         headers={"User-Agent": "DashboardFinanceiro/1.0 (+fii)"})
    except Exception as exc:
        logger.warning("fetch_informe %s: %s", year, exc)
        return None
    return r.content if r.status_code == 200 else None

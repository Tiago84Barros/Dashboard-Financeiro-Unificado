"""
data_pipeline/market/b3_saidas.py
Reconstrução point-in-time das empresas que SAÍRAM da B3 — núcleo PURO.

Por que existe: o backtest da seleção B3 partia do universo de hoje. Quem foi
comprada, fechou capital ou quebrou nunca entrava na reconstrução histórica, e
o retorno medido era o de uma amostra 100% sobrevivente (o mesmo defeito que o
módulo dos EUA já corrigiu com o painel PIT). Aqui ficam as funções que
transformam as duas fontes públicas que JÁ estão em disco — o COTAHIST da B3
(preço bruto diário, armazém local) e a DFP da CVM (demonstrações anuais,
cache em data/cache/cvm/dfp) — em preço mensal de retorno total, volume mensal
e múltiplos anuais no mesmo formato do painel das empresas vivas.

Nada aqui acessa rede ou banco. O gerador (scripts/gerar_b3_saidas.py) lê as
fontes e grava data/b3_saidas.json; o leitor (core/b3_saidas.py) injeta no
backtest.

Convenções declaradas (e herdadas pelas limitações do JSON):
  - available_at = DT_RECEB da PRIMEIRA versão da DFP (quando o mercado a viu).
    Os valores, porém, são os da ÚLTIMA versão — o arquivo de dados abertos só
    traz a mais recente. Reapresentação posterior é um look-ahead brando.
  - Ações em circulação por ano = lucro dos controladores / LPA básico. O LPA
    vem com duas casas; abaixo de |0,05| o erro de arredondamento passa de 10%
    e o ano herda a estimativa do vizinho válido mais próximo.
  - Desdobramento/grupamento: detectado no salto do preço bruto e confirmado
    pela variação das ações estimadas entre as DFPs vizinhas.
  - Dividendos: o total pago no ano (DFC) dividido pelas ações do ano, lançado
    em quatro parcelas trimestrais. Dividendo do ano da saída não é capturado.
"""
from __future__ import annotations

import io
import math
import zipfile
from dataclasses import dataclass, field

import pandas as pd

from data_pipeline.market.metrics import compute_snapshot

# Fatores "redondos" de desdobramento/grupamento praticados na B3.
FATORES_REDONDOS = (2, 3, 4, 5, 6, 8, 10, 20, 25, 50, 100, 200, 1000)
_ESCALA = {"MIL": 1000.0, "UNIDADE": 1.0}
LPA_MINIMO = 0.05


# --------------------------------------------------------------------------
# DFP
# --------------------------------------------------------------------------
@dataclass
class DemonstracaoAnual:
    cd_cvm: int
    ano: int
    available_at: pd.Timestamp
    consolidado: bool
    # (CD_CONTA) -> (DS_CONTA, valor em R$)
    contas: dict[str, tuple[str, float]] = field(default_factory=dict)


def _ler_csv(z: zipfile.ZipFile, nome: str) -> pd.DataFrame | None:
    if nome not in z.namelist():
        return None
    return pd.read_csv(z.open(nome), sep=";", encoding="latin1", dtype=str)


def ler_dfp(zip_bytes: bytes, ano: int, cds: set[int]) -> dict[int, DemonstracaoAnual]:
    """Extrai BPA/BPP/DRE/DFC do exercício ``ano`` para os códigos CVM pedidos.

    Prefere o consolidado quando a companhia publicou DRE consolidada.
    """
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    cab = _ler_csv(z, f"dfp_cia_aberta_{ano}.csv")
    if cab is None:
        return {}
    cab["cd"] = pd.to_numeric(cab["CD_CVM"], errors="coerce")
    cab = cab[cab["cd"].isin(cds)].copy()
    if cab.empty:
        return {}
    cab["versao"] = pd.to_numeric(cab["VERSAO"], errors="coerce")
    cab["receb"] = pd.to_datetime(cab["DT_RECEB"], errors="coerce")
    primeira = cab.sort_values("versao").groupby("cd")["receb"].first()

    out: dict[int, DemonstracaoAnual] = {}
    for escopo in ("con", "ind"):
        dre = _ler_csv(z, f"dfp_cia_aberta_DRE_{escopo}_{ano}.csv")
        if dre is None:
            continue
        dre["cd"] = pd.to_numeric(dre["CD_CVM"], errors="coerce")
        com_dre = set(dre.loc[dre["cd"].isin(cds), "cd"].dropna().astype(int)) - set(out)
        if not com_dre:
            continue
        for cd in com_dre:
            out[cd] = DemonstracaoAnual(
                cd_cvm=cd, ano=ano, available_at=primeira.get(cd, pd.NaT),
                consolidado=(escopo == "con"))
        partes = [dre]
        for dem in ("BPA", "BPP", "DFC_MD", "DFC_MI"):
            df = _ler_csv(z, f"dfp_cia_aberta_{dem}_{escopo}_{ano}.csv")
            if df is not None:
                df["cd"] = pd.to_numeric(df["CD_CVM"], errors="coerce")
                partes.append(df)
        for df in partes:
            df = df[df["cd"].isin(com_dre) & (df["ORDEM_EXERC"].str.upper() == "ÚLTIMO")]
            for r in df.itertuples(index=False):
                conta = str(r.CD_CONTA)
                try:
                    v = float(r.VL_CONTA)
                except (TypeError, ValueError):
                    continue
                # LPA (3.99.x) é por ação: a escala da planilha não se aplica.
                if not conta.startswith("3.99"):
                    v *= _ESCALA.get(str(r.ESCALA_MOEDA).upper(), 1.0)
                out[int(r.cd)].contas.setdefault(conta, (str(r.DS_CONTA), v))
    return out


def _v(contas: dict, codigo: str) -> float | None:
    x = contas.get(codigo)
    return None if x is None else x[1]


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return s


def _eh_divida(ds: str) -> bool:
    d = _norm(ds)
    return "emprestimo" in d or "financiamento" in d or "debenture" in d


def dividendos_pagos(contas: dict) -> float | None:
    """Dividendos + JCP pagos no ano (valor positivo), das linhas 6.03.xx.

    Só as linhas mais rasas que casam: a subconta de uma linha já contada não
    entra de novo. Recebidos, a pagar, de não controladores e empréstimos
    ficam fora.
    """
    casam = []
    for cod, (ds, v) in contas.items():
        if not cod.startswith("6.03.") or v is None or v >= 0:
            continue
        d = _norm(ds)
        if not ("dividend" in d or "juros sobre" in d or "jcp" in d):
            continue
        if any(x in d for x in ("recebid", "a pagar", "nao controlador", "emprestimo")):
            continue
        casam.append(cod)
    rasas = [c for c in casam if not any(c != o and c.startswith(o + ".") for o in casam)]
    if not rasas:
        return None
    return float(-sum(contas[c][1] for c in rasas))


# Sufixo do código de negociação -> rótulos da classe no LPA da DFP, em ordem de preferência.
_CLASSE_LPA = {"3": ("ON",), "4": ("PN", "PNA"), "5": ("PNA", "PN"), "6": ("PNB", "PN")}


def lpa_da_classe(contas: dict, classe: str) -> float | None:
    """LPA básico da classe negociada (3.99.01.xx).

    Tem de ser a classe do código: com direitos econômicos desiguais o lucro
    dividido pelo LPA de OUTRA classe conta ações na unidade errada. A GOL
    paga à PN 35 vezes o que paga à ON; lucro/LPA_ON dava um valor de mercado
    de R$ 265 bi em 2010. Lucro/LPA_PN dá as ações em "equivalentes PN", que
    é exatamente a unidade do preço de GOLL4.
    """
    rotulos = _CLASSE_LPA.get(classe)
    if not rotulos:
        return None
    por_rotulo = {}
    for cod, (ds, v) in contas.items():
        if cod.startswith("3.99.01.") and v:
            por_rotulo.setdefault(ds.strip().upper(), v)
    for r in rotulos:
        if r in por_rotulo:
            return por_rotulo[r]
    return None


def fundamentos(dem: DemonstracaoAnual, classe: str = "3") -> dict:
    """Insumos anuais no vocabulário de ``compute_snapshot`` (valores em R$).

    ``classe`` é o sufixo do código negociado ("3" ON, "4" PN, ...).
    """
    c = dem.contas
    ni_total = _v(c, "3.11")
    ni_ctrl = _v(c, "3.11.01") if dem.consolidado else None
    ni = ni_ctrl if ni_ctrl is not None else ni_total

    eq = _v(c, "2.03")
    if eq is not None and dem.consolidado:
        for cod, (ds, v) in c.items():
            if cod.startswith("2.03.") and cod.count(".") == 2 and "nao controlador" in _norm(ds):
                eq -= v
                break

    cash = sum(x for x in (_v(c, "1.01.01"), _v(c, "1.01.02")) if x is not None) \
        if (_v(c, "1.01.01") is not None or _v(c, "1.01.02") is not None) else None
    gd = None
    for cod in ("2.01.04", "2.02.01"):
        x = c.get(cod)
        if x is not None and _eh_divida(x[0]):
            gd = (gd or 0.0) + x[1]

    eps = lpa_da_classe(c, classe)

    return {
        "net_income": ni,
        "revenue": _v(c, "3.01"),
        "ebit": _v(c, "3.05"),
        "total_assets": _v(c, "1"),
        "equity": eq,
        "cash": cash,
        "gross_debt": gd,
        "net_debt": (gd - (cash or 0.0)) if gd is not None else None,
        "fco": _v(c, "6.01"),
        "current_assets": _v(c, "1.01"),
        "current_liabilities": _v(c, "2.01"),
        "dividendos_pagos": dividendos_pagos(c),
        "lpa": eps,
    }


# --------------------------------------------------------------------------
# Ações em circulação e eventos de capital
# --------------------------------------------------------------------------
def acoes_estimadas(fund_por_ano: dict[int, dict]) -> dict[int, float]:
    """Ações por ano = lucro/LPA, na unidade vigente na data da DFP do ano.

    Anos com |LPA| < 0,05 não estimam (erro de arredondamento > 10%).
    """
    out = {}
    for ano, f in fund_por_ano.items():
        ni, lpa = f.get("net_income"), f.get("lpa")
        if ni is None or lpa is None or abs(lpa) < LPA_MINIMO:
            continue
        n = ni / lpa
        if n > 0:
            out[ano] = n
    return out


def _fator_redondo(k: float, tol: float) -> float | None:
    for f in FATORES_REDONDOS:
        for alvo in (float(f), 1.0 / f):
            if abs(k / alvo - 1.0) <= tol:
                return alvo
    return None


@dataclass
class EventoCapital:
    data: pd.Timestamp      # primeiro pregão na nova base
    fator: float            # ações novas por ação antiga (2 = desdobra 1:2; 0.1 = grupa 10:1)
    status: str             # "confirmado" | "so_preco"


def detectar_eventos(precos: pd.Series, acoes: dict[int, float],
                     available_at: dict[int, pd.Timestamp]) -> list[EventoCapital]:
    """Desdobramentos/grupamentos no preço bruto diário (índice = pregão).

    Candidato: variação diária fora de [0,6; 1,7] que persiste (mediana dos 5
    pregões seguintes contra a dos 5 anteriores dentro de 20% do salto) e cujo
    fator fica a 12% de um fator redondo. Confirmado quando as ações estimadas
    nas DFPs que cercam o evento variam pelo mesmo fator (±25%). Sem DFP para
    confirmar, só aceita o fator a 3% de um redondo.
    """
    p = precos.dropna().sort_index()
    p = p[p > 0]
    if len(p) < 12:
        return []
    r = p / p.shift(1)
    ev = []
    for i in range(5, len(p) - 5):
        ri = r.iloc[i]
        if not (ri < 0.6 or ri > 1.7):
            continue
        antes = p.iloc[i - 5:i].median()
        depois = p.iloc[i:i + 5].median()
        persist = depois / antes
        if abs(persist / ri - 1.0) > 0.20:
            continue
        k_bruto = 1.0 / persist
        k = _fator_redondo(k_bruto, 0.12)
        if k is None:
            continue
        data = p.index[i]
        # DFP publicada antes e depois do evento
        antes_dfp = [a for a, d in available_at.items() if a in acoes and pd.notna(d) and d < data]
        depois_dfp = [a for a, d in available_at.items() if a in acoes and pd.notna(d) and d >= data]
        status = None
        if antes_dfp and depois_dfp:
            n0 = acoes[max(antes_dfp)]
            n1 = acoes[min(depois_dfp)]
            if abs((n1 / n0) / k - 1.0) <= 0.25:
                status = "confirmado"
        if status is None and _fator_redondo(k_bruto, 0.03) is not None:
            status = "so_preco"
        if status:
            ev.append(EventoCapital(pd.Timestamp(data), float(k), status))
    return ev


def fator_acumulado(eventos: list[EventoCapital], depois_de: pd.Timestamp,
                    ate: pd.Timestamp | None = None) -> float:
    """Π fatores dos eventos com data em (depois_de, ate]."""
    f = 1.0
    for e in eventos:
        if e.data > depois_de and (ate is None or e.data <= ate):
            f *= e.fator
    return f


def preco_ajustado(precos: pd.Series, eventos: list[EventoCapital]) -> pd.Series:
    """Preço bruto na unidade FINAL (a do último pregão)."""
    p = precos.dropna().sort_index().astype(float)
    if not eventos:
        return p
    div = pd.Series(1.0, index=p.index)
    for e in eventos:
        div[p.index < e.data] *= e.fator
    return p / div


# --------------------------------------------------------------------------
# Séries mensais
# --------------------------------------------------------------------------
def retorno_total_mensal(precos: pd.Series, eventos: list[EventoCapital],
                         dps_final_por_ano: dict[int, float]) -> pd.Series:
    """Série mensal (fim de mês) de retorno total na unidade final.

    TR_m = TR_{m-1} × (P_m + D_m) / P_{m-1}, com D_m = DPS do ano / 4 lançado
    em mar/jun/set/dez até o mês do último pregão.
    """
    adj = preco_ajustado(precos, eventos)
    if adj.empty:
        return adj
    mensal = adj.groupby(adj.index.to_period("M")).last()
    tr = []
    ant_p = None
    ant_tr = None
    for per, p in mensal.items():
        if ant_p is None:
            ant_tr = float(p)
        else:
            d = 0.0
            if per.month in (3, 6, 9, 12):
                d = dps_final_por_ano.get(per.year, 0.0) / 4.0
            ant_tr = ant_tr * (float(p) + d) / ant_p
        ant_p = float(p)
        tr.append(ant_tr)
    return pd.Series(tr, index=mensal.index)


def volume_mensal(volume_diario: pd.Series) -> pd.Series:
    v = volume_diario.dropna().sort_index()
    return v.groupby(v.index.to_period("M")).sum()


# --------------------------------------------------------------------------
# Múltiplos anuais
# --------------------------------------------------------------------------
def multiplos_anuais(precos: pd.Series, eventos: list[EventoCapital],
                     fund_por_ano: dict[int, dict], acoes: dict[int, float],
                     available_at: dict[int, pd.Timestamp]) -> list[dict]:
    """Uma linha por ano com DFP: múltiplos de ``compute_snapshot`` + valor de mercado.

    Valor de mercado em 31/12 = preço bruto de fechamento × ações naquela
    data. As ações da DFP estão na unidade da data de entrega; eventos entre
    31/12 e a entrega são desfeitos. Ano sem estimativa própria herda a do
    vizinho válido mais próximo, convertida pelos eventos no intervalo.
    """
    p = precos.dropna().sort_index()
    if p.empty:
        return []
    ultimo = p.index.max()
    anos_validos = sorted(acoes)
    linhas = []
    for ano in sorted(fund_por_ano):
        f = fund_por_ano[ano]
        fim = pd.Timestamp(ano, 12, 31)
        janela = p[(p.index <= fim) & (p.index > fim - pd.Timedelta(days=10))]
        if janela.empty or ultimo < fim - pd.Timedelta(days=10):
            continue
        preco = float(janela.iloc[-1])
        n_dec = None
        if anos_validos:
            base = min(anos_validos, key=lambda a: (abs(a - ano), a))
            d_base = available_at.get(base)
            if d_base is not None and pd.notna(d_base):
                n = acoes[base]
                if d_base > fim:
                    n /= fator_acumulado(eventos, fim, d_base)
                else:
                    n *= fator_acumulado(eventos, d_base, fim)
                n_dec = n
        mc = preco * n_dec if n_dec else None
        insumos = {k: f.get(k) for k in (
            "net_income", "revenue", "ebit", "total_assets", "equity", "cash",
            "gross_debt", "net_debt", "fco", "current_assets", "current_liabilities")}
        insumos["market_cap"] = mc
        # DY = dividendos/valor de mercado e Payout = dividendos/lucro, em
        # totais: o quociente é o mesmo da base por ação e dispensa a conversão.
        dv = f.get("dividendos_pagos")
        insumos["div_ttm"] = dv if dv is not None else (0.0 if mc else None)
        insumos["price"] = mc
        insumos["eps"] = f.get("net_income")
        snap = compute_snapshot(insumos)
        linha = {"ano": ano,
                 "available_at": (available_at.get(ano).date().isoformat()
                                  if available_at.get(ano) is not None and pd.notna(available_at.get(ano))
                                  else None),
                 "valor_mercado": None if mc is None or not math.isfinite(mc) else round(mc, 0)}
        linha.update({k: v for k, (v, _m) in snap.items()})
        linhas.append(linha)
    return linhas


def dps_final_por_ano(fund_por_ano: dict[int, dict], acoes: dict[int, float],
                      available_at: dict[int, pd.Timestamp],
                      eventos: list[EventoCapital]) -> dict[int, float]:
    """Dividendo por ação do ano na unidade FINAL (a do preço ajustado)."""
    out = {}
    validos = sorted(acoes)
    if not validos:
        return out
    for ano, f in fund_por_ano.items():
        dv = f.get("dividendos_pagos")
        if not dv:
            continue
        base = min(validos, key=lambda a: (abs(a - ano), a))
        d = available_at.get(base)
        if d is None or pd.isna(d):
            continue
        n_final = acoes[base] * fator_acumulado(eventos, d)
        if n_final > 0:
            out[ano] = dv / n_final
    return out

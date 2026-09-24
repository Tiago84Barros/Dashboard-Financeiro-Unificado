"""
scripts/gerar_b3_saidas.py
Gera data/b3_saidas.json: as empresas que saíram da B3, reconstruídas point-in-
time para o backtest da seleção B3 (ver data_pipeline/market/b3_saidas.py).

Fontes (todas locais, nenhuma rede):
  - COTAHIST da B3 no armazém local (market.b3_security_history, BDI 02);
  - DFP da CVM em data/cache/cvm/dfp/dfp_cia_aberta_AAAA.zip;
  - cadastro da CVM em data/cache/cvm/cad_cia_aberta.csv (motivo do cancelamento).

Entradas versionadas:
  - data/b3_saidas_curadoria.csv: quem entra, com código CVM e a taxonomia
    exata de public.setores (o agrupamento por segmento depende dela);
  - data/b3_saidas_excluidas.csv: saídas líquidas que ficam de fora, e por quê.

Toda saída líquida detectada que não estiver em nenhuma das duas listas vai
para ``saidas_nao_curadas`` no JSON — é o teste de que a curadoria não
esqueceu ninguém.

Uso (na árvore principal, que tem data/cache e acesso ao Docker):
    python scripts/gerar_b3_saidas.py
"""
from __future__ import annotations

import csv
import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from data_pipeline.market import b3_saidas as bs  # noqa: E402
from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

log = logging.getLogger(__name__)

VERSAO = "1.0.0"
ANO_DFP_INI = 2010
PISO_ADTV = 1e6          # R$/dia: mesmo piso da medição de mortalidade
DIAS_MORTO = 30          # último pregão a mais de 30 dias do fim do painel
DIAS_RENOMEACAO = 15     # sucessor da mesma classe começa em até 15 dias...
FAIXA_RENOMEACAO = (0.67, 1.5)  # ...com preço compatível: é troca de código


def _cache_dir() -> Path:
    # O cache da CVM é ignorado pelo git: roda da árvore principal (cwd).
    return Path.cwd() / "data" / "cache" / "cvm"


def ler_cotahist() -> pd.DataFrame:
    q = text("""
        select ticker, trade_date, close_unitario as close, financial_volume
        from market.b3_security_history
        where bdi = '02'
          and (specification like 'ON%' or specification like 'PN%' or specification like 'UNT%')
    """)
    eng = create_engine(_warehouse_url())
    with eng.connect() as c:
        df = pd.read_sql(q, c)
    eng.dispose()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["financial_volume"] = pd.to_numeric(df["financial_volume"], errors="coerce")
    return df


def saidas_liquidas(df: pd.DataFrame) -> dict[str, dict]:
    """Tickers que pararam de negociar, não foram só renomeados e tiveram
    ADTV >= R$ 1 mi (mediana out–mar) em algum ano de decisão antes de sair."""
    fim_painel = df["trade_date"].max()
    g = df.sort_values("trade_date").groupby("ticker")
    last, first = g["trade_date"].max(), g["trade_date"].min()
    lastc, firstc = g["close"].last(), g["close"].first()
    mortos = last[last < fim_painel - pd.Timedelta(days=DIAS_MORTO)].index
    saidas = set()
    for t in mortos:
        d = last[t]
        cand = first[(first > d) & (first <= d + pd.Timedelta(days=DIAS_RENOMEACAO))].index
        ren = [c for c in cand if c[4:] == t[4:] and lastc[t]
               and FAIXA_RENOMEACAO[0] <= firstc[c] / lastc[t] <= FAIXA_RENOMEACAO[1]]
        if not ren:
            saidas.add(t)
    df = df[df["ticker"].isin(saidas)].copy()
    df["mes"] = df["trade_date"].dt.to_period("M").dt.to_timestamp()
    mens = df.groupby(["ticker", "mes"])["financial_volume"].sum().reset_index()
    out: dict[str, dict] = {}
    for ano in range(2011, fim_painel.year + 1):
        ini, fim = pd.Timestamp(ano - 1, 10, 1), pd.Timestamp(ano, 4, 1)
        jan = mens[(mens["mes"] >= ini) & (mens["mes"] < fim)]
        adtv = jan.groupby("ticker")["financial_volume"].median() / 21
        for t in adtv[adtv >= PISO_ADTV].index:
            if last[t] > fim:
                out.setdefault(t, {"ultimo_pregao": last[t].date().isoformat(), "anos_liquidos": []})
                out[t]["anos_liquidos"].append(ano)
    return out


def ler_cadastro() -> dict[int, dict]:
    raw = (_cache_dir() / "cad_cia_aberta.csv").read_bytes().decode("latin1")
    out = {}
    for r in csv.DictReader(io.StringIO(raw), delimiter=";"):
        try:
            cd = int(r.get("CD_CVM") or 0)
        except ValueError:
            continue
        if cd:
            out[cd] = r
    return out


def ler_dfps(cds: set[int]) -> dict[int, dict[int, bs.DemonstracaoAnual]]:
    """{cd: {ano: DemonstracaoAnual}}."""
    out: dict[int, dict[int, bs.DemonstracaoAnual]] = {}
    for zp in sorted(_cache_dir().joinpath("dfp").glob("dfp_cia_aberta_*.zip")):
        try:
            ano = int(zp.stem.rsplit("_", 1)[1])
        except ValueError:
            continue
        if ano < ANO_DFP_INI:
            continue
        for cd, dem in bs.ler_dfp(zp.read_bytes(), ano, cds).items():
            out.setdefault(cd, {})[ano] = dem
        log.info("DFP %s lida", ano)
    return out


def reconstruir(ticker: str, cot: pd.DataFrame, dems: dict[int, bs.DemonstracaoAnual]) -> dict:
    serie = cot.set_index("trade_date").sort_index()
    precos = serie["close"]
    fund = {a: bs.fundamentos(d, ticker[4:]) for a, d in dems.items()}
    av = {a: d.available_at for a, d in dems.items()}
    acoes = bs.acoes_estimadas(fund)
    eventos = bs.detectar_eventos(precos, acoes, av)
    dps = bs.dps_final_por_ano(fund, acoes, av, eventos)
    tr = bs.retorno_total_mensal(precos, eventos, dps)
    vol = bs.volume_mensal(serie["financial_volume"])
    mult = bs.multiplos_anuais(precos, eventos, fund, acoes, av)
    return {
        "primeiro_pregao": precos.index.min().date().isoformat(),
        "ultimo_pregao": precos.index.max().date().isoformat(),
        "anos_dfp": sorted(dems),
        "anos_com_acoes_estimadas": sorted(acoes),
        "desdobramentos": [{"data": e.data.date().isoformat(), "fator": e.fator, "status": e.status}
                           for e in eventos],
        "precos": {str(p): round(float(v), 6) for p, v in tr.items()},
        "volume": {str(p): round(float(v), 2) for p, v in vol.items()},
        "fundamentos": mult,
    }


LIMITACOES = [
    "Os valores da DFP são os da última versão entregue; a data de disponibilidade é a da "
    "primeira. Reapresentação posterior entra antes da hora (look-ahead brando).",
    "Ações em circulação são estimadas por lucro/LPA; anos com |LPA| < 0,05 herdam a estimativa "
    "do ano vizinho. Emissões e recompras entre os dois anos não são capturadas.",
    "Dividendos entram como o total pago no ano (DFC) dividido pelas ações, em quatro parcelas "
    "trimestrais; o provento do ano da saída não é capturado.",
    "O último fechamento é o valor de saída. Em OPA e incorporação ele se aproxima do valor "
    "recebido; em incorporação por troca de ações a continuação na adquirente não é seguida.",
    "Instituições financeiras (bancos, seguradoras) ficam fora: o plano de contas da DFP delas "
    "não tem as linhas que o motor lê.",
    "Saídas anteriores a 2016 ficam fora: a janela de preço do backtest é de dez anos.",
]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cur = pd.read_csv(ROOT / "data" / "b3_saidas_curadoria.csv", dtype={"cd_cvm": int}).fillna("")
    exc = pd.read_csv(ROOT / "data" / "b3_saidas_excluidas.csv")
    cot = ler_cotahist()
    log.info("COTAHIST: %d linhas, %d tickers", len(cot), cot["ticker"].nunique())
    liq = saidas_liquidas(cot)
    cad = ler_cadastro()
    dfps = ler_dfps(set(cur["cd_cvm"]))

    empresas = []
    for r in cur.itertuples(index=False):
        c = cot[cot["ticker"] == r.ticker]
        if c.empty:
            log.warning("%s sem pregão no COTAHIST", r.ticker)
            continue
        rec = reconstruir(r.ticker, c, dfps.get(int(r.cd_cvm), {}))
        cadr = cad.get(int(r.cd_cvm), {})
        empresas.append({
            "ticker": r.ticker,
            "nome": (cadr.get("DENOM_COMERC") or cadr.get("DENOM_SOCIAL") or r.ticker).strip(),
            "cd_cvm": int(r.cd_cvm),
            "SETOR": r.SETOR, "SUBSETOR": r.SUBSETOR, "SEGMENTO": r.SEGMENTO,
            "motivo_cvm": (cadr.get("MOTIVO_CANCEL") or "").strip() or None,
            "nota": r.nota or None,
            **rec,
        })
        log.info("%s: %d meses, %d anos de múltiplos, %d eventos", r.ticker,
                 len(rec["precos"]), len(rec["fundamentos"]), len(rec["desdobramentos"]))

    conhecidos = set(cur["ticker"]) | set(exc["ticker"])
    nao_curadas = {t: v for t, v in sorted(liq.items()) if t not in conhecidos}
    doc = {
        "versao": VERSAO,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "resumo": {
            "empresas": len(empresas),
            "saidas_liquidas_detectadas": len(liq),
            "excluidas": int(len(exc)),
            "nao_curadas": len(nao_curadas),
            "eventos_de_capital": sum(len(e["desdobramentos"]) for e in empresas),
        },
        "excluidas": exc.to_dict(orient="records"),
        "saidas_nao_curadas": nao_curadas,
        "limitacoes": LIMITACOES,
        "empresas": empresas,
    }
    destino = ROOT / "data" / "b3_saidas.json"
    destino.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("gravado %s: %s", destino, doc["resumo"])
    if nao_curadas:
        log.warning("saídas líquidas sem curadoria: %s", ", ".join(nao_curadas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

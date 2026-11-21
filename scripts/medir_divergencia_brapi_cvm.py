# -*- coding: utf-8 -*-
"""Mede quanto os fundamentos da brapi divergem da DFP da CVM. Só lê.

Por que existe (14/11/2026). Os fundamentos da B3 vêm de uma fonte só, a brapi:
o saneamento por duas fontes (``audit_and_heal``) foi descontinuado em 07/2026
e ``core/cross_source.py`` não tem chamador. Antes de propor a CVM como segunda
fonte obrigatória, este script mede o tamanho do problema: em quantas empresas
o lucro, o patrimônio líquido e o LPA da brapi discordam da DFP entregue à CVM.

O que compara, por empresa e exercício (anual):

* ``lucro``: ``market.income_statements.net_income`` x lucro dos controladores
  da DFP (``data_pipeline.market.b3_saidas.fundamentos``);
* ``pl``: ``market.balance_sheets.equity`` x PL dos controladores da DFP;
* ``lpa``: ``market.income_statements.eps`` do ticker x LPA básico da classe
  negociada (3.99.01.xx). Units ficam fora: o LPA da DFP é por ação, não por unit.

A base da comparação é o universo cadastrado (``market.assets`` com código
CVM) em cada exercício cuja DFP foi lida: empresa-ano sem linha na brapi conta
como ``so_cvm``, não some. Exercício sem arquivo da DFP fica fora da medição
inteira, e não vira "só brapi".

A DFP vem do cache que ``scripts/treinar_previsao_lucros_b3.py`` já usa
(``data/cache/cvm/dfp/dfp_cia_aberta_AAAA.zip``); nada aqui busca na rede. A
brapi vem do banco que o app publicado lê (``core.database.get_engine``), ou do
armazém com ``--armazem``.

Rode da raiz do repositório:

    python scripts/medir_divergencia_brapi_cvm.py
    python scripts/medir_divergencia_brapi_cvm.py --anos 2024 2025 --armazem

Saída: resumo no terminal e o detalhe por empresa em
``data/cache/medicao_brapi_cvm.csv`` (pasta ignorada pelo git).
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path

# Raiz = pasta de onde se roda, para o script funcionar também fora de scripts/.
RAIZ = Path.cwd()
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

#: Faixas da divergência relativa |b - c| / max(|b|, |c|).
CONCORDA = 0.02
MODERADA = 0.10
#: Razão b/c que denuncia erro de unidade (mil, milhão) em vez de dado diferente.
ESCALAS = (1e3, 1e6)
TOL_ESCALA = 0.05

CATEGORIAS = ("concorda", "moderada", "forte", "sinal_trocado", "escala",
              "so_brapi", "so_cvm")


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def classificar(brapi, cvm) -> tuple[str, float | None]:
    """``(categoria, divergência relativa)`` de um par brapi x CVM.

    Zero é valor, não ausência: um lucro 0 na brapi contra 5 bi na CVM é
    divergência forte, não "sem dado".
    """
    b, c = _num(brapi), _num(cvm)
    if b is None and c is None:
        return "nenhum", None
    if c is None:
        return "so_brapi", None
    if b is None:
        return "so_cvm", None
    if b == c:
        return "concorda", 0.0
    if b != 0 and c != 0 and (b > 0) != (c > 0):
        return "sinal_trocado", None
    den = max(abs(b), abs(c))
    rel = abs(b - c) / den
    if c != 0 and b != 0:
        razao = abs(b / c)
        for k in ESCALAS:
            if abs(razao - k) / k <= TOL_ESCALA or abs(razao - 1 / k) * k <= TOL_ESCALA:
                return "escala", rel
    if rel <= CONCORDA:
        return "concorda", rel
    if rel <= MODERADA:
        return "moderada", rel
    return "forte", rel


def _ler_brapi(eng, anos: list[int]):
    import pandas as pd
    import sqlalchemy as sa

    universo = pd.read_sql(sa.text("""
        SELECT a.ticker, a.asset_type, c.codigo_cvm::int AS cd_cvm, c.name AS nome
          FROM market.assets a
          JOIN market.companies c ON c.id = a.company_id
         WHERE a.asset_type IN ('stock', 'unit') AND c.codigo_cvm IS NOT NULL
    """), eng)
    dre = pd.read_sql(sa.text("""
        SELECT ticker, year AS ano, net_income, eps
          FROM market.income_statements
         WHERE period = 'annual' AND quarter = 0 AND year = ANY(:anos)
    """), eng, params={"anos": anos})
    bp = pd.read_sql(sa.text("""
        SELECT ticker, year AS ano, equity
          FROM market.balance_sheets
         WHERE period = 'annual' AND quarter = 0 AND year = ANY(:anos)
    """), eng, params={"anos": anos})
    brapi = dre.merge(bp, on=["ticker", "ano"], how="outer")
    return universo, brapi.merge(universo, on="ticker", how="inner")


def _ler_cvm(dfp_dir: Path, anos: list[int], cds: set[int]) -> tuple[dict, list[int]]:
    """``(observações por (cd_cvm, ano), exercícios cujo arquivo foi lido)``.

    Arquivo ausente não é "empresa sem DFP": o exercício sai da lista e,
    com ela, da medição inteira.
    """
    from data_pipeline.market.b3_saidas import fundamentos, ler_dfp, lpa_da_classe

    out: dict[tuple[int, int], dict] = {}
    lidos: list[int] = []
    for ano in anos:
        arq = dfp_dir / f"dfp_cia_aberta_{ano}.zip"
        if not arq.exists():
            print(f"  DFP {ano}: ausente em {arq} -- ano fora da medição")
            continue
        dems = ler_dfp(arq.read_bytes(), ano, cds)
        lidos.append(int(ano))
        for cd, dem in dems.items():
            f = fundamentos(dem)
            out[(int(cd), int(ano))] = {
                "lucro": f.get("net_income"), "pl": f.get("equity"),
                # LPA zero informado é valor a comparar, não ausência.
                "lpa_por_classe": {k: lpa_da_classe(dem.contas, k, manter_zero=True)
                                   for k in "345678"},
                "consolidado": bool(dem.consolidado),
            }
        print(f"  DFP {ano}: {len(dems)} empresas lidas")
    return out, lidos


def _primeiro(linhas: list, campo: str):
    """``(ticker, valor)`` do primeiro ticker com ``campo`` presente."""
    for r in linhas:
        v = _num(r.get(campo))
        if v is not None:
            return r["ticker"], v
    return None, None


def medir(universo, brapi, cvm: dict, anos: list[int]):
    """Linhas de comparação: lucro e PL por empresa, LPA por ticker.

    A grade é ``universo`` x ``anos`` (os exercícios com DFP lida), não as
    linhas que a brapi tem: empresa-ano que falta na brapi vira ``so_cvm``.
    """
    import pandas as pd

    anos = sorted({int(a) for a in anos})
    brapi = brapi[brapi["ano"].isin(anos)]
    por_ticker_ano = {(str(r["ticker"]), int(r["ano"])): r
                      for r in brapi.to_dict("records")}
    uni = universo.dropna(subset=["cd_cvm"]).sort_values(["cd_cvm", "ticker"])

    linhas = []

    def _linha(campo, ano, cd, ticker, nome, b, c, dfp):
        cat, rel = classificar(b, c)
        if cat != "nenhum":
            linhas.append({"campo": campo, "ano": ano, "cd_cvm": cd, "ticker": ticker,
                           "nome": nome, "brapi": _num(b), "cvm": _num(c),
                           "categoria": cat, "divergencia": rel, "dfp": dfp})

    # Lucro e PL são da companhia: uma linha por (empresa, ano, campo). Cada
    # campo vem do primeiro ticker que o tem -- AAAA3 só com DRE e AAAA4 só
    # com balanço dão lucro de um e PL do outro.
    for cd, grupo in uni.groupby("cd_cvm", sort=True):
        cd = int(cd)
        tickers = [str(t) for t in grupo["ticker"]]
        nome = grupo["nome"].iloc[0]
        for ano in anos:
            ref = cvm.get((cd, ano))
            dfp = None if ref is None else ("consolidada" if ref["consolidado"] else "individual")
            linhas_b = [por_ticker_ano[(t, ano)] for t in tickers if (t, ano) in por_ticker_ano]
            for campo, col, chave in (("lucro", "net_income", "lucro"), ("pl", "equity", "pl")):
                tk, b = _primeiro(linhas_b, col)
                _linha(campo, ano, cd, tk or tickers[0], nome, b,
                       ref.get(chave) if ref else None, dfp)

    # LPA é por ticker; units ficam fora (o LPA da DFP é por ação).
    for r in uni[uni["asset_type"] == "stock"].itertuples(index=False):
        cd, tk = int(r.cd_cvm), str(r.ticker)
        for ano in anos:
            ref = cvm.get((cd, ano))
            c = ref["lpa_por_classe"].get(tk[-1:]) if ref else None
            b = por_ticker_ano.get((tk, ano), {}).get("eps")
            _linha("lpa", ano, cd, tk, r.nome, b, c, None)
    return pd.DataFrame(linhas)


def resumo(df) -> str:
    import pandas as pd

    if df.empty:
        return "Nenhum par brapi x CVM encontrado."
    partes = []
    tab = (df.pivot_table(index=["campo", "ano"], columns="categoria", values="cd_cvm",
                          aggfunc="count", fill_value=0)
             .reindex(columns=[c for c in CATEGORIAS if c in set(df["categoria"])]))
    partes.append("Pares por campo, exercício e categoria:\n" + tab.to_string())

    ultimo = int(df["ano"].max())
    u = df[df["ano"] == ultimo]
    ruins = u[u["categoria"].isin(["forte", "sinal_trocado", "escala"])]
    comparaveis = u[~u["categoria"].isin(["so_brapi", "so_cvm"])]
    n_emp = comparaveis["cd_cvm"].nunique()
    n_ruins = ruins["cd_cvm"].nunique()
    pct = f"{100 * n_ruins / n_emp:.1f}%" if n_emp else "-"
    partes.append(
        f"\nExercício {ultimo}: {n_ruins} de {n_emp} empresas comparáveis ({pct}) "
        "têm ao menos um campo com divergência forte, sinal trocado ou erro de escala.")

    top = (ruins.assign(_ord=ruins["divergencia"].fillna(9.0))
                .sort_values("_ord", ascending=False).head(25))
    if not top.empty:
        fmt = top[["ticker", "campo", "categoria", "brapi", "cvm", "divergencia"]].copy()
        fmt["divergencia"] = fmt["divergencia"].map(
            lambda v: "-" if pd.isna(v) else f"{100 * v:.0f}%")
        partes.append(f"\nMaiores divergências de {ultimo} (até 25):\n"
                      + fmt.to_string(index=False))
    return "\n".join(partes)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anos", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--dfp-dir", type=Path,
                    default=RAIZ / "data" / "cache" / "cvm" / "dfp")
    ap.add_argument("--armazem", action="store_true",
                    help="lê a brapi do armazém local em vez do banco do app")
    ap.add_argument("--saida", type=Path,
                    default=RAIZ / "data" / "cache" / "medicao_brapi_cvm.csv")
    args = ap.parse_args(argv)

    import sqlalchemy as sa

    if args.armazem:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        eng, origem = sa.create_engine(os.getenv("DFU_WAREHOUSE_URL") or _warehouse_url()), \
            "armazém local"
    else:
        from core.database import get_engine
        eng, origem = get_engine(), "banco do app (DATABASE_URL)"
    if eng is None:
        print("Sem banco configurado (DATABASE_URL). Use --armazem.")
        return 2

    print(f"brapi: {origem}; anos {args.anos}")
    universo, brapi = _ler_brapi(eng, args.anos)
    print(f"  {universo['cd_cvm'].nunique()} empresas no universo; "
          f"{brapi['cd_cvm'].nunique()} com algum dado anual da brapi")
    cvm, lidos = _ler_cvm(args.dfp_dir, args.anos, set(universo["cd_cvm"].astype(int)))
    if not lidos:
        print("Nenhuma DFP lida: confira --dfp-dir.")
        return 2
    print(f"  exercícios medidos: {lidos}")
    df = medir(universo, brapi, cvm, lidos)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.saida, index=False, encoding="utf-8-sig")
    print()
    print(resumo(df))
    print(f"\nDetalhe por empresa: {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

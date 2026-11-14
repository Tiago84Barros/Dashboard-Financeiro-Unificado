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


def _ler_cvm(dfp_dir: Path, anos: list[int], cds: set[int]) -> dict:
    from data_pipeline.market.b3_saidas import fundamentos, ler_dfp, lpa_da_classe

    out: dict[tuple[int, int], dict] = {}
    for ano in anos:
        arq = dfp_dir / f"dfp_cia_aberta_{ano}.zip"
        if not arq.exists():
            print(f"  DFP {ano}: ausente em {arq} -- ano fora da medição")
            continue
        dems = ler_dfp(arq.read_bytes(), ano, cds)
        for cd, dem in dems.items():
            f = fundamentos(dem)
            out[(int(cd), int(ano))] = {
                "lucro": f.get("net_income"), "pl": f.get("equity"),
                "lpa_por_classe": {k: lpa_da_classe(dem.contas, k) for k in "345678"},
                "consolidado": bool(dem.consolidado),
            }
        print(f"  DFP {ano}: {len(dems)} empresas lidas")
    return out


def medir(brapi, cvm: dict):
    """Linhas de comparação: lucro e PL por empresa, LPA por ticker."""
    import pandas as pd

    linhas = []
    # Lucro e PL são da companhia: um ticker por (empresa, ano), o primeiro
    # que tem dado na brapi, para não contar PETR3 e PETR4 como duas empresas.
    por_empresa = (brapi.sort_values(["cd_cvm", "ano", "ticker"])
                   .assign(_tem=lambda d: d["net_income"].notna() | d["equity"].notna())
                   .sort_values("_tem", ascending=False, kind="stable")
                   .drop_duplicates(["cd_cvm", "ano"]))
    for r in por_empresa.itertuples(index=False):
        ref = cvm.get((int(r.cd_cvm), int(r.ano)))
        for campo, b, c in (("lucro", r.net_income, ref and ref["lucro"]),
                            ("pl", r.equity, ref and ref["pl"])):
            cat, rel = classificar(b, c)
            if cat != "nenhum":
                linhas.append({"campo": campo, "ano": int(r.ano), "cd_cvm": int(r.cd_cvm),
                               "ticker": r.ticker, "nome": r.nome, "brapi": _num(b),
                               "cvm": _num(c), "categoria": cat, "divergencia": rel,
                               "dfp": None if ref is None else
                               ("consolidada" if ref["consolidado"] else "individual")})
    for r in brapi[brapi["asset_type"] == "stock"].itertuples(index=False):
        ref = cvm.get((int(r.cd_cvm), int(r.ano)))
        classe = str(r.ticker)[-1:]
        c = ref["lpa_por_classe"].get(classe) if ref else None
        cat, rel = classificar(r.eps, c)
        if cat != "nenhum":
            linhas.append({"campo": "lpa", "ano": int(r.ano), "cd_cvm": int(r.cd_cvm),
                           "ticker": r.ticker, "nome": r.nome, "brapi": _num(r.eps),
                           "cvm": _num(c), "categoria": cat, "divergencia": rel,
                           "dfp": None})
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
    _, brapi = _ler_brapi(eng, args.anos)
    print(f"  {brapi['cd_cvm'].nunique()} empresas com algum dado anual da brapi")
    cvm = _ler_cvm(args.dfp_dir, args.anos, set(brapi["cd_cvm"].astype(int)))
    if not cvm:
        print("Nenhuma DFP lida: confira --dfp-dir.")
        return 2
    df = medir(brapi, cvm)
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.saida, index=False, encoding="utf-8-sig")
    print()
    print(resumo(df))
    print(f"\nDetalhe por empresa: {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

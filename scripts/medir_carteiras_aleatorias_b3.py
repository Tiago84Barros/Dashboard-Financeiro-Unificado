# -*- coding: utf-8 -*-
"""Carteiras sorteadas da B3: 15 ou 30 ações? E as de regra, habilidade ou sorte?

Desenho e regra de decisão na §22 de `docs/guia_metodologia_carteira_b3.md`,
registrados antes de medir. Duas perguntas:

1. Por tamanho N (10, 15, 20, 30, 40): 1.000 carteiras sorteadas do universo
   de cada safra, pesos iguais, trocadas toda safra. Quantas batem pesos
   iguais, Ibovespa e Selic, e quanto espalham.
2. Cada carteira de regra (§19, §20 e a carteira padrão com os mesmos nomes em
   pesos iguais) entre 1.000 sorteios com o mesmo número de nomes que ela teve
   em cada safra: o percentil dela, líquido e bruto.

A régua é a mesma das §18–§20 (`medir_escolha`, recorte de
`medir_estrategias_fundamentalistas_b3.montar_recorte`). Antes de sortear, o
script confere que a régua com ``memo`` dá o mesmo resultado que sem ele.

Uso::

    python scripts/medir_carteiras_aleatorias_b3.py --cache-dir <pasta>
    python scripts/medir_carteiras_aleatorias_b3.py --cache-dir <pasta> --sorteios 20 --dry-run

O armazém local precisa estar de pé. Nada é gravado em banco nenhum.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO = ROOT / "data" / "oos_carteiras_aleatorias_b3.json"
TAMANHOS = (10, 15, 20, 30, 40)
SORTEIOS = 1000
SEMENTE = 20261123
PERCENTIS = (5, 10, 25, 50, 75, 90, 95)
CARTEIRA_PADRAO_EW = "carteira_padrao_pesos_iguais"
LIMIAR_CHANCE = 0.05  # 5 pontos: "30 dá mais chance que 15" (§22)
LIMIAR_HABILIDADE = 0.95  # acima do percentil 95, líquido e bruto (§22)


def sortear(universo, n: int, rng) -> list[str]:
    """``n`` nomes sem reposição; com menos de ``n`` no universo, todos."""
    nomes = sorted(str(t) for t in universo)
    if n <= 0 or not nomes:
        return []
    if n >= len(nomes):
        return nomes
    return sorted(rng.choice(nomes, size=n, replace=False).tolist())


def _media(valores):
    v = [x for x in valores if x is not None]
    return sum(v) / len(v) if v else None


def resumo_do_sorteio(m: dict, ibov: dict, selic: dict) -> dict:
    """O que cada sorteio guarda: excesso médio sobre os três benchmarks."""
    safras = m["safras"]
    return {
        "ew": m["vs_equal_weight"]["media"],
        "ew_bruto": m["bruto_vs_equal_weight"]["media"],
        "ibov": _media([t["liquido"] - ibov[t["safra"]] for t in safras
                        if ibov.get(t["safra"]) is not None]),
        "selic": _media([t["liquido"] - selic[t["safra"]] for t in safras
                         if selic.get(t["safra"]) is not None]),
        "giro": m["giro_medio_anual"],
    }


def distribuicao(valores: list[float]) -> dict:
    import numpy as np

    v = np.asarray([x for x in valores if x is not None], dtype=float)
    if not len(v):
        return {"n": 0}
    return {
        "n": int(len(v)),
        "media": float(v.mean()),
        "fracao_acima_de_zero": float((v > 0).mean()),
        "percentis": {str(p): float(np.percentile(v, p)) for p in PERCENTIS},
    }


def percentil_de(valor: float | None, valores: list[float]) -> float | None:
    """Fração dos sorteios abaixo de ``valor`` (empate conta meio)."""
    v = [x for x in valores if x is not None]
    if valor is None or not v:
        return None
    abaixo = sum(1 for x in v if x < valor)
    iguais = sum(1 for x in v if x == valor)
    return (abaixo + 0.5 * iguais) / len(v)


def conferir_memo(medir, por_safra, rng, k: int = 3) -> None:
    """A régua com ``memo`` tem de dar o mesmo que sem ele (§22)."""
    memo: dict = {}
    for i in range(k):
        n = (10, 25, 60)[i % 3]
        nomes = {p["safra"]: sortear(p["universo"], n, rng) for p in por_safra}
        escolha = lambda p, nomes=nomes: nomes[p["safra"]]  # noqa: E731
        a, b = medir(escolha), medir(escolha, memo)
        if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True,
                                                                    default=str):
            raise SystemExit(f"régua com memo diverge da régua sem memo (sorteio {i})")
    print(f"memo conferido: {k} sorteios iguais com e sem memo", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="reaproveita a rodada da aba (o mesmo pickle de "
                         "medir_oos_carteira_b3.py --cache-dir)")
    ap.add_argument("--sorteios", type=int, default=SORTEIOS)
    ap.add_argument("--dry-run", action="store_true", help="mede e imprime sem gravar")
    args = ap.parse_args()

    import numpy as np

    from scripts.medir_estrategias_fundamentalistas_b3 import montar_recorte

    rc = montar_recorte(args.cache_dir)
    oos, fe, medir, por_safra = rc["oos"], rc["fe"], rc["medir"], rc["por_safra"]
    conferir_memo(medir, por_safra, np.random.default_rng([SEMENTE, 0]))

    memo: dict = {}
    carteiras = dict(rc["escolhas"])
    carteiras[CARTEIRA_PADRAO_EW] = lambda p: rc["lideres_padrao"](p["safra"])
    reais = {k: medir(e, memo) for k, e in carteiras.items()}
    ibov = {p["safra"]: oos.retorno_benchmark(rc["ibov"], rc["precos"], p["safra"])
            for p in por_safra}
    selic = {s: v["base"]["retorno_selic"] for s, v in memo.items()}

    def rodar(tamanho_da_safra, bloco: int) -> list[dict]:
        rng = np.random.default_rng([SEMENTE, bloco])
        out, t0 = [], time.time()
        for i in range(args.sorteios):
            nomes = {p["safra"]: sortear(p["universo"], tamanho_da_safra(p["safra"]), rng)
                     for p in por_safra}
            m = medir(lambda p, nomes=nomes: nomes[p["safra"]], memo)
            out.append(resumo_do_sorteio(m, ibov, selic))
            if i == 19:
                print(f"  20 sorteios em {time.time() - t0:.1f}s", flush=True)
        return out

    # Pergunta 1: por tamanho.
    por_tamanho = {}
    for n in TAMANHOS:
        print(f"N = {n}", flush=True)
        s = rodar(lambda _safra, n=n: n, bloco=n)
        por_tamanho[str(n)] = {
            "vs_equal_weight": distribuicao([x["ew"] for x in s]),
            "vs_equal_weight_bruto": distribuicao([x["ew_bruto"] for x in s]),
            "vs_ibovespa": distribuicao([x["ibov"] for x in s]),
            "vs_selic": distribuicao([x["selic"] for x in s]),
            "giro_medio_anual": _media([x["giro"] for x in s]),
        }

    def faixa(n):
        pc = por_tamanho[str(n)]["vs_equal_weight"]["percentis"]
        return pc["95"] - pc["5"]

    def fracao(n, chave):
        return por_tamanho[str(n)][chave]["fracao_acima_de_zero"]

    decisao_tamanho = {
        "faixa_p5_p95_15": faixa(15), "faixa_p5_p95_30": faixa(30),
        "30_protege_mais_que_15": faixa(30) < faixa(15),
        "30_da_mais_chance_que_15": {
            chave: fracao(30, chave) - fracao(15, chave) >= LIMIAR_CHANCE
            for chave in ("vs_equal_weight", "vs_ibovespa", "vs_selic")},
    }

    # Pergunta 2: cada carteira de regra entre sorteios do mesmo tamanho.
    por_carteira = {}
    for bloco, (rotulo, m) in enumerate(reais.items(), start=1000):
        tamanhos = {t["safra"]: int(t["n_carteira"]) for t in m["safras"]}
        print(f"{rotulo}: tamanhos {tamanhos}", flush=True)
        s = rodar(lambda safra, tamanhos=tamanhos: tamanhos.get(safra, 0), bloco=bloco)
        ew, ew_bruto = m["vs_equal_weight"]["media"], m["bruto_vs_equal_weight"]["media"]
        pct = percentil_de(ew, [x["ew"] for x in s])
        pct_bruto = percentil_de(ew_bruto, [x["ew_bruto"] for x in s])
        if pct is None or pct_bruto is None:
            leitura = "sem medida"
        elif pct > LIMIAR_HABILIDADE and pct_bruto > LIMIAR_HABILIDADE:
            leitura = "acima do p95: separa-se da sorte"
        elif min(pct, pct_bruto) >= 0.5:
            leitura = "entre p50 e p95: não se separa da sorte"
        else:
            leitura = "abaixo do p50 em uma das medidas: pior que a sorte mediana"
        por_carteira[rotulo] = {
            "tamanhos_por_safra": tamanhos,
            "excesso_ew": ew, "excesso_ew_bruto": ew_bruto,
            "giro_medio_anual": m["giro_medio_anual"],
            "percentil_liquido": pct, "percentil_bruto": pct_bruto,
            "sorteios_vs_equal_weight": distribuicao([x["ew"] for x in s]),
            "sorteios_vs_equal_weight_bruto": distribuicao([x["ew_bruto"] for x in s]),
            "sorteios_giro_medio_anual": _media([x["giro"] for x in s]),
            "leitura": leitura,
        }
        print(f"  {rotulo}: excesso {ew:+.4f} pct liq {pct} pct bruto {pct_bruto} "
              f"-> {leitura}", flush=True)

    safras = sorted({t["safra"] for m in reais.values() for t in m["safras"]})
    saida = {
        "versao_medicao": fe.VERSAO,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": rc["nome"],
        "safras": safras,
        "desenho": "docs/guia_metodologia_carteira_b3.md §22 (registrado antes de medir)",
        "sorteios": args.sorteios, "semente": SEMENTE, "tamanhos": list(TAMANHOS),
        "metrica": ("excesso médio por safra, líquido do giro (custos PF), sobre "
                    "pesos iguais, Ibovespa (BOVA11) e Selic; bruto só contra pesos iguais"),
        "benchmarks_por_safra": {str(s): {"ibovespa": ibov.get(s), "selic": selic.get(s)}
                                 for s in safras},
        "por_tamanho": por_tamanho,
        "decisao_tamanho": decisao_tamanho,
        "por_carteira": por_carteira,
        "limites": [
            "nove safras: leitura fraca",
            "o sorteio troca 100% dos nomes por safra; as de regra trocam menos",
            "as carteiras de regra são correlacionadas; não há correção para isso",
            "pesos iguais e Ibovespa não pagam custo: viés contra as carteiras",
        ],
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if args.dry_run:
        print(json.dumps({"por_tamanho": por_tamanho, "decisao_tamanho": decisao_tamanho},
                         indent=1, ensure_ascii=False, default=str))
    else:
        CAMINHO.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

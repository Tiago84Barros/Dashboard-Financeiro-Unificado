# -*- coding: utf-8 -*-
"""Mede fora da amostra o filtro nota + fatores na rota "Líder de cada segmento".

A regra mora em `core/b3_nota_fatores.py::trocar_piores` e é a mesma que a
tela usa quando a caixa "Filtro nota + fatores (em teste)" está marcada:

- na safra, os piores 30% do sinal nota + fatores do universo inteiro (os
  ``fica=False`` de `carteira_nota_fatores`, a mesma carteira ampla medida em
  `scripts/medir_nota_fatores_b3.py`) não entram na carteira da líder;
- quem cai é trocado pelo próximo do ranking do PRÓPRIO segmento que não esteja
  entre os piores, nem barrado pela guarda de entrada, nem reprovado pelo piso
  de qualidade (quando o perfil o liga); o peso vai para o substituto;
  segmento sem substituto perde a vaga;
- o filtro roda depois do piso de qualidade, e os piores ficam barrados também
  nas trocas seguintes (guarda, tetos e vetos da banda), como na tela.

Régua: a de `scripts/medir_oos_carteira_b3.py::_medir_perfil` sem mudar nada
-- PIT, custo do giro, IR, caixa a Selic (D1) e pesos iguais no universo. A
medição roda duas vezes sobre a MESMA rodada da aba: sem o filtro (controle) e
com o filtro (o único passo trocado é `_segmentos_por_safra`). A comparação
principal é a diferença PAREADA, safra a safra, da carteira sem portão.

Regra de decisão registrada ANTES de medir (não é ajustada depois): o filtro
só vira padrão se a média da diferença pareada líquida for positiva e o IC 95%
excluir o zero. Senão fica opcional e desligado.

Uso::

    python scripts/medir_lider_filtro_nota_fatores_b3.py --cache-dir <pasta>
    python scripts/medir_lider_filtro_nota_fatores_b3.py --dry-run

O armazém local precisa estar de pé. Grava
`data/oos_lider_filtro_nota_fatores_b3.json`; nada vai para banco nenhum.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO = ROOT / "data" / "oos_lider_filtro_nota_fatores_b3.json"

REGRA_DE_DECISAO = (
    "liga por padrão só se a média da diferença pareada LÍQUIDA (com filtro "
    "menos sem filtro, carteira sem portão) for > 0 e o IC 95% excluir o zero; "
    "senão fica opcional e desligado")


def aplicar_filtro_na_safra(p: dict, piores, trocar_piores,
                            reprovado_no_piso=None) -> dict:
    """A safra ``p`` de `_segmentos_por_safra` com o filtro aplicado.

    Pura (não muda ``p``): troca os piores em cada segmento, tira o segmento
    que ficou vazio e põe os piores na guarda (``exclui``), para que as trocas
    seguintes da medição também os pulem. ``reprovado_no_piso`` barra só o
    substituto desta troca, como na tela.
    """
    ruins = frozenset(str(t) for t in piores)
    guarda = p["exclui"]
    piso = reprovado_no_piso or (lambda _tk: False)

    def barra_substituto(tk):
        return bool(guarda(tk)) or bool(piso(tk))

    segmentos = []
    trocas = []
    vagas = 0
    for setor, sel, pesos, ranking in p["segmentos"]:
        novos, pesos_n, tr = trocar_piores(sel, ranking, pesos, ruins,
                                           exclui=barra_substituto)
        trocas.extend(tr)
        vagas += max(0, len(sel) - len(novos))
        if novos:
            segmentos.append((setor, novos, pesos_n, ranking))
    q = dict(p)
    q["segmentos"] = segmentos
    q["exclui"] = lambda tk, _g=guarda, _r=ruins: bool(_g(tk)) or str(tk) in _r
    q["filtro"] = {"n_piores": len(ruins), "trocas": trocas, "vagas_perdidas": vagas,
                   "segmentos_perdidos": len(p["segmentos"]) - len(segmentos)}
    return q


def diferenca_pareada(controle: list[dict], filtrado: list[dict], chave: str) -> list[dict]:
    """Diferença safra a safra só onde as duas mediram (mesma safra)."""
    base = {int(t["safra"]): t for t in controle if t.get(chave) is not None}
    linhas = []
    for t in filtrado:
        s = int(t["safra"])
        if t.get(chave) is None or s not in base:
            continue
        linhas.append({"safra": s, "dif": float(t[chave]) - float(base[s][chave])})
    return linhas


def decidir(resumo: dict) -> bool:
    lo, hi = (resumo.get("ic95") or [None, None])
    return (resumo.get("media") is not None and resumo["media"] > 0
            and lo is not None and hi is not None and lo > 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="mede e imprime sem gravar")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="reaproveita a rodada da aba (o mesmo pickle de "
                         "medir_oos_carteira_b3.py --cache-dir)")
    args = ap.parse_args()

    from scripts.medir_nota_fatores_b3 import preparar

    prep = preparar(args.cache_dir)
    M, oos, nf = prep["M"], prep["oos"], prep["nf"]
    resultados, precos, params, decisao = prep["rodada"]
    ibov, cost_cfg, hoje, nome = prep["ibov"], prep["cost_cfg"], prep["hoje"], prep["perfil"]
    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION
    from core.b3_quality_floor import reprovados
    from core.b3_vigencia import janela_de_vigencia

    controle, _ = M._medir_perfil(resultados, precos, params, decisao, ibov, cost_cfg, hoje)

    res_ok = [r for r in resultados if r.get("_oos_ctx")]
    hist_batch = decisao.get("hist_batch") or {}
    pm = nf.precos_mensais(precos)
    original = M._segmentos_por_safra
    filtro_por_safra: dict[int, dict] = {}

    def com_filtro(*a, **k):
        contexto, por_safra = original(*a, **k)
        saida = []
        for p in por_safra:
            scores: dict[str, float] = {}
            for res in res_ok:
                scores.update(oos._scores_do_ano(res, p["safra"]))
            inicio, _ = janela_de_vigencia(p["safra"])
            tabela = nf.carteira_nota_fatores(p["universo"], scores, precos, hist_batch,
                                              inicio, precos_mes=pm)["tabela"]
            reprova = frozenset()
            if params["piso_qualidade"]:
                # Mesma Selic de N-1 que o piso da safra usou.
                selic_n = float(contexto["selic_macro"].get(
                    p["safra"] - 1, contexto["taxa_selic_aa"]))
                candidatos = {str(t) for _s, _sel, _p, rk in p["segmentos"]
                              for t, _sc in rk}
                reprova = reprovados(p["df_pit"], candidatos, selic=selic_n)
            q = aplicar_filtro_na_safra(p, nf.piores_da_tabela(tabela), nf.trocar_piores,
                                        reprovado_no_piso=reprova.__contains__)
            filtro_por_safra[int(p["safra"])] = q["filtro"]
            saida.append(q)
        return contexto, saida

    M._segmentos_por_safra = com_filtro
    try:
        filtrado, _ = M._medir_perfil(resultados, precos, params, decisao, ibov,
                                      cost_cfg, hoje)
    finally:
        M._segmentos_por_safra = original

    c = controle["variantes"][oos.SEM_PORTAO]
    f = filtrado["variantes"][oos.SEM_PORTAO]
    dif_liq = oos.resumir(diferenca_pareada(c["safras"], f["safras"], "liquido"), "dif")
    dif_ir = oos.resumir(diferenca_pareada(c["safras"], f["safras"], "liquido_com_ir"),
                         "dif")
    liga = decidir(dif_liq)

    def resumo_da(v):
        return {k: v[k] for k in ("vs_equal_weight", "vs_equal_weight_com_ir", "vs_selic",
                                  "safras_em_caixa", "safras_sem_carteira")}

    print(f"diferença pareada líquida: media={dif_liq['media']} ic95={dif_liq['ic95']} "
          f"n={dif_liq['n_safras']} neg={dif_liq['safras_negativas']}", flush=True)
    print(f"diferença pareada com IR: media={dif_ir['media']} ic95={dif_ir['ic95']}",
          flush=True)
    print(f"decisão pré-registrada: {'LIGAR por padrão' if liga else 'fica opcional'}",
          flush=True)

    safras = sorted(filtro_por_safra)
    saida = {
        # Mesma chave que `oos.vencida` lê para dizer se a medição venceu.
        "versao_medicao": nf.VERSAO_FILTRO_LIDER,
        "versao_nota_fatores": nf.VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": nome,
        "regra": {
            "piores": (f"os {nf.CORTE:.0%} piores do sinal nota + fatores do universo "
                       "da safra (fica=False de carteira_nota_fatores)"),
            "onde": "rota líder de cada segmento, depois do piso de qualidade",
            "troca": ("próximo do ranking do mesmo segmento fora dos piores, da "
                      "guarda de entrada e do piso de qualidade; herda o peso; sem "
                      "substituto a vaga se perde"),
            "trocas_seguintes": "os piores ficam barrados também na guarda da medição",
        },
        "regra_de_decisao": REGRA_DE_DECISAO,
        "decisao": "ligar por padrão" if liga else "fica opcional e desligado",
        "metrica_principal": ("diferença pareada por safra do retorno LÍQUIDO da "
                              "carteira sem portão: com filtro menos sem filtro"),
        "diferenca_pareada": {"liquido": dif_liq, "com_ir": dif_ir},
        "com_filtro": resumo_da(f),
        "sem_filtro": resumo_da(c),
        "trocas_por_safra": {str(s): filtro_por_safra[s] for s in safras},
        "fora_da_regra": [
            "portão de LLM (Inteligência e gate qualitativo) não roda na medição",
            "troca ON/PN do piso de liquidez da tela não é refeita aqui",
            "rota de revisão (b3_review) não aplica o filtro",
        ],
        "safras_por_carteira": {
            "com_filtro": [t["safra"] for t in f["safras"]],
            "sem_filtro": [t["safra"] for t in c["safras"]],
        },
    }
    texto = json.dumps(saida, indent=2, ensure_ascii=False, default=str)
    if args.dry_run:
        print(texto)
    else:
        CAMINHO.write_text(texto + "\n", encoding="utf-8")
        print(f"gravado em {CAMINHO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Mede fora da amostra o desconto de liderança da B3, ligado e desligado.

O desconto mora em `core/pesos_por_score.py::apply_decay_penalty`: a cada ano
consecutivo como líder do segmento, o score do líder perde 7% (teto de 30%).
O docstring diz para que serve: "força rotação de carteira". Isso contraria a
premissa original do usuário (22/11/2026), "não vender a líder do ano anterior
sem que os fundamentos piorem", e nunca foi medido na B3. Na Seleção de FIIs
ele já é medido ligado e desligado (`scripts/medir_safras_fii.py --decaimento`).

Como mede:

- a aba de Criação de Portfólio B3 roda DUAS vezes sem cabeça no perfil padrão
  (Equilibrado), com o LLM desligado: uma como está (controle) e outra com
  `views.portfolio_b3._apply_decay_penalty` trocado pela identidade
  (`scripts/medir_nota_fatores_b3.py::rodada_da_aba`). Cada uma tem o seu
  pickle com ``--cache-dir``;
- a régua é a de `scripts/medir_oos_carteira_b3.py::_medir_perfil`, sem mudar
  nada: PIT, custo do giro, IR, caixa a Selic (D1), pesos iguais no universo;
- a rodada sem desconto usa o MESMO fim de aquecimento do controle
  (``comeca_em``);
- a comparação principal é a diferença PAREADA, safra a safra, do retorno
  líquido da carteira sem portão: sem desconto menos com desconto.

O desconto muda o líder de cada ano e, por isso, o backtest de cada segmento:
a aprovação econômica do segmento também pode mudar. Isso faz parte do efeito
medido, como na tela.

Regra de decisão registrada ANTES de medir (não é ajustada depois): o desconto
só sai da B3 se a média da diferença pareada líquida for > 0 e o IC 95%
excluir o zero. Senão fica como está.

Uso::

    python scripts/medir_decaimento_lideranca_b3.py --cache-dir data/cache
    python scripts/medir_decaimento_lideranca_b3.py --dry-run

O armazém local precisa estar de pé. Grava
`data/oos_decaimento_lideranca_b3.json`; nada vai para banco nenhum.
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

CAMINHO = ROOT / "data" / "oos_decaimento_lideranca_b3.json"

VERSAO = "decaimento-lideranca-b3-1.0.0"

REGRA_DE_DECISAO = (
    "o desconto de liderança sai da B3 só se a média da diferença pareada "
    "LÍQUIDA (sem desconto menos com desconto, carteira sem portão) for > 0 e o "
    "IC 95% excluir o zero; senão fica ligado, como está")


def decidir(resumo: dict) -> bool:
    """True = tirar o desconto (a regra pré-registrada passou)."""
    lo, hi = (resumo.get("ic95") or [None, None])
    return (resumo.get("media") is not None and resumo["media"] > 0
            and lo is not None and hi is not None and lo > 0)


CAMPOS_LADO_A_LADO = ("n_ativos", "giro", "custo", "liquido", "maiores")


def lado_a_lado(controle: list[dict], sem: list[dict]) -> dict[str, dict]:
    """Por safra, giro, custo, retorno e maiores nomes das duas carteiras.

    O giro é a pergunta do usuário: o desconto força a troca de líder e cada
    troca paga custo e IR.
    """
    com = {int(t["safra"]): t for t in controle}
    sem_ = {int(t["safra"]): t for t in sem}
    return {str(s): {"com_desconto": {k: com.get(s, {}).get(k) for k in CAMPOS_LADO_A_LADO},
                     "sem_desconto": {k: sem_.get(s, {}).get(k) for k in CAMPOS_LADO_A_LADO}}
            for s in sorted(set(com) | set(sem_))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="mede e imprime sem gravar")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="reaproveita as rodadas da aba (o pickle de "
                         "medir_oos_carteira_b3.py --cache-dir e o sem desconto)")
    args = ap.parse_args()

    from scripts.medir_estrategias_fundamentalistas_b3 import diferenca_pareada
    from scripts.medir_nota_fatores_b3 import preparar, rodada_da_aba

    prep = preparar(args.cache_dir)
    M, oos, view = prep["M"], prep["oos"], prep["view"]
    resultados, precos, params, decisao = prep["rodada"]
    ibov, cost_cfg, hoje, nome = prep["ibov"], prep["cost_cfg"], prep["hoje"], prep["perfil"]

    from core.b3_methodology import SCORE_VERSION
    from core.b3_portfolio_presets import PRESETS
    from core.b3_portfolio_presets import VERSION as PRESETS_VERSION

    controle, _ = M._medir_perfil(resultados, precos, params, decisao, ibov, cost_cfg, hoje)
    r2, p2, par2, d2 = rodada_da_aba(M, view, nome, dict(PRESETS[nome].valores),
                                     args.cache_dir, sem_decaimento=True)
    # Mesmo fim de aquecimento: safra que uma das duas esvazia conta como caixa.
    sem, _ = M._medir_perfil(r2, p2, par2, d2, ibov, cost_cfg, hoje,
                             comeca_em=controle["comeca_em"])

    c = controle["variantes"][oos.SEM_PORTAO]
    f = sem["variantes"][oos.SEM_PORTAO]
    dif_liq = oos.resumir(diferenca_pareada(c["safras"], f["safras"], "liquido"), "dif")
    dif_ir = oos.resumir(diferenca_pareada(c["safras"], f["safras"], "liquido_com_ir"),
                         "dif")
    tira = decidir(dif_liq)

    def resumo_da(v):
        return {k: v.get(k) for k in ("vs_equal_weight", "vs_equal_weight_com_ir",
                                      "vs_selic", "safras_em_caixa",
                                      "safras_sem_carteira")}

    print(f"diferença pareada líquida (sem - com): media={dif_liq['media']} "
          f"ic95={dif_liq['ic95']} n={dif_liq['n_safras']}", flush=True)
    print(f"diferença pareada com IR: media={dif_ir['media']} ic95={dif_ir['ic95']}",
          flush=True)
    print(f"decisão pré-registrada: {'TIRAR o desconto' if tira else 'fica ligado'}",
          flush=True)

    saida = {
        "versao_medicao": VERSAO,
        "versao_metodologia": SCORE_VERSION,
        "versao_presets": PRESETS_VERSION,
        "medido_em": datetime.now().isoformat(timespec="seconds"),
        "perfil": nome,
        "desconto": ("core.pesos_por_score.apply_decay_penalty: 7% a.a. por ano "
                     "consecutivo de liderança, teto 30%; desligado = identidade "
                     "nas duas chamadas da aba (laço anual e carteira atual)"),
        "regra_de_decisao": REGRA_DE_DECISAO,
        "decisao": "tirar o desconto" if tira else "fica ligado",
        "metrica_principal": ("diferença pareada por safra do retorno LÍQUIDO da "
                              "carteira sem portão: sem desconto menos com desconto"),
        "diferenca_pareada": {"liquido": dif_liq, "com_ir": dif_ir},
        "com_desconto": resumo_da(c),
        "sem_desconto": resumo_da(f),
        "por_safra": lado_a_lado(c["safras"], f["safras"]),
        "comeca_em": controle["comeca_em"],
        "safras_por_carteira": {
            "com_desconto": [t["safra"] for t in c["safras"]],
            "sem_desconto": [t["safra"] for t in f["safras"]],
        },
        "limites": [
            "o desconto muda o líder e, com ele, o backtest e a aprovação de cada "
            "segmento: a diferença mede os dois efeitos juntos, como na tela",
            "nove safras não separam sorte de regra com folga",
        ],
        "fora_da_regra": [
            "portão de LLM (Inteligência e gate qualitativo) não roda na medição",
            "o histórico de compras da aba (aporte que nunca vende) não é a régua; "
            "a régua rebalanceia em abril, como a carteira recomendada",
        ],
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

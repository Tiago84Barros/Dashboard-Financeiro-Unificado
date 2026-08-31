"""Confere a vitrine de FIIs pelo mesmo caminho que a tela usa.

Publicar sem verificar seria repetir o defeito que originou esta rotina: em
31/08/2026 a vitrine venceu, a leitura devolveu linhas sem metrica e a tela
creditou a falha aos filtros de elegibilidade (PR #190). Aqui a checagem passa
por `load_fii_methodology_inputs`, e nao pela tabela crua, porque e essa a
funcao cuja saida a decisao consome.

Sai com codigo 1 quando a vitrine nao serve; 0 quando serve, ainda que velha.
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-idade-dias", type=int, default=None,
                        help="Reprova se a vitrine for mais velha que isto.")
    args = parser.parse_args()

    import core.market_read as mr

    mr._reset_fii_snapshot_memory_cache()
    mr.load_fii_methodology_inputs.clear()
    frame = mr.load_fii_methodology_inputs()

    erro = frame.attrs.get("load_error")
    idade = frame.attrs.get("snapshot_age_days")
    print(f"linhas={len(frame)} erro={erro} "
          f"as_of={frame.attrs.get('snapshot_as_of')} idade={idade} "
          f"aviso={frame.attrs.get('snapshot_stale_warning')}")

    if erro:
        print(f"REPROVADO: a vitrine nao pode ser lida ({erro}).")
        return 1
    if frame.empty:
        print("REPROVADO: a vitrine foi lida vazia.")
        return 1

    # Nao basta ter linhas: o que reprovou os 394 fundos foi um quadro cheio
    # de linhas e vazio das colunas que a decisao le.
    exigidas = ("dy_12m", "pvp", "liquidez_diaria", "history_months", "max_drawdown")
    faltando = [c for c in exigidas if c not in frame.columns]
    if faltando:
        print(f"REPROVADO: faltam as colunas que a elegibilidade le: {faltando}")
        return 1

    if args.max_idade_dias is not None and idade is not None:
        if int(idade) > args.max_idade_dias:
            print(f"REPROVADO: idade {idade}d acima do limite {args.max_idade_dias}d.")
            return 1

    print("OK: vitrine legivel, com as colunas de decisao presentes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

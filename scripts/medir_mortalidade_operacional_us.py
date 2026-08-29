# -*- coding: utf-8 -*-
"""Refaz a mortalidade americana na populacao que o painel de fato analisa.

`scripts/medir_mortalidade_us.py` mede sobre todo CIK que arquivou relatorio
anual: 9.686 em 2010, 70,07% desaparecidos ate 2025. Esse numero vai para a tela
e para o portao "Universo de deslistadas", e e com ele que o usuario desconta o
retorno historico. So que ali dentro ha trust de leasing, emissor de ABS,
subsidiaria de seguradora, fundo fechado e emissor estrangeiro de 20-F. O painel
analisa acao operacional americana.

Veiculo termina por desenho -- a carteira do trust vence, o ABS e liquidado --
e contar isso como morte de empresa infla o desconto. Este script refaz a conta
sobre `market_us.sec_entidade` (SIC por CIK, servido pela SEC inclusive para
quem morreu) usando `core.us_universo_sec`, e grava as DUAS coortes: a ampla,
que continua respondendo "quantos arquivadores sumiram", e a operacional, que
responde a pergunta do painel.

    python scripts/medir_mortalidade_operacional_us.py [--anos 2010 2015 2020 2025]
                                                       [--aplicar]

Sem `--aplicar` nada e gravado em disco.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.us_survivorship import (  # noqa: E402
    CAMINHO_MEDICAO,
    COBERTURA_IDENTIDADE_MINIMA_PCT,
    carregar_medicao,
    ciks_com_relatorio_anual,
    ciks_com_relatorio_anual_operacional,
    coorte_operacional_verificada,
    gravar_medicao,
    medir_mortalidade,
    restringir_a_operacionais,
)
from core.us_universo_sec import particionar  # noqa: E402
from scripts.medir_mortalidade_us import painel_por_ano  # noqa: E402


def _entidades():
    from sqlalchemy import create_engine, text

    from scripts.publish_fii_selection_from_local import _warehouse_url
    eng = create_engine(
        _warehouse_url().replace("postgresql://", "postgresql+psycopg2://"))
    with eng.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(
            "SELECT cik, nome, sic, sic_descricao FROM market_us.sec_entidade "
            "WHERE http_status = 200"))]


def _ciks_por_ano(anos: list[int], cache: Path, *, operacional: bool = True
                   ) -> dict[int, set[int]]:
    """Lê a coorte doméstica ou a ampla, explicitamente, do mesmo índice SEC."""
    leitor = (ciks_com_relatorio_anual_operacional if operacional
              else ciks_com_relatorio_anual)
    por_ano: dict[int, set[int]] = {}
    for ano in anos:
        ciks: set[int] = set()
        for q in (1, 2, 3, 4):
            arq = cache / f"{ano}Q{q}.idx"
            if not arq.exists():
                raise SystemExit(
                    f"indice {arq.name} ausente -- rode antes "
                    f"scripts/medir_mortalidade_us.py --dry-run")
            ciks |= leitor(
                arq.read_text(encoding="latin-1", errors="ignore"))
        por_ano[ano] = ciks
    return por_ano


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anos", type=int, nargs="+", default=[2010, 2015, 2020, 2025])
    ap.add_argument("--cache", default=None)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args(argv)

    cache = Path(args.cache) if args.cache else ROOT / ".cache" / "sec_full_index"
    anos = sorted(set(args.anos))
    # O denominador operacional nasce sem 20-F. A leitura ampla é preservada
    # apenas para declarar quantos arquivadores estrangeiros ficaram fora.
    por_ano = _ciks_por_ano(anos, cache, operacional=True)
    por_ano_ampla = _ciks_por_ano(anos, cache, operacional=False)
    base_ano = anos[0]

    part = particionar(_entidades())
    print(f"identidade apurada: {sum(len(v) for v in part.values())} CIKs "
          f"({len(part['operacionais'])} operacionais, "
          f"{len(part['veiculos'])} veiculos, "
          f"{len(part['nao_classificados'])} sem SIC)")

    base = por_ano[base_ano]
    identidade = set().union(*part.values())
    sem_identidade = base - identidade
    nao_classificados = base & part["nao_classificados"]
    desconhecidos = sem_identidade | nao_classificados
    # Cobertura é a fração CLASSIFICADA, não a consultada: a consultada já é
    # dita por `sem_identidade_apurada`, e repetir o mesmo fato em dois campos
    # deixaria o limiar sem nada para medir.
    classificados = base & (part["operacionais"] | part["veiculos"])
    cobertura_identidade_pct = round(100.0 * len(classificados) / len(base), 2) if base else 0.0
    if sem_identidade or not base:
        # CIK que ninguem consultou e lacuna de execucao, nao incerteza medida:
        # nao tem tamanho conhecido e por isso bloqueia. Ja o sem SIC tem
        # tamanho, e vira banda logo abaixo.
        print("Mortalidade operacional NÃO VERIFICADO: "
              f"denominador {len(base)}, "
              f"{len(sem_identidade)} CIKs da coorte {base_ano} sem identidade "
              f"consultada; rode scripts/classificar_entidades_sec.py --aplicar")
        if args.aplicar:
            print("[bloqueado] --aplicar exige a coorte-base inteiramente consultada.")
            return 2
        print("[dry-run] nada gravado.")
        return 0

    # Cobertura baixa e recusa limpa, nao excecao: com poucos classificados a
    # banda deixa de ser estreita e a conta deixa de responder a pergunta. Medir
    # antes de chamar `medir_mortalidade` evita que o caso degenerado (nenhum
    # operacional em ano nenhum) suba como ValueError em vez de veredito.
    op = restringir_a_operacionais(por_ano, part["operacionais"])
    if (cobertura_identidade_pct < COBERTURA_IDENTIDADE_MINIMA_PCT
            or sum(1 for ciks in op.values() if ciks) < 2):
        print("Mortalidade operacional NÃO VERIFICADO: cobertura de identidade "
              f"{cobertura_identidade_pct}% sobre {len(base)} CIKs da coorte "
              f"{base_ano} (mínimo {COBERTURA_IDENTIDADE_MINIMA_PCT}%); "
              f"{len(nao_classificados)} sem SIC informado pela SEC.")
        if args.aplicar:
            print("[bloqueado] --aplicar não gravou coorte sem cobertura suficiente.")
            return 2
        print("[dry-run] nada gravado.")
        return 0

    coorte_op = medir_mortalidade(
        op, restringir_a_operacionais(painel_por_ano(anos), part["operacionais"]) or None)

    # Os dois extremos de classificar o desconhecido. O desfecho de cada um e
    # observado no indice como o de qualquer outro CIK -- a duvida e so de
    # pertencimento -- entao a banda e exata, nao estimada.
    com_desconhecidos = medir_mortalidade(
        restringir_a_operacionais(por_ano, part["operacionais"] | part["nao_classificados"]))
    extremos = sorted((float(coorte_op["mortalidade_pct"]),
                       float(com_desconhecidos["mortalidade_pct"])))

    coorte_op.update({
        "populacao": "operacional",
        "regra": "core.us_universo_sec.classificar",
        "veiculos_excluidos": len(base & part["veiculos"]),
        "nao_classificados": len(nao_classificados),
        "sem_identidade_apurada": len(sem_identidade),
        "cobertura_identidade_pct": cobertura_identidade_pct,
        "estrangeiros_20f_excluidos": len(por_ano_ampla[base_ano] - base),
        "mortalidade_pct_min": extremos[0],
        "mortalidade_pct_max": extremos[1],
    })

    for ano, d in coorte_op["curva"].items():
        print(f"   operacionais da coorte {base_ano} vivas em {ano}: "
              f"{d['vivas']} ({d['sobrevivencia_pct']}%)")
    print(f"mortalidade operacional ate {coorte_op['ano_final']}: "
          f"{coorte_op['mortalidade_pct']}% "
          f"(banda {extremos[0]}%-{extremos[1]}% pelos {len(nao_classificados)} sem SIC; "
          f"cobertura de identidade {cobertura_identidade_pct}%)")
    print(f"fora da conta: {coorte_op['veiculos_excluidos']} veiculos, "
          f"{coorte_op['estrangeiros_20f_excluidos']} estrangeiros de 20-F")

    medicao = dict(carregar_medicao() or {})
    ampla = medicao.get("coorte") or {}
    if ampla.get("mortalidade_pct") is not None:
        print(f"(ampla, todo arquivador: {ampla['mortalidade_pct']}% sobre "
              f"{ampla['universo_base']} CIKs)")

    if not args.aplicar:
        print("[dry-run] nada gravado.")
        return 0
    if not coorte_operacional_verificada(coorte_op):
        print("[bloqueado] --aplicar não gravou coorte operacional fora do contrato.")
        return 2
    medicao["coorte_operacional"] = coorte_op
    print("gravado em", gravar_medicao(medicao, CAMINHO_MEDICAO))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

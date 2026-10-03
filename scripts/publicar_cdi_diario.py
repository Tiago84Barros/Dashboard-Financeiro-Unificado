"""Publica o CDI diário (BCB/SGS 12) em ``data/public/cdi_diario.json.gz``.

O app lê o arquivo e só pede ao BCB os dias que faltam depois dele -- ver
:func:`core.rentabilidade.obter_cdi`. Em 03/10/2026 a API REST do SGS sumiu do
DNS; :func:`core.rentabilidade.baixar_cdi_bcb` cai no SOAP (``core.bcb_sgs``).

Incremental: relê o arquivo atual e só pede ao BCB os dias depois do último
publicado (com 10 dias de sobreposição, porque o SGS às vezes corrige o
último valor). Sem arquivo, baixa desde ``--desde``.

Recusa gravar (saída 1) se o BCB falhar: renovar ``gerado_em`` sobre série
parada faria o arquivo parecer em dia.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A carteira mais antiga do app começa em 2019; 2010 sobra para quem importar
# extrato mais velho, e custa ~4 mil linhas (uns 30 KB comprimidos).
DESDE_PADRAO = date(2010, 1, 1)
SOBREPOSICAO_DIAS = 10


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=None)
    p.add_argument("--desde", type=date.fromisoformat, default=DESDE_PADRAO)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from core.rentabilidade import (
        CAMINHO_CDI_PUBLICADO,
        baixar_cdi_bcb,
        ler_cdi_publicado,
        serializar_cdi,
    )

    saida = args.saida or CAMINHO_CDI_PUBLICADO
    atual = {d: v for d, v in ler_cdi_publicado(saida).items() if d >= args.desde}
    hoje = date.today()
    inicio = (max(atual) - timedelta(days=SOBREPOSICAO_DIAS)
              if atual and min(atual) <= args.desde + timedelta(days=7) else args.desde)
    if not (atual and inicio > args.desde):
        atual = {}
    novos, falha = baixar_cdi_bcb(inicio, hoje, timeout=45.0)
    relatorio = {"saida": str(saida), "pedido_desde": inicio.isoformat(),
                 "linhas_novas": len(novos)}
    if falha or not novos:
        relatorio["erro"] = falha or "o BCB devolveu a série vazia"
        print(json.dumps(relatorio, ensure_ascii=False))
        return 1
    serie = {**atual, **novos}
    relatorio.update(linhas=len(serie), primeiro=min(serie).isoformat(),
                     ultimo=max(serie).isoformat())
    if not args.dry_run:
        saida.parent.mkdir(parents=True, exist_ok=True)
        saida.write_bytes(serializar_cdi(serie, datetime.now(timezone.utc)))
    print(json.dumps(relatorio, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

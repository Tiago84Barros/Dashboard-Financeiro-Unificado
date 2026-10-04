"""Publica o macro do BCB em ``data/public/macro_brasil.json.gz``.

Lê do armazém local (``MACRO_LOCAL_DB_URL``), só leitura, o que
``scripts/ingerir_macro_brasil.py`` gravou -- Selic meta, IPCA mensal e 12m e
Focus -- e grava o arquivo que o bloco CONTEXTO DE MERCADO lê em produção, onde
o armazém não é alcançável. Ver :mod:`core.macro_brasil`.

Recusa publicar (saída 1) se a coleta mais nova tiver mais de
``COLETA_MAXIMA_DIAS``: renovar a data do arquivo sobre coleta parada faria o
cenário velho parecer fresco. Sem publicação nova, o arquivo vence sozinho em
``core.macro_brasil.IDADE_MAXIMA_DIAS`` e o bloco passa a dizer que faltou.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A coleta roda todo dia; três dias sem linha nova de Selic meta (série diária)
# é coleta parada.
COLETA_MAXIMA_DIAS = 3


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from core import macro_brasil as mb
    from core.macro_data.database import get_local_macro_engine

    engine = get_local_macro_engine()
    if engine is None:
        print("MACRO_LOCAL_DB_URL não configurada -- este script lê o Docker local.")
        return 2
    agora = datetime.now(timezone.utc)
    try:
        obs = mb.ler_do_armazem(engine, hoje=agora.date())
    finally:
        engine.dispose()
    # A Selic meta é diária: o período dela mede a coleta melhor que
    # ``retrieved_at``, que não avança quando o valor repetido é deduplicado.
    selic = max((o["reference_period"] for o in obs
                 if o["provider"] == mb.PROVEDOR_SGS and o["provider_code"] == "432"),
                default=None)
    res = mb.resumo(obs)
    relatorio = {
        "observacoes": len(obs),
        "series": sorted(k for k in res),
        "selic_meta_ate": selic.isoformat() if selic else None,
    }
    if selic is None or (agora.date() - selic).days > COLETA_MAXIMA_DIAS:
        relatorio["erro"] = (f"Selic meta sem período nos últimos {COLETA_MAXIMA_DIAS} dias; "
                             "rode scripts/ingerir_macro_brasil.py antes de publicar")
        print(json.dumps(relatorio, ensure_ascii=False, sort_keys=True), flush=True)
        return 1

    dados = mb.serializar(agora, obs)
    destino = args.saida or mb.CAMINHO_PADRAO
    relatorio.update(bytes=len(dados), destino=str(destino), dry_run=args.dry_run)
    if not args.dry_run:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados)
    print(json.dumps(relatorio, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

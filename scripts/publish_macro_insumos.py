"""Publica os insumos macro das carteiras em ``data/public/macro_insumos.json.gz``.

Lê o banco macro do Docker local (``MACRO_LOCAL_DB_URL``), só leitura, e grava
o que ``load_portfolio_macro_snapshot`` consultaria agora: todas as exposições
setoriais e as 24 últimas observações de cada série, no modo ``strict``. O app
publicado lê esse arquivo quando não alcança o Docker -- ver
:mod:`core.macro_data.insumos_publicados`.

Recusa publicar (saída 1) se a observação mais nova foi coletada há mais de
``COLETA_MAXIMA_DIAS``: renovar a data do arquivo sobre coleta parada faria o
cenário velho parecer fresco. Sem publicação nova, o arquivo anterior vence
sozinho em ``IDADE_MAXIMA_DIAS`` e as telas voltam a dizer "indisponível".
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

# A coleta roda todo dia; uma semana sem linha nova é coleta parada.
COLETA_MAXIMA_DIAS = 7


def coletar(engine, *, agora: datetime) -> tuple[list[dict], list[dict]]:
    from sqlalchemy import text

    from core.macro_data.portfolio_context import _ler_insumos

    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        exposicoes = [dict(r) for r in conn.execute(text("""
            SELECT asset_class, sector, factor, sensitivity, confidence, channel
              FROM macro_sector_exposures
             ORDER BY asset_class, sector, factor
        """)).mappings()]
    # As observações não dependem da classe nem do setor: é a mesma consulta
    # do cálculo, no mesmo instante.
    _, observacoes = _ler_insumos(engine, asset_class="b3", sectors=[],
                                  as_of=agora, knowledge_mode="strict")
    observacoes = sorted(
        (dict(o) for o in observacoes),
        key=lambda o: (o["provider"], o["provider_code"], o.get("country_code") or "",
                       o["reference_period"]),
    )
    return exposicoes, observacoes


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from core.macro_data.database import get_local_macro_engine
    from core.macro_data.insumos_publicados import CAMINHO_PADRAO, serializar

    engine = get_local_macro_engine()
    if engine is None:
        print("MACRO_LOCAL_DB_URL não configurada -- este script lê o Docker local.")
        return 2
    agora = datetime.now(timezone.utc)
    exposicoes, observacoes = coletar(engine, agora=agora)
    coleta = max((o["retrieved_at"] for o in observacoes if o.get("retrieved_at")),
                 default=None)
    relatorio = {
        "exposicoes": len(exposicoes),
        "observacoes": len(observacoes),
        "series": len({(o["provider"], o["provider_code"], o.get("country_code"))
                       for o in observacoes}),
        "coleta_mais_recente": coleta.isoformat() if coleta else None,
    }
    if coleta is None or (agora - coleta).days > COLETA_MAXIMA_DIAS:
        relatorio["erro"] = (f"coleta macro parada há mais de {COLETA_MAXIMA_DIAS} dias; "
                             "rode run_macro_updates.py antes de publicar")
        print(json.dumps(relatorio, ensure_ascii=False, sort_keys=True), flush=True)
        return 1
    if not exposicoes:
        relatorio["erro"] = "macro_sector_exposures vazia"
        print(json.dumps(relatorio, ensure_ascii=False, sort_keys=True), flush=True)
        return 1

    dados = serializar(agora, exposicoes, observacoes)
    destino = args.saida or CAMINHO_PADRAO
    relatorio.update(bytes=len(dados), destino=str(destino), dry_run=args.dry_run)
    if not args.dry_run:
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados)
    print(json.dumps(relatorio, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

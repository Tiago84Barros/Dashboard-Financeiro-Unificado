"""Publica no Supabase o impacto macro por setor, para cada classe de ativo.

Por que este script existe
--------------------------
``macro_observations`` e ``macro_sector_exposures`` moram no Postgres do Docker
local — dezenas de milhares de linhas, e é por isso que moram lá. O app
publicado não alcança aquele banco, então as telas de carteira vinham exibindo
*"Camada macro do Docker local indisponível; os pesos permanecem
fundamentalistas"*: o dado existia, estava calculado, e só não atravessava.

Aqui ele atravessa. O acervo fica; **o resultado** vai.

O cálculo não é refeito
-----------------------
O impacto sai de ``load_portfolio_macro_snapshot``, exatamente a função que a
tela chamaria se estivesse na máquina do Docker. Reimplementar a agregação aqui
seria criar uma segunda fórmula para a mesma pergunta, e duas fórmulas
envelhecem em direções diferentes sem que nada quebre.

Por que por setor
-----------------
O impacto de um símbolo depende só do setor dele — as sensibilidades são
indexadas por ``(setor, fator)`` e o símbolo entra apenas como rótulo. Publicar
por setor é exato e independe de qual carteira estava aberta quando o script
rodou. Ver :mod:`core.macro_data.vitrine`.

Simulação por omissão; ``--apply`` grava.

    python scripts/publish_macro_vitrine.py            # simula
    python scripts/publish_macro_vitrine.py --apply    # grava
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from core.destino_local import e_local, url_da_engine  # noqa: E402
from core.macro_data import vitrine as vit  # noqa: E402
from core.macro_data.portfolio_context import (  # noqa: E402
    load_portfolio_macro_snapshot,
)

logger = logging.getLogger("publish_macro_vitrine")

_SQL_SETORES = text("""
    SELECT DISTINCT sector
      FROM macro_sector_exposures
     WHERE asset_class = :asset_class AND length(trim(sector)) > 0
     ORDER BY 1
""")


def _url_acervo() -> str:
    return str(os.getenv("MACRO_LOCAL_DB_URL") or "")


def _url_supabase() -> str:
    return str(os.getenv("SUPABASE_UNIFICADO_URL")
               or os.getenv("DATABASE_URL")
               or os.getenv("SUPABASE_DB_URL") or "")


def _setores(engine, asset_class: str) -> tuple[str, ...]:
    with engine.connect() as conn:
        return tuple(str(r[0]).strip()
                     for r in conn.execute(_SQL_SETORES,
                                           {"asset_class": asset_class}))


def publicar(*, aplicar: bool, classes: tuple[str, ...],
             knowledge_mode: str = "strict") -> dict:
    url_acervo, url_remoto = _url_acervo(), _url_supabase()
    if not url_acervo:
        raise RuntimeError("MACRO_LOCAL_DB_URL não configurada")
    if not url_remoto:
        raise RuntimeError("Supabase não configurado")

    acervo = create_engine(url_acervo, pool_pre_ping=True)
    if not e_local(acervo):
        # O acervo é a FONTE. Lê-lo de um destino remoto significaria que as
        # observações macro inteiras já foram parar na nuvem -- o problema que
        # este desenho existe para impedir.
        raise RuntimeError(
            f"a fonte macro não é local ({url_da_engine(acervo)}): "
            "as observações não deveriam estar fora do armazém")

    momento = datetime.now(timezone.utc)
    remoto = create_engine(url_remoto, pool_pre_ping=True)
    resumo: dict = {"destino": url_da_engine(remoto), "gerada_em": momento,
                    "classes": {}}
    try:
        for asset_class in classes:
            setores = _setores(acervo, asset_class)
            if not setores:
                resumo["classes"][asset_class] = {
                    "publicado": False,
                    "motivo": "nenhum setor mapeado em macro_sector_exposures",
                }
                continue
            # O setor entra como símbolo e como setor: a função devolve um
            # impacto por chave, e a chave aqui É o setor.
            snapshot = load_portfolio_macro_snapshot(
                acervo, asset_class=asset_class,
                assets={setor: setor for setor in setores},
                as_of=momento, knowledge_mode=knowledge_mode,
            )
            linha = {
                "setores": len(setores),
                "setores_com_impacto": len(snapshot.impacts),
                "series": snapshot.source_count,
                "as_of": snapshot.as_of,
            }
            if not aplicar:
                linha["publicado"] = False
                linha["motivo"] = "simulação: use --apply para gravar"
            else:
                linha.update(vit.publicar(
                    remoto, snapshot, asset_class=asset_class,
                    setores={setor: setor for setor in setores},
                    granularidade="setor", origem="armazém local",
                    gerada_em=momento))
            resumo["classes"][asset_class] = linha
        return resumo
    finally:
        acervo.dispose()
        remoto.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="grava no Supabase (por omissão, apenas simula)")
    parser.add_argument("--classe", action="append", choices=list(vit.CLASSES),
                        help="limita a publicação a uma classe (repetível)")
    args = parser.parse_args()

    resumo = publicar(aplicar=args.apply,
                      classes=tuple(args.classe or vit.CLASSES))
    print(f"destino: {resumo['destino']}")
    for classe, linha in resumo["classes"].items():
        detalhe = ", ".join(f"{k}={v}" for k, v in linha.items())
        print(f"{classe}: {detalhe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

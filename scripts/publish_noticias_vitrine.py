"""Publica no Supabase a leitura corrente do noticiário, por ativo.

O acervo mora no armazém local e não cabe na nuvem (~22 MB por janela de 30
dias, acumulando, contra 23 MB de folga). Este script é a ponte: lê o acervo
local, agrega pelo **mesmo** código que a tela usaria (``ponte.leituras_do_acervo``)
e grava uma linha por ativo no Supabase.

Agregar aqui com uma segunda fórmula seria o defeito clássico da casa: duas
implementações do mesmo cálculo envelhecem em direções diferentes e passam a
discordar sem que nada quebre. Por isso o publicador não tem fórmula.

Simulação por omissão; ``--apply`` grava. A gravação é remota e substitui a
vitrine inteira.

    python scripts/publish_noticias_vitrine.py            # simula
    python scripts/publish_noticias_vitrine.py --apply    # grava
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from core.conjuntura import ponte  # noqa: E402
from core.destino_local import e_local, url_da_engine  # noqa: E402
from core.noticias import vitrine as vit  # noqa: E402
from core.noticias.armazenamento import VERSAO_METODOLOGIA  # noqa: E402

logger = logging.getLogger("publish_noticias_vitrine")

_SQL_SIMBOLOS_DO_ACERVO = text("""
    SELECT DISTINCT upper(t.ticker) AS simbolo
      FROM noticias_itens i
      CROSS JOIN LATERAL jsonb_array_elements_text(
             COALESCE(i.entidades -> 'tickers', '[]'::jsonb)) AS t(ticker)
     WHERE COALESCE(i.publicado_em, i.coletado_em) >= :inicio
       AND length(trim(t.ticker)) > 0
     ORDER BY 1
""")


def _url_acervo() -> str:
    return str(os.getenv("NOTICIAS_LOCAL_DB_URL")
               or os.getenv("MACRO_LOCAL_DB_URL") or "")


def _url_supabase() -> str:
    return str(os.getenv("SUPABASE_UNIFICADO_URL")
               or os.getenv("DATABASE_URL")
               or os.getenv("SUPABASE_DB_URL") or "")


def _simbolos_do_acervo(engine, *, inicio: datetime) -> tuple[str, ...]:
    with engine.connect() as conn:
        return tuple(str(r[0]) for r in conn.execute(
            _SQL_SIMBOLOS_DO_ACERVO, {"inicio": inicio}))


def _simbolos_da_carteira(engine) -> tuple[str, ...]:
    """Ativos que o usuário detém ou estuda, ainda que sem notícia nenhuma.

    Eles entram na vitrine com ``valor`` nulo e o motivo ao lado. Deixá-los de
    fora faria a tela não conseguir separar "este ativo não teve notícia" de
    "este ativo não foi publicado", e as duas frases pedem providências opostas.
    """
    try:
        from core.noticias.universo_coleta import montar

        alvos, _ = montar("normal", engine=engine, limite=10_000)
        return tuple(alvos)
    except Exception as exc:  # noqa: BLE001 - cobertura menor, nunca aborto
        logger.warning("universo da carteira indisponível: %s", exc)
        return ()


def publicar(*, aplicar: bool, janela_dias: int, versao: str) -> dict:
    url_acervo, url_remoto = _url_acervo(), _url_supabase()
    if not url_acervo:
        raise RuntimeError("NOTICIAS_LOCAL_DB_URL não configurada")
    if not url_remoto:
        raise RuntimeError("Supabase não configurado")

    acervo = create_engine(url_acervo, pool_pre_ping=True)
    if not e_local(acervo):
        # O acervo é a FONTE. Ler de um destino remoto aqui significaria que
        # alguém já gravou o acervo inteiro na nuvem -- o problema que este
        # desenho existe para impedir -- e publicar em cima disso esconderia o
        # fato em vez de expô-lo.
        raise RuntimeError(
            f"a fonte do acervo não é local ({url_da_engine(acervo)}): "
            "o acervo não deveria estar fora do armazém")

    momento = datetime.now(timezone.utc)
    inicio = momento - timedelta(days=janela_dias)
    remoto = create_engine(url_remoto, pool_pre_ping=True)
    try:
        do_acervo = _simbolos_do_acervo(acervo, inicio=inicio)
        da_carteira = _simbolos_da_carteira(remoto)
        simbolos = tuple(sorted(set(do_acervo) | set(da_carteira)))
        if not simbolos:
            return {"publicado": False,
                    "motivo": "nenhum ativo no acervo nem na carteira",
                    "ativos": 0, "ativos_medidos": 0}

        leituras = ponte.leituras_do_acervo(
            acervo, simbolos=simbolos, as_of=momento, janela_dias=janela_dias)
        itens = sum(lt.n_itens for lt in leituras.values())
        medidos = sum(1 for lt in leituras.values() if lt.medida)

        resumo = {
            "ativos": len(simbolos), "ativos_medidos": medidos,
            "itens_no_acervo": itens, "janela_dias": janela_dias,
            "versao": versao, "do_acervo": len(do_acervo),
            "da_carteira": len(da_carteira),
            "destino": url_da_engine(remoto),
        }
        if not aplicar:
            resumo["publicado"] = False
            resumo["motivo"] = "simulação: use --apply para gravar"
            return resumo

        escrito = vit.publicar(
            remoto, leituras.values(), versao=versao,
            janela_dias=janela_dias, itens_no_acervo=itens,
            origem="armazém local", gerada_em=momento)
        resumo.update(escrito)
        return resumo
    finally:
        acervo.dispose()
        remoto.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="grava no Supabase (por omissão, apenas simula)")
    parser.add_argument("--janela-dias", type=int,
                        default=ponte.JANELA_NOTICIAS_DIAS)
    parser.add_argument("--versao", default=VERSAO_METODOLOGIA)
    args = parser.parse_args()

    resumo = publicar(aplicar=args.apply, janela_dias=args.janela_dias,
                      versao=args.versao)
    for chave, valor in resumo.items():
        print(f"{chave}: {valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

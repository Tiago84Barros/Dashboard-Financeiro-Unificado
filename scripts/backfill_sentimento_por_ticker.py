"""Preenche ``entidades.sentimento_por_ticker`` no acervo a partir do cache cru.

Por que este script existe
--------------------------
Até 01/10/2026 o acervo (``noticias_itens``) guardava só o tom do provedor para
o artigo inteiro. O tom que Alpha Vantage e Marketaux medem para cada ticker
chegava em ``Noticia.bruto`` e não era gravado. A coleta passou a gravá-lo; este
script recupera o que dá para recuperar das linhas antigas.

O que dá é pouco, e o script diz quanto: o cache cru
(``local_staging/noticias/cache``) só guarda as respostas recentes de cada
consulta. As linhas cujas respostas já saíram do cache ficam com o tom do
artigo, marcado como tal na publicação.

O que ele NÃO faz
-----------------
**Não re-coleta** e não toca em nenhuma outra coluna nem chave: a única escrita
é acrescentar ``sentimento_por_ticker`` ao JSON de ``entidades``. Só entram os
tickers que a linha já tem (``armazenamento.sentimento_por_ticker``) -- a
resolução de entidades de hoje não é refeita aqui.

Uso, no padrão do projeto (simulação por omissão):

    python scripts/backfill_sentimento_por_ticker.py
    python scripts/backfill_sentimento_por_ticker.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.noticias.armazenamento import sentimento_por_ticker  # noqa: E402
from core.noticias.cache import DIRETORIO_PADRAO  # noqa: E402
from core.noticias.provedores.alphavantage import AlphaVantage  # noqa: E402
from core.noticias.provedores.marketaux import Marketaux  # noqa: E402


def _provedor(carga: dict):
    """Classe do provedor pela forma da resposta; ``None`` para as outras."""
    if "sentiment_score_definition" in carga and "feed" in carga:
        return AlphaVantage
    if "data" in carga and "meta" in carga:
        return Marketaux
    return None


def tons_do_cache(diretorio: Path) -> tuple[dict, int]:
    """``{(provedor, url): {TICKER: tom}}`` e quantas respostas foram lidas.

    Com a mesma URL em mais de uma resposta, vale a gravada por último."""
    achados: dict[tuple[str, str], tuple[str, dict]] = {}
    respostas = 0
    for arq in sorted(diretorio.glob("*.json")):
        try:
            entrada = json.loads(arq.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        carga = entrada.get("carga") if isinstance(entrada, dict) else None
        cls = _provedor(carga) if isinstance(carga, dict) else None
        if cls is None:
            continue
        respostas += 1
        quando = str(entrada.get("gravado_em") or "")
        # ``_extrair`` só lê a carga; sem instância de rede nem chave de API.
        for item in cls._extrair(object.__new__(cls), carga):
            tons = (item.bruto or {}).get("ticker_sentiment")
            if not tons:
                continue
            chave = (cls.nome, item.url)
            if chave not in achados or achados[chave][0] <= quando:
                achados[chave] = (quando, tons)
    return {k: v[1] for k, v in achados.items()}, respostas


def planejar(linhas, tons: dict) -> list[tuple[str, dict]]:
    """(id_dedup, tom por ticker) das linhas que mudam. Puro."""
    plano = []
    for r in linhas:
        cru = tons.get((r["provedor"], r["url"]))
        if not cru:
            continue
        ent = r["entidades"] or {}
        if isinstance(ent, str):
            ent = json.loads(ent)
        novo = sentimento_por_ticker({"ticker_sentiment": cru},
                                     ent.get("tickers") or ())
        if novo and novo != ent.get("sentimento_por_ticker"):
            plano.append((r["id_dedup"], novo))
    return plano


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", type=Path, default=DIRETORIO_PADRAO)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    from core.noticias.destino import engine_acervo

    tons, respostas = tons_do_cache(args.cache)
    print(f"cache: {respostas} respostas de provedor com tom, "
          f"{len(tons)} URLs com tom por ticker")
    motor = engine_acervo()
    if motor is None:
        print("acervo local não configurado")
        return 1
    urls = sorted({u for _, u in tons})
    with motor.connect() as conn:
        linhas = [dict(r._mapping) for r in conn.execute(text(
            "SELECT id_dedup, provedor, url, entidades FROM noticias_itens "
            "WHERE provedor IN ('alphavantage', 'marketaux') "
            "AND url = ANY(:urls)"), {"urls": urls})]
    plano = planejar(linhas, tons)
    print(f"acervo: {len(linhas)} linhas casadas por URL; {len(plano)} "
          "ganham tom por ticker")
    if not args.apply:
        print("simulação: nada gravado (use --apply)")
        return 0
    with motor.begin() as conn:
        for id_dedup, novo in plano:
            conn.execute(text(
                "UPDATE noticias_itens SET entidades = entidades || "
                "jsonb_build_object('sentimento_por_ticker', "
                "CAST(:tom AS jsonb)) WHERE id_dedup = :id"),
                {"tom": json.dumps(novo), "id": id_dedup})
    motor.dispose()
    print(f"gravado: {len(plano)} linhas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

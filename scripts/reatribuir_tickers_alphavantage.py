"""Reaplica a atribuição de tickers do Alpha Vantage ao acervo local já gravado.

Até 26/09/2026 o provedor aceitava a lista ``ticker_sentiment`` inteira, e a
MSFT recebia matéria da Palo Alto e da Intel (ver ``RELEVANCIA_MINIMA_TICKER``
em :mod:`core.noticias.provedores.alphavantage`). A correção vale para a coleta
nova; este script corrige o que já está no acervo, porque a conjuntura lê 30
dias de janela e o resíduo pesaria na nota por um mês.

O banco não guarda o ``relevance_score`` cru. Só dá para corrigir o item cujo
payload original ainda existe em disco: o cache da coleta
(``local_staging/noticias/cache``) e os arquivos passados em ``--payload``
(resposta crua do ``NEWS_SENTIMENT``). Item sem payload fica como está, e o
script diz quantos são -- corrigir por palpite seria trocar um atribuidor
errado por outro.

O item passa pelo mesmo ``_extrair`` e pelo mesmo ``para_noticia`` da coleta,
com o universo de entidades carregado do mesmo jeito; só ``entidades.tickers``
é reescrita, e só para tirar ticker -- nunca para pôr. Página vazia (``pagina_vazia``) perde os tickers em vez de sumir:
apagar linha do acervo não é papel deste script.

Grava só no acervo local (``engine_acervo``), nunca no Supabase. Sem
``--aplicar`` é simulação.

    python scripts/reatribuir_tickers_alphavantage.py
    python scripts/reatribuir_tickers_alphavantage.py --payload av_*.json --aplicar
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from core.noticias import (
    coleta,  # noqa: E402
    universo_entidades,  # noqa: E402
)
from core.noticias.cache import DIRETORIO_PADRAO  # noqa: E402
from core.noticias.dedup import hash_url  # noqa: E402
from core.noticias.destino import engine_acervo  # noqa: E402
from core.noticias.normalizacao import url_canonica  # noqa: E402
from core.noticias.provedores.alphavantage import (  # noqa: E402
    AlphaVantage,
    pagina_vazia,
)

PROVEDOR = AlphaVantage.nome


def ler_feeds(caminhos) -> dict[str, dict]:
    """Itens crus por URL. Aceita o envelope do cache e a resposta crua."""
    itens: dict[str, dict] = {}
    for caminho in caminhos:
        try:
            carga = json.loads(Path(caminho).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(carga, dict) and "carga" in carga:
            carga = carga["carga"]
        feed = carga.get("feed") if isinstance(carga, dict) else None
        if not isinstance(feed, list):
            continue
        for cru in feed:
            if isinstance(cru, dict) and cru.get("url"):
                itens[cru["url"]] = cru
    return itens


def entidades_json(noticia) -> dict:
    ent = noticia.entidades
    return {"tickers": list(ent.tickers), "empresas": list(ent.empresas),
            "setores": list(ent.setores), "paises": list(ent.paises),
            "moedas": list(ent.moedas), "ativos": list(ent.ativos)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--payload", nargs="*", default=[],
                    help="respostas cruas do NEWS_SENTIMENT (aceita glob)")
    ap.add_argument("--aplicar", action="store_true",
                    help="grava no acervo local; sem isto é simulação")
    args = ap.parse_args(argv)

    caminhos = sorted(glob.glob(str(DIRETORIO_PADRAO / "*.json")))
    for padrao in args.payload:
        caminhos.extend(sorted(glob.glob(padrao)))
    crus = ler_feeds(caminhos)

    engine = engine_acervo()
    if engine is None:
        print("acervo local não configurado", file=sys.stderr)
        return 2
    universo, limitacoes = universo_entidades.carregar()
    for lim in limitacoes:
        print(f"limitação: {lim}")

    extrator = AlphaVantage(transporte=None)
    novos: dict[str, dict] = {}
    for url, cru in crus.items():
        extraidos = extrator._extrair({"feed": [cru]})
        id_dedup = hash_url(url_canonica(url) or url)
        if not extraidos:
            if pagina_vazia(cru.get("summary")):
                novos[id_dedup] = None      # sem fato: sem ticker
            continue
        noticia = coleta.para_noticia(extraidos[0], PROVEDOR, universo=universo)
        novos[noticia.id_dedup] = entidades_json(noticia)

    with engine.connect() as conn:
        linhas = conn.execute(text(
            "SELECT id_dedup, entidades FROM noticias_itens "
            "WHERE provedor = :p"), {"p": PROVEDOR}).fetchall()

    mudancas = []
    for id_dedup, antigo in linhas:
        if id_dedup not in novos:
            continue
        antigo = antigo or {}
        tickers_antigos = list(antigo.get("tickers") or [])
        recalculado = novos[id_dedup]
        # Só remove. O recálculo também ACRESCENTA (55 atribuições na
        # simulação de 26/09/2026): item gravado quando o universo não
        # carregou, e o resolvedor por nome marcando BLK e STT onde aparecem
        # como acionistas. Esse é outro defeito, e este script não é o lugar
        # de introduzir atribuição que a coleta não fez.
        manter = set(recalculado["tickers"]) if recalculado else set()
        novo = {**antigo,
                "tickers": [t for t in tickers_antigos if t in manter]}
        if novo["tickers"] != tickers_antigos:
            mudancas.append((id_dedup, tickers_antigos, novo))

    saiu = sum(len(set(a) - set(n["tickers"])) for _, a, n in mudancas)
    print(f"payloads lidos: {len(caminhos)} arquivos, {len(crus)} itens crus")
    print(f"acervo {PROVEDOR}: {len(linhas)} itens; com payload: "
          f"{sum(1 for i, _ in linhas if i in novos)}; sem payload (intocados): "
          f"{sum(1 for i, _ in linhas if i not in novos)}")
    print(f"itens a reescrever: {len(mudancas)}; atribuições removidas: {saiu}")

    if not args.aplicar:
        print("simulação: nada gravado (use --aplicar)")
        return 0
    with engine.begin() as conn:
        for id_dedup, _, novo in mudancas:
            conn.execute(text(
                "UPDATE noticias_itens SET entidades = CAST(:e AS jsonb) "
                "WHERE id_dedup = :i AND provedor = :p"),
                {"e": json.dumps(novo, ensure_ascii=False), "i": id_dedup,
                 "p": PROVEDOR})
    print(f"gravado: {len(mudancas)} itens no acervo local")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

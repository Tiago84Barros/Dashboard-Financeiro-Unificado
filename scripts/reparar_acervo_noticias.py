"""Conserta mojibake e tickers da B3 no titulo nos itens ja gravados no acervo local.

Dois defeitos medidos em 04/10/2026 (auditoria app4, LLM-A6), os dois com a
causa corrigida na coleta e com resto no acervo:

1. **Mojibake.** 2.923 dos 3.377 itens da Exame entraram como
   ``AutorizaÃ§Ã£o`` desde 06/09: o feed responde sem ``charset`` e o
   ``requests`` decodificava como ISO-8859-1. ``titulo`` e congelado no UPSERT
   (``ON CONFLICT`` nao o reescreve), entao recoletar nao conserta -- so este
   script. ``hash_conteudo`` e ``simhash`` sao recalculados junto, porque
   derivam do texto: com o texto quebrado, a mesma materia em outro veiculo
   nunca casava no dedup por conteudo.
2. **Ticker da B3 no titulo.** ``HGLG11 anuncia...`` saia com ``tickers=[]``
   (ver :func:`core.noticias.entidades.tickers_b3_do_titulo`). Aqui so se
   ACRESCENTA ticker, setor e pais do ticker; nada e removido.

A nota (``noticias_avaliacoes``) nao e recalculada: ela e de uma versao de
metodologia e quem a reescreve e o avaliador, na proxima passada.

Grava so no acervo local (``engine_acervo``), nunca no Supabase. Sem
``--aplicar`` e simulacao.

    python scripts/reparar_acervo_noticias.py
    python scripts/reparar_acervo_noticias.py --aplicar
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from core.noticias import dedup, universo_entidades  # noqa: E402
from core.noticias.destino import engine_acervo  # noqa: E402
from core.noticias.entidades import tickers_b3_do_titulo  # noqa: E402
from core.noticias.normalizacao import consertar_mojibake  # noqa: E402

_ATUALIZA = text("""
    UPDATE noticias_itens
       SET titulo = :titulo, resumo = :resumo, hash_conteudo = :hash_conteudo,
           simhash = :simhash, entidades = CAST(:entidades AS JSONB)
     WHERE id_dedup = :id_dedup
""")


def reparar_linha(titulo, resumo, entidades, universo) -> dict | None:
    """O que muda numa linha, ou ``None`` se nada muda. Puro, para teste."""
    novo_titulo = consertar_mojibake(titulo) or (titulo or "")
    novo_resumo = consertar_mojibake(resumo) if resumo else resumo
    ent = dict(entidades or {})
    tickers = list(ent.get("tickers") or [])
    setores = list(ent.get("setores") or [])
    paises = list(ent.get("paises") or [])
    novos = [t for t in tickers_b3_do_titulo(novo_titulo, universo)
             if t not in tickers]
    for t in novos:
        tickers.append(t)
        setor = universo.setor_por_ticker.get(t)
        if setor and setor not in setores:
            setores.append(setor)
        pais = universo.pais_por_ticker.get(t)
        if pais and pais not in paises:
            paises.append(pais)
    texto_mudou = novo_titulo != titulo or novo_resumo != resumo
    if not texto_mudou and not novos:
        return None
    ent.update(tickers=tickers, setores=setores, paises=paises)
    saida = {"titulo": novo_titulo, "resumo": novo_resumo,
             "entidades": ent, "tickers_novos": novos,
             "texto_mudou": texto_mudou}
    if texto_mudou:
        saida["hash_conteudo"] = dedup.hash_conteudo(novo_titulo, novo_resumo)
        sh = dedup.simhash(f"{novo_titulo} {novo_resumo or ''}")
        saida["simhash"] = sh
    return saida


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--aplicar", action="store_true",
                    help="grava no acervo local; sem isto e simulacao")
    args = ap.parse_args(argv)

    engine = engine_acervo()
    if engine is None:
        print("acervo local nao configurado", file=sys.stderr)
        return 2
    universo, limitacoes = universo_entidades.carregar()
    for lim in limitacoes:
        print(f"limitacao: {lim}")

    try:
        with engine.connect() as conn:
            linhas = conn.execute(text(
                "SELECT id_dedup, titulo, resumo, entidades, hash_conteudo, "
                "simhash, veiculo FROM noticias_itens")).fetchall()

        mudancas = []
        for id_dedup, titulo, resumo, ent, hc, sh, veiculo in linhas:
            r = reparar_linha(titulo, resumo, ent, universo)
            if r is None:
                continue
            r.setdefault("hash_conteudo", hc)
            r.setdefault("simhash", sh)
            mudancas.append((id_dedup, veiculo, titulo, r))

        texto = [m for m in mudancas if m[3]["texto_mudou"]]
        por_veiculo: dict[str, int] = {}
        for _, v, _, _ in texto:
            por_veiculo[v or "?"] = por_veiculo.get(v or "?", 0) + 1
        com_ticker = [m for m in mudancas if m[3]["tickers_novos"]]
        contagem: dict[str, int] = {}
        for m in com_ticker:
            for t in m[3]["tickers_novos"]:
                contagem[t] = contagem.get(t, 0) + 1

        print(f"acervo: {len(linhas)} itens")
        print(f"texto consertado: {len(texto)} itens {por_veiculo}")
        print(f"ticker da B3 acrescentado pelo titulo: {len(com_ticker)} itens; "
              f"mais frequentes: {sorted(contagem.items(), key=lambda x: -x[1])[:15]}")
        for _, v, antes, r in texto[:5]:
            print(f"  [{v}] {antes[:90]!r}\n      -> {r['titulo'][:90]!r}")
        for _, v, _, r in com_ticker[:5]:
            print(f"  [{v}] +{r['tickers_novos']} {r['titulo'][:90]!r}")

        if not args.aplicar:
            print("simulacao: nada gravado (use --aplicar no acervo LOCAL)")
            return 0
        with engine.begin() as conn:
            for id_dedup, _, _, r in mudancas:
                conn.execute(_ATUALIZA, {
                    "id_dedup": id_dedup, "titulo": r["titulo"],
                    "resumo": r["resumo"], "hash_conteudo": r["hash_conteudo"],
                    "simhash": r["simhash"],
                    "entidades": json.dumps(r["entidades"], ensure_ascii=False),
                })
        print(f"gravado: {len(mudancas)} linhas no acervo local")
        return 0
    finally:
        # engine_acervo nao e cacheado: quem cria descarta (memoria
        # engine-macro-nao-e-cacheado).
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

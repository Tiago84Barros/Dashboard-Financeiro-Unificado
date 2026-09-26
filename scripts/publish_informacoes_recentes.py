"""Publica notícias e documentos recentes em ``data/public/informacoes_recentes.json.gz``.

Lê o armazém local (Docker ``dfu_warehouse``), só leitura, e grava o que as
seções Notícias e Relatórios da Inteligência dos Ativos precisam e o app
publicado não alcança:

- notícias dos últimos ``JANELA_NOTICIAS`` dias do acervo (banco
  ``noticias``), já filtradas e classificadas por
  ``core.inteligencia_ativos.informacoes`` (o ativo tem de ser o assunto da
  manchete; impacto e dimensões pelo léxico próprio, não pelo
  ``tipo_evento`` do acervo), no máximo ``MAX_NOTICIAS`` por ativo, com os
  descartes contados por motivo;
- documentos dos últimos ``JANELA_DOCUMENTOS`` dias: CVM/IPE para
  companhias (``public.docs_corporativos``) e FNET para FIIs
  (``market.fii_documents``). Só metadados (tipo, título, data, URL); o
  conteúdo fica no RAG.

Achados extraídos de documento de FII (``market.fii_document_findings``) NÃO
entram: em 26/09/2026 nenhum dos 751 estava validado, e os lidos eram
boilerplate de regulamento ("CAPÍTULO 13 – DOS FATORES DE RISCO") ou o
contrário do rótulo ("sem atrasos" marcado como ``atraso``). Publicar como
"novo risco" seria inventar.

Recusa publicar (saída 1) se a notícia mais recente do acervo tiver mais de
``ACERVO_MAXIMO_DIAS`` dias; saída 2 se o armazém estiver fora do ar.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.inteligencia_ativos import informacoes as inf  # noqa: E402

CAMINHO_PADRAO = Path(__file__).resolve().parents[1] / "data" / "public" / \
    "informacoes_recentes.json.gz"
VERSAO = 1
JANELA_NOTICIAS = 60
JANELA_DOCUMENTOS = 180
MAX_NOTICIAS = 6
MAX_DOCUMENTOS = 8
MAX_RESUMO = 240
ACERVO_MAXIMO_DIAS = 7

FONTE_NOTICIAS = "Acervo de notícias do projeto (RSS e APIs de imprensa)"
FONTE_DOCS = {"b3": "CVM/IPE (docs_corporativos)",
              "fii": "B3/FNET (fii_documents)"}

METODO = {
    "noticias": (
        f"Últimos {JANELA_NOTICIAS} dias. Entra só se o ativo é o assunto da "
        "manchete (ticker escrito ou nome resolvido na própria manchete), "
        "sem 13F, sem resumo de mercado com 3+ empresas e com ao menos uma "
        "categoria material no léxico. Duplicatas por evento e por manchete "
        f"normalizada. Até {MAX_NOTICIAS} por ativo, impacto mais alto "
        "primeiro."),
    "relatorios": (
        f"Últimos {JANELA_DOCUMENTOS} dias. Até {MAX_DOCUMENTOS} por ativo, "
        "por prioridade de tipo (fato relevante, balanço, release, guidance, "
        "gerencial...). Dimensões pelo título, não pelo conteúdo."),
}


def _iso(x) -> str | None:
    if x is None:
        return None
    return x.isoformat(timespec="seconds") if isinstance(x, datetime) \
        else x.isoformat()


def _resumo(texto: str | None) -> str | None:
    t = " ".join(str(texto or "").split())
    if not t:
        return None
    return t if len(t) <= MAX_RESUMO else t[:MAX_RESUMO - 1].rstrip() + "…"


def agrupar_noticias(linhas, resolver, eh_13f) -> dict:
    """Linhas do acervo → ``{ticker: {"itens": [...], "descartadas": {...}}}``.

    ``resolver(titulo) -> tuple[str, ...]`` acha tickers pelo nome na
    manchete; ``eh_13f(titulo) -> bool``. Puro dado os dois."""
    candidatas: dict[str, list] = defaultdict(list)
    descartes: dict[str, Counter] = defaultdict(Counter)
    for r in linhas:
        titulo = str(r.get("titulo") or "").strip()
        if not titulo:
            continue
        declarados = [str(t).upper() for t in
                      ((r.get("entidades") or {}).get("tickers") or [])]
        resolvidos = tuple(resolver(titulo))
        treze = eh_13f(titulo)
        for tk in dict.fromkeys(declarados + list(resolvidos)):
            veredito = inf.avaliar_manchete(tk, titulo, resolvidos,
                                            relato_13f=treze)
            if isinstance(veredito, str):
                descartes[tk][veredito] += 1
                continue
            candidatas[tk].append((veredito, r, titulo))

    saida = {}
    for tk in sorted(set(candidatas) | set(descartes)):
        vistos_evento, vistas_manchete, itens = set(), set(), []
        ordem = sorted(candidatas.get(tk, []), key=lambda x: (
            inf._ORDEM_NIVEL[x[0].nivel],
            -(x[1].get("publicado_em") or datetime.min.replace(
                tzinfo=timezone.utc)).timestamp()))
        for cl, r, titulo in ordem:
            ev, mc = r.get("evento_id"), inf.chave_de_duplicata(titulo)
            if (ev and ev in vistos_evento) or mc in vistas_manchete:
                descartes[tk][inf.D_DUPLICADA] += 1
                continue
            vistos_evento.add(ev)
            vistas_manchete.add(mc)
            if len(itens) >= MAX_NOTICIAS:
                continue
            pub = r.get("publicado_em")
            itens.append({
                "headline": titulo,
                "date": pub.date().isoformat() if pub else None,
                "source": r.get("veiculo") or r.get("dominio"),
                "url": r.get("url_canonica") or r.get("url"),
                "impact_level": cl.nivel,
                "affected_dimension": list(cl.dimensoes),
                "summary": _resumo(r.get("resumo")),
                "categoria": cl.categoria,
                "motivo": cl.motivo,
                "retrieved_at": _iso(r.get("coletado_em")),
            })
        # exibição: mais recente primeiro dentro do que entrou
        itens.sort(key=lambda i: i["date"] or "", reverse=True)
        saida[tk] = {"itens": itens,
                     "descartadas": dict(sorted(descartes[tk].items()))}
    return saida


def agrupar_documentos(linhas) -> dict:
    """Linhas normalizadas (ticker, categoria, tipo, titulo, data, fonte, url,
    coletado_em) → ``{ticker: [documento, ...]}``. Puro."""
    por: dict[str, list] = defaultdict(list)
    vistos = set()
    for r in linhas:
        tipo = inf.tipo_documento(r.get("categoria"), r.get("tipo"),
                                  r.get("titulo"))
        tk = str(r.get("ticker") or "").upper()
        if tipo is None or not tk:
            continue
        chave = (tk, r.get("url") or (r.get("titulo"), r.get("data")))
        if chave in vistos:
            continue
        vistos.add(chave)
        cl = inf.classificar_titulo(r.get("titulo") or "")
        por[tk].append({
            "tipo": tipo,
            "titulo": str(r.get("titulo") or inf.ROTULO_DOC[tipo]).strip(),
            "reference_date": _iso(r.get("data")),
            "source": r.get("fonte"),
            "source_url": r.get("url"),
            "retrieved_at": _iso(r.get("coletado_em")),
            "dimensoes": list(cl.dimensoes) if cl else [],
        })
    saida = {}
    for tk, docs in por.items():
        # mais recente primeiro; a ordenação estável por tipo preserva isso
        docs.sort(key=lambda d: d["reference_date"] or "", reverse=True)
        docs.sort(key=lambda d: inf.PRIORIDADE_DOC.index(d["tipo"]))
        escolhidos = docs[:MAX_DOCUMENTOS]
        escolhidos.sort(key=lambda d: d["reference_date"] or "", reverse=True)
        saida[tk] = escolhidos
    return saida


_ROTULO_FNET = {
    "RELAT GERENCIAL": "Relatório gerencial", "FATO RELEV": "Fato relevante",
    "AVISO MERCADO": "Aviso ao mercado", "DFIN": "Demonstrações financeiras",
    "DF ANUAL FII": "Demonstrações financeiras anuais",
    "AGO": "Ata de AGO", "EDITAL AGO": "Edital de AGO",
    "AGE PROPOST ADM": "Proposta da administração (AGE)",
    "AGO PROPOST ADM": "Proposta da administração (AGO)",
    "AGO/AGE PROPADM": "Proposta da administração (AGO/AGE)",
    "MIN PROSP DEF": "Prospecto definitivo de oferta",
    "MIN PROSP PREL": "Prospecto preliminar de oferta",
}


def coletar_noticias(conn_noticias, universo) -> tuple[dict, str | None, int]:
    from sqlalchemy import text

    from core.noticias.entidades import relato_de_posicao, resolver_tickers
    linhas = [dict(r._mapping) for r in conn_noticias.execute(text(f"""
        SELECT titulo, resumo, url, url_canonica, veiculo, dominio,
               publicado_em, coletado_em, evento_id, entidades
          FROM noticias_itens
         WHERE publicado_em >= now() - interval '{JANELA_NOTICIAS} days'
           AND publicado_em <= now() + interval '1 day'"""))]
    base = conn_noticias.execute(text(
        "SELECT max(publicado_em) FROM noticias_itens "
        "WHERE publicado_em <= now() + interval '1 day'")).scalar()
    por = agrupar_noticias(
        linhas, lambda t: resolver_tickers((), t, universo),
        lambda t: relato_de_posicao(t) is not None)
    return por, _iso(base), len(linhas)


def coletar_documentos(conn) -> tuple[dict, dict]:
    from sqlalchemy import text
    b3 = [dict(r._mapping) for r in conn.execute(text(f"""
        SELECT ticker, categoria, tipo, titulo, data, fonte, url,
               created_at AS coletado_em
          FROM public.docs_corporativos
         WHERE data >= current_date - {JANELA_DOCUMENTOS}
           AND data <= current_date + 1"""))]
    fii = []
    for r in conn.execute(text(f"""
        SELECT ticker, document_type, reference_date,
               COALESCE(source_published_at::date, reference_date) AS data,
               source_url, first_observed_at
          FROM market.fii_documents
         WHERE COALESCE(source_published_at::date, reference_date)
               >= current_date - {JANELA_DOCUMENTOS}""")):
        m = r._mapping
        dt = str(m["document_type"] or "").strip().upper()
        rotulo = _ROTULO_FNET.get(dt, dt.title())
        ref = m["reference_date"]
        fii.append({
            "ticker": m["ticker"], "categoria": "", "tipo": dt.lower(),
            "titulo": f"{rotulo} — referência {ref:%d/%m/%Y}" if ref else rotulo,
            "data": m["data"], "fonte": "B3/FNET", "url": m["source_url"],
            "coletado_em": m["first_observed_at"]})
    base = {
        "b3": _iso(max((x["data"] for x in b3), default=None)),
        "fii": _iso(max((x["data"] for x in fii), default=None)),
    }
    return agrupar_documentos(b3 + fii), base


def serializar(payload: dict, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
    with open(caminho, "wb") as fh, gzip.GzipFile(
            filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(bruto)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from sqlalchemy import create_engine, text

    from core.noticias import universo_entidades
    from scripts.publish_fii_selection_from_local import _warehouse_url
    try:
        url = _warehouse_url()
        engine = create_engine(url)
        conn = engine.connect()
        conn_n = create_engine(re.sub(r"/[^/]+$", "/noticias", url)).connect()
    except Exception as exc:
        print(f"armazém local indisponível: {type(exc).__name__}: {exc}")
        return 2
    agora = datetime.now(timezone.utc)
    with conn, conn_n:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn_n.execute(text("SET TRANSACTION READ ONLY"))
        universo, limitacoes = universo_entidades.carregar(
            engine=engine, usar_cache=False)
        noticias, base_n, lidas = coletar_noticias(conn_n, universo)
        documentos, base_d = coletar_documentos(conn)

    com_noticia = {k: v for k, v in noticias.items() if v["itens"]}
    descartes = Counter()
    for v in noticias.values():
        descartes.update(v["descartadas"])
    relatorio = {
        "noticias_lidas": lidas,
        "tickers_com_noticia": len(com_noticia),
        "noticias_publicadas": sum(len(v["itens"]) for v in com_noticia.values()),
        "descartes": dict(descartes.most_common()),
        "tickers_com_documento": len(documentos),
        "documentos_publicados": sum(len(v) for v in documentos.values()),
        "base_noticias": base_n, "base_documentos": base_d,
        "limitacoes_universo": list(limitacoes),
    }
    if base_n is None or (agora - datetime.fromisoformat(base_n)).days \
            > ACERVO_MAXIMO_DIAS:
        relatorio["erro"] = (f"acervo de notícias parado há mais de "
                             f"{ACERVO_MAXIMO_DIAS} dias.")
        print(json.dumps(relatorio, ensure_ascii=False, indent=2))
        return 1
    payload = {
        "versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
        "metodo": METODO,
        "noticias": {"janela_dias": JANELA_NOTICIAS, "base_ate": base_n,
                     "fonte": FONTE_NOTICIAS, "por_ticker": noticias},
        "relatorios": {"janela_dias": JANELA_DOCUMENTOS, "base_ate": base_d,
                       "fonte": FONTE_DOCS, "por_ticker": documentos},
    }
    if not args.dry_run:
        serializar(payload, args.saida)
        relatorio["arquivo"] = str(args.saida)
        relatorio["bytes"] = args.saida.stat().st_size
    print(json.dumps(relatorio, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

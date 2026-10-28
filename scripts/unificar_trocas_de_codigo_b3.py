# -*- coding: utf-8 -*-
"""
Unifica as trocas de código da B3 (ELET3 -> AXIA3 e mais 11): um dono só
para o histórico de cada companhia e classe.

Contexto (2026-10): ``public.setores`` tinha os dois códigos de cada troca, a
ingestão diária reativava os dois em ``market.assets`` e a aba "Criação de
portfólio" contava a mesma companhia duas vezes — liderando dois segmentos no
mesmo ano e entrando duas vezes no benchmark. A leitura já deduplica
(``core.b3_troca_codigo``); este script corrige a BASE, para cada par:

* fatos (preço, demonstrações, proventos, indicadores) do código antigo
  passam para o atual — na mesma chave fica a linha do atual;
* o código antigo fica ``is_active = FALSE``;
* ``market.ticker_alias`` ganha antigo -> atual com o motivo de troca
  provada (a ingestão passa a pular o antigo) e perde o alias legado
  invertido (atual -> antigo);
* ``public.setores`` fica com UMA classificação para os dois códigos
  (LISTAGEM preenchida prevalece; empate, a do código novo).

Os pares vêm da mesma regra que o app usa (``mapa_de_trocas``): os 12 pares
conferidos em ``PARES_CONHECIDOS``, as trocas já gravadas e o alias legado
quando os dois códigos são da mesma companhia. Companhias diferentes vetam
o par; ON/PN/Unit nunca são troca.

Dry-run por padrão, numa transação SÓ LEITURA. Idempotente: a segunda
execução não acha linha a mover nem ativo a desativar.

Uso:
  python scripts/unificar_trocas_de_codigo_b3.py --alvo warehouse            # dry-run
  python scripts/unificar_trocas_de_codigo_b3.py --alvo supabase             # dry-run
  python scripts/unificar_trocas_de_codigo_b3.py --alvo warehouse --apply
  python scripts/unificar_trocas_de_codigo_b3.py --alvo supabase --apply --confirmo-producao
  python scripts/unificar_trocas_de_codigo_b3.py --alvo warehouse --pares ELET3:AXIA3
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from core.b3_troca_codigo import classe, mapa_de_trocas, normalizar  # noqa: E402


def _engine(alvo: str):
    if alvo == "warehouse":
        from sqlalchemy import create_engine

        from scripts.publish_fii_selection_from_local import _warehouse_url
        # mesmo caminho dos scripts de publicação: o armazém local não passa
        # por core.database (que aponta para o Supabase)
        return create_engine(_warehouse_url().replace("postgresql://", "postgresql+psycopg2://", 1))
    from core.database import get_engine
    return get_engine()


def _somente_leitura(conn) -> None:
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text("SET LOCAL statement_timeout = '60s'"))


def _ativos(conn) -> pd.DataFrame:
    rows = conn.execute(text("""
        SELECT ticker, company_id, is_active FROM market.assets
        WHERE asset_type IN ('stock', 'unit')
    """)).fetchall()
    return pd.DataFrame(rows, columns=["ticker", "company_id", "is_active"])


def _aliases(conn) -> pd.DataFrame:
    if not conn.execute(text("SELECT to_regclass('market.ticker_alias') IS NOT NULL")).scalar():
        return pd.DataFrame(columns=["brapi_symbol", "b3_ticker", "motivo"])
    tem_motivo = conn.execute(text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'market' AND table_name = 'ticker_alias' AND column_name = 'motivo'
    """)).scalar()
    motivo = "motivo" if tem_motivo else "NULL"
    rows = conn.execute(text(
        f"SELECT brapi_symbol, b3_ticker, {motivo} FROM market.ticker_alias")).fetchall()
    return pd.DataFrame(rows, columns=["brapi_symbol", "b3_ticker", "motivo"])


def _codigo_cvm(conn, antigo: str, novo: str):
    try:
        return conn.execute(text("""
            SELECT c.codigo_cvm FROM market.assets a
            JOIN market.companies c ON c.id = a.company_id
            WHERE a.ticker IN (:a, :n) AND c.codigo_cvm IS NOT NULL
            ORDER BY (a.ticker = :n) DESC LIMIT 1
        """), {"a": antigo, "n": novo}).scalar()
    except Exception:
        return None


def _sem_regra(ativos: pd.DataFrame, mapa: dict[str, str]) -> list[str]:
    """Companhia + classe com mais de um código ATIVO que nenhuma regra decide."""
    vivos = ativos[ativos["is_active"].fillna(False).astype(bool) & ativos["company_id"].notna()]
    grupos: dict[tuple, list[str]] = {}
    for tk, cid in zip(vivos["ticker"].map(normalizar), vivos["company_id"]):
        if tk in mapa or classe(tk) is None:
            continue
        grupos.setdefault((int(cid), classe(tk)), []).append(tk)
    return [f"company_id {cid} classe {cls}: {', '.join(sorted(tks))}"
            for (cid, cls), tks in sorted(grupos.items()) if len(tks) > 1]


def _pendente(rel: dict) -> bool:
    fatos = [v for v in (rel.get("fatos") or {}).values() if isinstance(v, dict)]
    alias = rel.get("alias") if isinstance(rel.get("alias"), dict) else {}
    setores = rel.get("setores") if isinstance(rel.get("setores"), dict) else {}
    antigo = (rel.get("ativos") or {}).get(rel["antigo"]) or {}
    return bool(
        any(f["mover"] or f["descartar"] for f in fatos)
        or antigo.get("ativo")
        or alias.get("gravar") or alias.get("apagar")
        or setores.get("alinhar") or setores.get("inserir")
    )


def _imprimir(rel: dict) -> None:
    a, n = rel["antigo"], rel["novo"]
    marca = "PENDENTE" if _pendente(rel) else "ok (nada a fazer)"
    print(f"\n{a} -> {n}: {marca}")
    for tk in (a, n):
        at = (rel.get("ativos") or {}).get(tk)
        estado = "sem ativo" if at is None else (
            f"company_id={at['company_id']} ativo={'sim' if at['ativo'] else 'não'}")
        print(f"  {tk:6s} {estado}")
    for tabela, v in (rel.get("fatos") or {}).items():
        if isinstance(v, dict):
            if v["mover"] or v["descartar"]:
                print(f"  {tabela}: mover {v['mover']}, descartar {v['descartar']} "
                      f"(já existem sob {n})")
        else:
            print(f"  {tabela}: {v}")
    alias = rel.get("alias")
    if isinstance(alias, dict):
        if alias.get("gravar"):
            print(f"  alias: gravar {alias['gravar']}")
        for x in alias.get("apagar") or []:
            print(f"  alias: apagar legado invertido {x}")
        for x in alias.get("terceiros") or []:
            print(f"  alias: MANTIDO, aponta outra companhia (revisar) {x}")
    else:
        print(f"  alias: {alias}")
    setores = rel.get("setores")
    if isinstance(setores, dict):
        print(f"  setores: classificação de {setores['origem']} = {setores['classificacao']}"
              + (f"; alinhar {', '.join(setores['alinhar'])}" if setores.get("alinhar") else "")
              + (f"; inserir linha de {setores['inserir']}" if setores.get("inserir") else ""))
    else:
        print(f"  setores: {setores if setores is not None else 'nenhum dos dois códigos'}")


def main() -> int:
    from data_pipeline.market.troca_codigo import unificar

    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--alvo", choices=("warehouse", "supabase"), required=True,
                    help="warehouse = armazém local (Docker dfu_warehouse); "
                         "supabase = produção (core.database.get_engine)")
    ap.add_argument("--apply", action="store_true", help="grava (default: dry-run)")
    ap.add_argument("--confirmo-producao", action="store_true",
                    help="obrigatório com --apply --alvo supabase")
    ap.add_argument("--pares", nargs="*", default=None,
                    help="restringe a estes pares ANTIGO:NOVO (ou só ANTIGO)")
    args = ap.parse_args()

    if args.apply and args.alvo == "supabase" and not args.confirmo_producao:
        print("ERRO: --apply no Supabase exige --confirmo-producao.")
        return 2

    engine = _engine(args.alvo)
    if engine is None:
        print("ERRO: banco não configurado.")
        return 1

    with engine.connect() as conn:
        _somente_leitura(conn)
        ativos, aliases = _ativos(conn), _aliases(conn)
    mapa = mapa_de_trocas(ativos[["ticker", "company_id"]], aliases)
    if args.pares:
        quer = {normalizar(p.split(":")[0]) for p in args.pares}
        mapa = {a: n for a, n in mapa.items() if a in quer}

    modo = "APLICAR" if args.apply else "DRY-RUN (transação só leitura)"
    print(f"alvo: {args.alvo} | modo: {modo} | pares: {len(mapa)}")

    pendentes, vetados = 0, []
    for antigo, novo in sorted(mapa.items()):
        try:
            if args.apply:
                with engine.begin() as conn:
                    rel = unificar(conn, antigo, novo, aplicar=True,
                                   codigo_cvm=_codigo_cvm(conn, antigo, novo))
            else:
                with engine.connect() as conn:
                    _somente_leitura(conn)
                    rel = unificar(conn, antigo, novo, aplicar=False)
                    conn.rollback()
        except ValueError as exc:
            vetados.append(f"{antigo} -> {novo}: {exc}")
            continue
        pendentes += _pendente(rel)
        _imprimir(rel)

    print("\n── resumo ──")
    verbo = "aplicados" if args.apply else "pendentes"
    print(f"pares {verbo}: {pendentes} de {len(mapa)}")
    for v in vetados:
        print(f"vetado: {v}")
    sem_regra = _sem_regra(ativos, mapa)
    if sem_regra:
        print("companhia + classe com mais de um código ativo e sem regra (não mexido):")
        for s in sem_regra:
            print(f"  {s}")
    else:
        print("nenhuma companhia + classe com dois códigos ativos fora dos pares acima")
    return 0


if __name__ == "__main__":
    sys.exit(main())

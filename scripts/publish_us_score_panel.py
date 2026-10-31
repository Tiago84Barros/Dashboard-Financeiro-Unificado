# -*- coding: utf-8 -*-
"""Publica na vitrine o painel PIT dos EUA já montado.

Por que existe: o painel do backtest americano era montado a cada visita a
partir de três tabelas da vitrine -- as safras (`score_vintages`), os preços
mensais (`prices_monthly`) e os desfechos de saída (`delisting_outcomes`). Isso
foi o maior egress do Supabase, que estourou o plano free no ciclo set/2026
(7,8 GB contra 5 GB). O PR #445 já reduziu a leitura de preços de 368 mil para
39 mil linhas. Este script corta o resto: monta o painel UMA vez e grava as
~22 mil linhas prontas em `market_us.score_panel_pub`.

**Montado a partir da vitrine, não do armazém local.** O painel publicado tem de
ser o que o app montaria ao vivo, linha por linha. Montá-lo das mesmas três
tabelas, pela mesma função (`core.us_read.load_score_panel`), garante isso por
construção; montar do armazém, que tem mais preços e outra data de fim de
dado, daria um painel diferente do que a tela mostrava até aqui.

**Velho não passa por atual.** Junto com o painel vai a impressão das fontes no
momento da montagem: contagem de safras da versão, contagem e último mês de
preço, contagem de desfechos. A impressão é tirada ANTES de montar, de modo que
uma republicação no meio do caminho deixa a impressão gravada desatualizada e o
leitor recusa o painel -- nunca o contrário. O leitor compara com o estado
atual a cada carga (uma consulta de uma linha) e, se diverge, monta ao vivo.

**Só a versão corrente.** Painéis de outra metodologia ou de outro horizonte são
apagados: o leitor pede por (versão, horizonte), e linha que ninguém lê só ocupa
espaço num banco que está a 50 MB do limite.

Depois de gravar, o script relê o painel pelo caminho do app e confere que é
idêntico ao montado. Se não for, sai com erro.

**Também no armazém local (`--armazem`).** O app rodando na máquina contra o
armazém (`scripts/abrir_app_armazem.bat`) não achava as tabelas publicadas e
montava o painel ao vivo: 34,5 s a cada cache frio, contra ~1 s lendo o
publicado. Com `--armazem` o script monta das tabelas DO ARMAZÉM e grava NO
ARMAZÉM -- o mesmo princípio de cima, aplicado ao outro banco: o painel servido
é o que o app montaria ao vivo ali, e a impressão é a do armazém, de modo que
dado novo no armazém derruba o publicado do mesmo jeito. Não toca no Supabase.

Simulação por padrão; grava somente com --apply.

Uso:
    python -m scripts.publish_us_score_panel
    python -m scripts.publish_us_score_panel --apply
    python -m scripts.publish_us_score_panel --apply --armazem
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

# Espelha supabase_unificado/schema/079_market_us_score_panel_pub.sql. O script
# cria o que grava, como os demais publicadores dos EUA.
DDL = """
CREATE SCHEMA IF NOT EXISTS market_us;
CREATE TABLE IF NOT EXISTS market_us.score_panel_pub (
    score_version  TEXT             NOT NULL,
    horizon_months INTEGER          NOT NULL,
    ordem          INTEGER          NOT NULL,
    date           DATE             NOT NULL,
    symbol         TEXT             NOT NULL,
    score          DOUBLE PRECISION,
    fwd_return     DOUBLE PRECISION,
    censored       BOOLEAN          NOT NULL,
    PRIMARY KEY (score_version, horizon_months, ordem)
);
CREATE TABLE IF NOT EXISTS market_us.score_panel_pub_meta (
    score_version  TEXT        NOT NULL,
    horizon_months INTEGER     NOT NULL,
    impressao      TEXT        NOT NULL,
    atributos      TEXT        NOT NULL,
    n_linhas       INTEGER     NOT NULL,
    publicado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (score_version, horizon_months)
);
ALTER TABLE market_us.score_panel_pub ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_us.score_panel_pub_meta ENABLE ROW LEVEL SECURITY;
"""

COLS = ("date", "symbol", "score", "fwd_return", "censored")


def _atributos(painel) -> dict:
    from core.us_read import _ATTRS_PUBLICADOS
    return {k: int(painel.attrs[k]) for k in _ATTRS_PUBLICADOS
            if painel.attrs.get(k) is not None}


def _gravar(conn, painel, versao: str, horizonte: int, impressao: dict) -> None:
    from core.us_read import PAINEL_PUB, PAINEL_PUB_META
    if conn.dialect.name == "postgresql":
        for stmt in (s.strip() for s in DDL.split(";")):
            if stmt:
                conn.execute(text(stmt))
    chave = {"v": versao, "h": int(horizonte)}
    # Outras versões e horizontes saem; a corrente é regravada inteira.
    for tabela in (PAINEL_PUB, PAINEL_PUB_META):
        conn.execute(text(f"DELETE FROM market_us.{tabela}"), {})
    linhas = painel[list(COLS)].copy()
    linhas.insert(0, "ordem", range(len(linhas)))
    linhas.insert(0, "horizon_months", int(horizonte))
    linhas.insert(0, "score_version", versao)
    linhas["censored"] = linhas["censored"].astype(bool)
    linhas.to_sql(PAINEL_PUB, conn, schema="market_us", if_exists="append",
                  index=False, method="multi", chunksize=1000)
    conn.execute(text(
        f"INSERT INTO market_us.{PAINEL_PUB_META} "
        "(score_version, horizon_months, impressao, atributos, n_linhas) "
        "VALUES (:v, :h, :i, :a, :n)"),
        {**chave, "i": json.dumps(impressao, sort_keys=True),
         "a": json.dumps(_atributos(painel), sort_keys=True),
         "n": int(len(linhas))})


def publicar(*, remoto, aplicar: bool, versao: str | None = None,
             horizonte: int | None = None) -> dict:
    import core.us_read as ur
    if versao is None:
        from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
        versao = US_FUNDAMENTAL_SCORE_VERSION
    if horizonte is None:
        from core.us_data import HORIZONTE_PAINEL_MESES
        horizonte = HORIZONTE_PAINEL_MESES
    resumo: dict = {"ok": True, "versao": versao, "horizonte": int(horizonte),
                    "aplicado": False}

    with remoto.connect() as conn:
        impressao = ur.impressao_do_painel(conn, versao)
    t0 = time.time()
    painel = ur.load_score_panel(score_version=versao, horizon_months=horizonte,
                                 publicado=False, engine=remoto)
    resumo.update(impressao=impressao, linhas=int(len(painel)),
                  atributos=_atributos(painel),
                  montagem_s=round(time.time() - t0, 1))
    if painel.empty:
        resumo["ok"] = False
        resumo["motivo"] = painel.attrs.get("motivo", "painel vazio")
        return resumo
    if not aplicar:
        return resumo

    with remoto.begin() as conn:
        _gravar(conn, painel, versao, horizonte, impressao)
    resumo["aplicado"] = True

    # Relê pelo caminho do app: o que foi gravado tem de ser o que foi montado.
    with remoto.connect() as conn:
        relido = ur._painel_publicado(conn, versao, horizonte)
    if relido is None:
        resumo["ok"] = False
        resumo["motivo"] = ("painel gravado não foi aceito na releitura: as "
                            "fontes mudaram durante a publicação -- rode de novo")
        return resumo
    esperado = painel.reset_index(drop=True)
    iguais = (relido[list(COLS)].reset_index(drop=True)
              .equals(esperado[list(COLS)]))
    attrs_iguais = all(relido.attrs.get(k) == painel.attrs.get(k)
                       for k in ur._ATTRS_PUBLICADOS)
    resumo["conferido"] = bool(iguais and attrs_iguais)
    if not resumo["conferido"]:
        resumo["ok"] = False
        resumo["motivo"] = "painel relido diverge do montado"
    return resumo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", dest="aplicar",
                    help="grava de fato (sem esta flag, apenas simula)")
    ap.add_argument("--versao", default=None,
                    help="metodologia a publicar (padrão: a corrente)")
    ap.add_argument("--armazem", action="store_true",
                    help="monta e grava no armazém local, não no Supabase")
    args = ap.parse_args(argv)

    from scripts.publish_us_snapshot import _engine

    if args.armazem:
        from scripts.publish_fii_selection_from_local import _warehouse_url
        remoto = _engine(_warehouse_url())
    else:
        from core.config import settings
        if not settings.db_url:
            print("Vitrine (Supabase) não configurada: DATABASE_URL ausente.",
                  file=sys.stderr)
            return 2
        remoto = _engine(settings.db_url)
    try:
        resumo = publicar(remoto=remoto, aplicar=args.aplicar, versao=args.versao)
    finally:
        remoto.dispose()

    print(json.dumps(resumo, ensure_ascii=False, sort_keys=True, default=str))
    if not resumo.get("ok"):
        return 2
    if not args.aplicar:
        print("[simulação] nada gravado; use --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Publica na vitrine as saídas derivadas dos EUA (`market_us.delistings`).

O portão "Universo de deslistadas" (`core/validacao_motor.py`) conta saídas na
vitrine e junta ao painel PIT por símbolo. Ele nunca teve o que contar: a
tabela existe só no warehouse local, e em produção a resposta era "nenhuma
saída em 16 safras" -- verdade sobre o painel e mentira sobre o mercado. É
exatamente a assinatura de universo sobrevivente que este módulo passou as
últimas sessões desfazendo, agora produzida pela publicação e não pela
derivação.

Três escolhas de escopo:

**A linha refutada viaja junto.** Ela é a prova de que aquela saída foi
conferida e negada -- se ficasse de fora, a vitrine não distinguiria "não
conferida" de "conferida e falsa", e a próxima republicação teria de confiar
na memória de quem publicou. Quem lê filtra por `refuted_by IS NULL`.

**A junção é por símbolo.** `company_id` está preenchido em 2 das 12.107
saídas, e `score_vintages` na vitrine não tem essa coluna. Publicar sem
símbolo resolvido é publicar linha que nenhuma consulta alcança -- mas ela
viaja mesmo assim, porque o denominador ("quantas saídas existem") é metade da
medida, e escondê-lo inflaria a fração que entra no painel.

**Sem chave estrangeira para `companies`.** A vitrine não tem o cadastro, e
arrastá-lo para satisfazer a FK publicaria uma tabela que nenhuma tela lê.

Simulação por padrão; grava somente com --apply.

Uso::

    python -m scripts.publish_us_delistings
    python -m scripts.publish_us_delistings --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from scripts.publish_us_score_vintages import _gravar_em_lotes  # noqa: E402

# Espelha supabase_unificado/schema/059_market_us_delistings_vitrine.sql.
# Criar o que se grava, em vez de supor que a migration rodou: migration
# registrada e nunca executada já deixou tela vazia sem erro neste projeto.
DDL_SAIDAS = """
CREATE SCHEMA IF NOT EXISTS market_us;
CREATE TABLE IF NOT EXISTS market_us.delistings (
    cik                       BIGINT      PRIMARY KEY,
    company_id                BIGINT      NULL,
    symbol                    TEXT        NULL,
    symbol_source             TEXT        NULL,
    symbol_as_of              DATE        NULL,
    last_annual_report_year   INTEGER     NOT NULL,
    absence_year              INTEGER     NOT NULL,
    delisted_date             DATE        NOT NULL,
    reason                    TEXT        NOT NULL DEFAULT 'ausencia_de_relatorio_anual',
    source                    TEXT        NOT NULL DEFAULT 'sec_full_index',
    refuted_form              TEXT        NULL,
    refuted_by                TEXT        NULL,
    refuted_date              DATE        NULL,
    checked_at                TIMESTAMPTZ NULL,
    derived_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS delistings_symbol_idx
    ON market_us.delistings (symbol) WHERE symbol IS NOT NULL;
CREATE INDEX IF NOT EXISTS delistings_absence_year_idx
    ON market_us.delistings (absence_year);
"""

COLS = ("cik", "symbol", "symbol_source", "symbol_as_of",
        "last_annual_report_year", "absence_year", "delisted_date",
        "reason", "source", "refuted_form", "refuted_by", "refuted_date",
        "checked_at")

SQL_LER = ("SELECT " + ", ".join(COLS) + " FROM market_us.delistings "
           "ORDER BY absence_year, cik")


def ler_saidas(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute(text(SQL_LER))]


def publicar(*, local, remoto, aplicar: bool) -> dict:
    with local.connect() as conn:
        linhas = ler_saidas(conn)
    if not linhas:
        return {"ok": False, "saidas": 0,
                "motivo": ("o warehouse local não tem `market_us.delistings`"
                           " povoada; rode scripts/ingerir_deslistadas_us.py")}

    # `refuted_by` e o filtro, nao `refuted_form`: a refutacao por
    # continuidade do papel (sucessao de registrante) nao tem forma de
    # relatorio para citar, e e ela que pega o caso comum.
    i_sym, i_ref = COLS.index("symbol"), COLS.index("refuted_by")
    vivas = [r for r in linhas if r[i_ref] is None]
    resumo = {"ok": True, "saidas": len(linhas), "refutadas": len(linhas) - len(vivas),
              "com_simbolo": sum(1 for r in vivas if r[i_sym]),
              "gravado": False}
    if not aplicar:
        return resumo

    with remoto.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout='600s'"))
        conn.exec_driver_sql(DDL_SAIDAS)

    atualiza = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLS if c != "cik")
    sql = (f"INSERT INTO market_us.delistings ({','.join(COLS)}) VALUES %s "
           f"ON CONFLICT (cik) DO UPDATE SET {atualiza}")
    resumo["gravadas"] = _gravar_em_lotes(remoto, sql, linhas, rotulo="saídas")
    resumo["gravado"] = True

    with remoto.connect() as conn:
        remotas = int(conn.execute(text(
            "SELECT count(*) FROM market_us.delistings")).scalar_one())
    resumo["saidas_na_vitrine"] = remotas
    # Conferência contra o local, não contra o que este processo julga ter
    # gravado: é o que pega gravação parcial de uma execução interrompida.
    if remotas < len(linhas):
        resumo["ok"] = False
        resumo["motivo"] = (f"publicação incompleta: local={len(linhas)}, "
                            f"vitrine={remotas}")
    return resumo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", dest="aplicar",
                    help="grava de fato (sem esta flag, apenas simula)")
    args = ap.parse_args(argv)

    from core.config import settings
    from scripts.publish_fii_selection_from_local import _warehouse_url
    from scripts.publish_us_snapshot import _engine

    if not settings.db_url:
        print("Vitrine (Supabase) não configurada: DATABASE_URL ausente.",
              file=sys.stderr)
        return 2
    local = _engine(_warehouse_url())
    remoto = _engine(settings.db_url)
    try:
        resumo = publicar(local=local, remoto=remoto, aplicar=args.aplicar)
    finally:
        local.dispose()
        remoto.dispose()

    print(json.dumps(resumo, ensure_ascii=False, sort_keys=True, default=str))
    if not resumo.get("ok"):
        return 2
    if not args.aplicar:
        print("[simulação] nada gravado; use --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

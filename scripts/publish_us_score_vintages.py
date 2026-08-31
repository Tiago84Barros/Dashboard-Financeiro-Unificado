# -*- coding: utf-8 -*-
"""Publica na vitrine a safra PIT dos EUA e os preços mensais que ela exige.

O painel de backtest americano (`core/us_read.py::load_score_panel`) junta
`market_us.score_vintages` a `market_us.prices_monthly`. As duas existem no
warehouse local -- 25.990 safras da metodologia corrente e 643 mil preços
mensais -- e nenhuma das duas estava na vitrine. Sem elas o painel volta vazio,
o portão "Painel PIT" nunca abre e o Rank-IC fora da amostra não tem o que
estreitar: o intervalo de -9,93% a +7,37% que a tela exibe é o de uma janela
que nunca foi ampliada porque o histórico nunca chegou ao ar.

Duas escolhas de escopo, ambas deliberadas:

**Só a versão corrente.** Publicar safra de outra metodologia encheria a vitrine
com linhas que o leitor filtra fora -- ele consulta por `score_version` -- e o
painel continuaria vazio, agora com espaço gasto. Se o local não tem safra da
versão corrente, este script recusa e manda rodar `score-history`, em vez de
publicar o que existe e deixar a tela explicar a ausência errada.

**Preço mensal inteiro dos símbolos da safra, não só os meses de rebalanço.**
Seria mais barato publicar junho de cada ano, que é onde as safras caem. Mas o
painel distingue "o dado acabou" de "a ação acabou" pelo fim da série de cada
símbolo, e usa o último preço negociado como saída de quem deslistou. Numa
grade só de junhos, quem quebrou em setembro sai pelo preço de junho -- retorno
zero em vez da queda -- e o backtest volta a ser otimista exatamente onde este
módulo passou a última semana deixando de ser.

Simulação por padrão; grava somente com --apply.

Uso:
    python -m scripts.publish_us_score_vintages
    python -m scripts.publish_us_score_vintages --apply
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

TRACK = "fundamental"

# Espelha supabase_unificado/schema/057_market_us_score_vintages_vitrine.sql.
# O script cria o que grava em vez de supor que a migration rodou: migration
# registrada e nunca executada já deixou verificador e escritor lendo estruturas
# diferentes neste projeto, e o sintoma foi uma tela vazia sem erro nenhum.
DDL_VINTAGES = """
CREATE SCHEMA IF NOT EXISTS market_us;
CREATE TABLE IF NOT EXISTS market_us.score_vintages (
    id            BIGSERIAL   PRIMARY KEY,
    symbol        TEXT        NOT NULL,
    score_version TEXT        NOT NULL,
    as_of_date    DATE        NOT NULL,
    track         TEXT        NOT NULL DEFAULT 'fundamental'
                     CHECK (track IN ('fundamental','asymmetric')),
    score         NUMERIC(10,4),
    coverage         NUMERIC(6,2),
    score_confidence NUMERIC(6,2),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'market_us'
                     AND table_name = 'score_vintages'
                     AND column_name = 'company_id') THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_us_score_vintage_simbolo
            ON market_us.score_vintages (symbol, score_version, as_of_date, track);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_us_score_version_asof
    ON market_us.score_vintages (score_version, as_of_date, track);
ALTER TABLE market_us.score_vintages ENABLE ROW LEVEL SECURITY;
"""

DDL_PRECOS = """
CREATE TABLE IF NOT EXISTS market_us.prices_monthly (
    symbol         TEXT NOT NULL,
    month_end      DATE NOT NULL,
    close          NUMERIC(18,6),
    adjusted_close NUMERIC(18,6),
    volume         BIGINT,
    total_return   NUMERIC(12,6),
    source         TEXT,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, month_end)
);
"""

COLS_VINTAGE = ("symbol", "score_version", "as_of_date", "track", "score",
                "coverage", "score_confidence")
COLS_PRECO = ("symbol", "month_end", "close", "adjusted_close", "volume",
              "total_return")


def versao_corrente() -> str:
    from core.us_methodology import US_FUNDAMENTAL_SCORE_VERSION
    return US_FUNDAMENTAL_SCORE_VERSION


def ler_safras(conn, versao: str) -> list[tuple]:
    """Safras da versão pedida, sem símbolo nulo.

    Safra sem símbolo não é publicável e não é erro de leitura: o painel junta
    por símbolo, então ela nunca participaria da conta. Descartar aqui, e
    contá-la no resumo, é diferente de deixá-la viajar e virar linha órfã na
    vitrine.
    """
    return conn.execute(text(
        f"SELECT {', '.join(COLS_VINTAGE)} FROM market_us.score_vintages "
        "WHERE score_version = :v AND track = :t AND symbol IS NOT NULL "
        "ORDER BY as_of_date, symbol"), {"v": versao, "t": TRACK}).all()


def _iso(valor) -> str:
    """Data como texto ISO, venha ela do PostgreSQL (date) ou do SQLite (str)."""
    return valor.isoformat() if hasattr(valor, "isoformat") else str(valor)


def ler_precos_mensais(conn, versao: str, desde: str | None = None) -> list[tuple]:
    """Série mensal dos símbolos que aparecem na safra, do primeiro ano em diante.

    O recorte é por símbolo e não por data de rebalanço -- ver o cabeçalho: a
    grade completa é o que permite ao painel enxergar onde cada série termina.

    O corte inferior sai de `min(as_of_date)` menos uma folga, calculado aqui e
    não em SQL: `INTERVAL` é PostgreSQL puro, e o mesmo recorte precisa rodar em
    SQLite na suíte. Comparação textual de datas ISO ordena igual nos dois.
    """
    if desde is None:
        primeira = conn.execute(text(
            "SELECT min(as_of_date) FROM market_us.score_vintages "
            "WHERE score_version = :v AND track = :t AND symbol IS NOT NULL"),
            {"v": versao, "t": TRACK}).scalar()
        if primeira is None:
            return []
        # Um ano e um mês antes da primeira safra: o painel busca o preço no mês
        # <= as_of, e sem folga a safra mais antiga ficaria sem âncora se o mês
        # exato faltar na série.
        ano, mes = int(_iso(primeira)[:4]), int(_iso(primeira)[5:7])
        desde = f"{ano - 1:04d}-{mes:02d}-01"
    return conn.execute(text(
        f"SELECT p.{', p.'.join(COLS_PRECO)} FROM market_us.prices_monthly p "
        "WHERE p.symbol IN (SELECT DISTINCT symbol FROM market_us.score_vintages "
        "                   WHERE score_version = :v AND track = :t "
        "                     AND symbol IS NOT NULL) "
        "  AND p.month_end >= :desde "
        "ORDER BY p.symbol, p.month_end"),
        {"v": versao, "t": TRACK, "desde": desde}).all()


def _gravar_em_lotes(destino, sql: str, linhas: list[tuple], *,
                     lote: int = 1000, rotulo: str = "") -> int:
    """Insere com retentativa; o pooler do Supabase derruba conexão longa.

    Sem a retentativa uma publicação de 336 mil linhas morre no meio e deixa a
    vitrine com meia série -- pior que vitrine vazia, porque o painel roda e
    responde com um universo truncado sem dizer que foi truncado.
    """
    from psycopg2.extras import execute_values
    gravadas = 0
    for i in range(0, len(linhas), lote):
        bloco = linhas[i:i + lote]
        erro = None
        for tentativa in range(1, 6):
            try:
                with destino.begin() as conn:
                    cur = conn.connection.cursor()
                    try:
                        execute_values(cur, sql, bloco, page_size=250)
                    finally:
                        cur.close()
                erro = None
                break
            except Exception as exc:  # noqa: BLE001
                erro = exc
                time.sleep(2 * tentativa)
        if erro is not None:
            raise erro
        gravadas += len(bloco)
        if rotulo and gravadas % 25000 == 0:
            print(f"  {rotulo}: {gravadas}/{len(linhas)}", flush=True)
    return gravadas


def _remover_sobras(remoto, safras: list[tuple], versao: str) -> int:
    """Apaga da vitrine a safra que o local não tem mais.

    A publicação é upsert e, até 31/08/2026, só escrevia. Empresa que saía do
    universo -- por exclusão de instrumento, por reclassificação, por qualquer
    motivo -- deixava as linhas antigas na vitrine para sempre, e o app publicado
    seguia rankeando com elas. Foi assim que FGN (F&G Annuities, `excluded` no
    local por não ser ação ordinária) continuou com as safras de 2024 e 2025 no
    ar depois de sair do universo.

    Duas linhas nesse dia, mas o mecanismo não tem teto e trabalha na direção
    errada: quem some do local é justamente quem um filtro decidiu tirar, e a
    vitrine desfazia a decisão em silêncio.

    A conferência antiga também não pegava, porque comparava `remotas < local` --
    e sobra é o caso em que remoto é MAIOR. Assimetria nessa checagem é cegueira
    para metade dos defeitos possíveis; agora ela exige igualdade.
    """
    locais = {(linha[0], _iso(linha[2])) for linha in safras}
    with remoto.connect() as conn:
        remotas = [(r[0], _iso(r[1])) for r in conn.execute(text(
            "SELECT symbol, as_of_date FROM market_us.score_vintages "
            "WHERE score_version = :v AND track = :t"),
            {"v": versao, "t": TRACK})]
    sobras = [k for k in remotas if k not in locais]
    if not sobras:
        return 0
    with remoto.begin() as conn:
        for sym, data in sobras:
            conn.execute(text(
                "DELETE FROM market_us.score_vintages "
                "WHERE symbol = :s AND as_of_date = :d "
                "  AND score_version = :v AND track = :t"),
                {"s": sym, "d": data, "v": versao, "t": TRACK})
    return len(sobras)


def publicar(*, local, remoto, aplicar: bool, versao: str | None = None) -> dict:
    versao = versao or versao_corrente()
    with local.connect() as conn:
        safras = ler_safras(conn, versao)
        if not safras:
            outras = [str(r[0]) for r in conn.execute(text(
                "SELECT DISTINCT score_version FROM market_us.score_vintages "
                "WHERE track = :t ORDER BY 1"), {"t": TRACK})]
            return {"ok": False, "versao": versao, "safras": 0, "precos": 0,
                    "versoes_locais": outras,
                    "motivo": (f"o warehouse local não tem safra da versão {versao}"
                               + (f" (tem {', '.join(outras)})" if outras else "")
                               + "; rode run_us_ingest.py score-history antes")}
        precos = ler_precos_mensais(conn, versao)

    simbolos_safra = {r[0] for r in safras}
    simbolos_preco = {r[0] for r in precos}
    # Símbolo com safra e sem preço não some do resumo: ele é a diferença entre
    # o universo pontuado e o universo mensurável, e é essa diferença que
    # explica um painel menor do que a safra sugere.
    sem_preco = sorted(simbolos_safra - simbolos_preco)
    resumo = {"ok": True, "versao": versao, "safras": len(safras),
              "precos": len(precos), "simbolos": len(simbolos_safra),
              "simbolos_sem_preco": len(sem_preco),
              "exemplos_sem_preco": sem_preco[:10], "gravado": False}
    if not aplicar:
        return resumo

    with remoto.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout='600s'"))
        conn.exec_driver_sql(DDL_VINTAGES)
        conn.exec_driver_sql(DDL_PRECOS)

    sql_v = (f"INSERT INTO market_us.score_vintages ({','.join(COLS_VINTAGE)}) "
             "VALUES %s ON CONFLICT (symbol, score_version, as_of_date, track) "
             "DO UPDATE SET score = EXCLUDED.score, coverage = EXCLUDED.coverage, "
             "score_confidence = EXCLUDED.score_confidence")
    sql_p = (f"INSERT INTO market_us.prices_monthly ({','.join(COLS_PRECO)}) "
             "VALUES %s ON CONFLICT (symbol, month_end) DO UPDATE SET "
             "close = EXCLUDED.close, adjusted_close = EXCLUDED.adjusted_close, "
             "volume = EXCLUDED.volume, total_return = EXCLUDED.total_return")
    resumo["safras_gravadas"] = _gravar_em_lotes(
        remoto, sql_v, safras, rotulo="safras")
    resumo["precos_gravados"] = _gravar_em_lotes(
        remoto, sql_p, precos, rotulo="preços")
    resumo["gravado"] = True

    resumo["safras_removidas"] = _remover_sobras(remoto, safras, versao)

    with remoto.connect() as conn:
        remotas = int(conn.execute(text(
            "SELECT count(*) FROM market_us.score_vintages "
            "WHERE score_version = :v AND track = :t"),
            {"v": versao, "t": TRACK}).scalar_one())
    resumo["safras_na_vitrine"] = remotas
    # Conferência contra o local, não contra o que este processo julga ter
    # gravado: é a única checagem que pega gravação parcial de uma execução
    # anterior interrompida.
    if remotas != len(safras):
        resumo["ok"] = False
        resumo["motivo"] = (f"vitrine diverge do local: local={len(safras)}, "
                            f"vitrine={remotas}")
    return resumo


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", dest="aplicar",
                    help="grava de fato (sem esta flag, apenas simula)")
    ap.add_argument("--versao", default=None,
                    help="publica outra versão de metodologia (padrão: a corrente)")
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
        resumo = publicar(local=local, remoto=remoto, aplicar=args.aplicar,
                          versao=args.versao)
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

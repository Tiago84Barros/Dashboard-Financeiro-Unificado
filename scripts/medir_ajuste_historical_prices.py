# -*- coding: utf-8 -*-
"""
Materializa o FATOR DE AJUSTE de ``market.historical_prices``.

Contexto (medicao de 02/09/2026, armazem local)
----------------------------------------------
A suspeita que originou este script era de lixo: 211 pares (ticker, data) com
``close`` mais de 1.000x acima do fechamento bruto do pregao -- PDGR3 marcando
R$ 11.816.667.136 num mes em que a acao valia R$ 20,99. A medicao refutou a
suspeita. ``market.historical_prices.close`` NAO e preco bruto: e preco
retroajustado por SPLIT (e ``adjusted_close``, por split E provento). O fator e
constante dentro de cada epoca e so da degrau em acao societaria:

    PDGR3   5,6e8 -> 1,1e9  (desdobramento 1:2,   2010-11)
            1,25e9 -> 2,49e7 (grupamento 1:50,    2015-10)
            2,5e6  -> 25.000 (grupamento 1:100,   2023-03)
                   -> 200    (grupamento 1:125,   2025-02)
                   -> 1,0    (grupamento 1:200,   2025-11)

Cruzando com ``market.b3_security_history`` (COTAHIST, serie sadia): 1.059 das
1.090 epocas de fator com 3+ observacoes tem coeficiente de variacao <= 2%, e
todo degrau acima de 10x casa com um grupamento documentado (MGLU3 1:10/2024,
HAPV3 1:15/2025, IRBR3 1:30/2023, BHIA3 1:25/2023, SANB 1:55/2014, TELB3/4
1:10.000/2011). Nao sobrou residuo inexplicavel. Os R$ 11 bilhoes sao a
aritmetica de uma cadeia real de grupamentos, nao corrupcao.

O que isso QUEBRA
-----------------
Nada que leia a serie como RETORNO (razao entre dois pontos): retorno total,
CAGR, drawdown, e o giro financeiro -- ``close`` e ``volume`` sao coajustados
pelo fator inverso, entao o produto e invariante.

Quebra quem le ``close`` como PRECO NEGOCIADO. Em 52.961 linhas mensais com
contraparte bruta, so 30.234 (57%) tem fator ~1. Dois consumidores:

  * ``core/market_read.py::load_fii_metrics_mensal`` -- documenta ``close``
    como "preco bruto (NAO ajustado)" e divide pelo VPA historico. 992 pares
    (ticker, mes) em 51 dos 284 fundos saem fora de escala: FLRP11 exibe P/VP
    historico de 0,008-0,010 contra 0,83-0,99 reais; TEPP11, 0,077 contra
    0,765; SJAU11 chega a 206 contra 20,6. Alimenta o grafico de
    ``views/fiis.py`` E o percentil de valuation contra o proprio historico em
    ``core/global_portfolio/signals.py`` -- decisao, nao so exibicao.
  * ``core/confianca_secao.py::SQL_PROVENTO_IMPLAUSIVEL`` (A-132) -- compara
    ``dividends.amount`` (bruto, R$/cota da epoca) com ``close`` (ajustado).
    Medido: 9 fundos sinalizados hoje contra 6 lendo o preco do pregao; 7
    falsos positivos (BLMO11, CFII11, FYTO11, HGAG11, HGBS11, MCRE11, RDLI11)
    e 4 falsos negativos (BBFI11, HGPO11, PRSN11, TSNC11).

Nenhuma faixa de validacao jamais tocou nisso: ``core/data_quality`` so cobre
multiplos derivados e ``data_pipeline/market/normalize.py::price_rows`` so
descarta ``close <= 0``. Ou seja, ao contrario da armadilha conhecida de faixa
que rejeita gravando NULL, aqui NADA foi apagado -- o valor absurdo foi gravado
inteiro, e e o valor certo.

O que este script FAZ
---------------------
Nao apaga nem reescreve preco. Deriva, por (ticker, data), o fator que separa a
serie ajustada do preco efetivamente negociado -- cruzando com a serie bruta
oficial da B3 -- e o materializa em ``market.price_adjustment_factor``. Com
ele, qualquer consumidor recupera o preco da epoca com ``close / fator``, sem
reingerir nada e sem tocar em ``historical_prices``.

Cobertura e registrada, nunca imputada: linha de ``historical_prices`` sem
pregao bruto no mesmo mes (anterior a 2010 nas acoes, FII fora do arquivo) NAO
recebe fator. Ausencia fica ausencia -- inventar 1,0 ali seria o preenchimento
que nunca contradiz a decisao errada.

``market.b3_security_history`` e lida e nunca escrita.

Uso
---
  python scripts/medir_ajuste_historical_prices.py            # simula (default)
  python scripts/medir_ajuste_historical_prices.py --apply    # grava
  python scripts/medir_ajuste_historical_prices.py --apply --csv

Grava SOMENTE no armazem local (``dfu_warehouse``); o alvo sai de
``scripts/publish_fii_selection_from_local::_warehouse_url`` e o script recusa
qualquer outro destino.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from scripts.publish_fii_selection_from_local import _warehouse_url  # noqa: E402

BACKUP_DIR = ROOT / "migration" / "backup" / "ajuste_historical_prices"

# Hosts aceitos como armazem local. A tabela derivada nao vai para o Supabase:
# e insumo de correcao, nao vitrine, e o plano free ja aperta.
HOSTS_LOCAIS = {"localhost", "127.0.0.1", "::1"}
PORTA_ARMAZEM = 5433

# Tolerancia para chamar o fator de "unitario". Dois centavos num papel de
# R$ 1,00 ja dao 2%; abaixo disso e arredondamento da fonte, nao ajuste.
TOL_FATOR_UNITARIO = 0.02

DDL = """
CREATE TABLE IF NOT EXISTS market.price_adjustment_factor (
    ticker            text        NOT NULL,
    date              date        NOT NULL,
    close_ajustado    numeric     NOT NULL,
    close_bruto       numeric     NOT NULL,
    fator             numeric     NOT NULL,
    trade_date        date        NOT NULL,
    fonte_bruto       text        NOT NULL,
    medido_em         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, date),
    CONSTRAINT price_adjustment_factor_positivo CHECK (fator > 0)
)
"""

COMENTARIO = """
COMMENT ON TABLE market.price_adjustment_factor IS
  'Fator que separa market.historical_prices.close (retroajustado por split) do '
  'preco efetivamente negociado na B3: preco_da_epoca = historical_prices.close / fator. '
  'Derivado do fechamento oficial do ultimo pregao do mes; linha de historical_prices '
  'sem pregao bruto no mes NAO recebe fator (ausencia nao e imputada).'
"""

# Um candle de historical_prices por mes contra o ULTIMO pregao do mesmo mes na
# serie bruta. Casar por data exata perderia a maioria das linhas (a brapi
# carimba dia 1, que raramente e pregao); casar pelo maximo do mes
# contaminaria o fator com a amplitude intramensal.
SQL_FATOR = """
WITH bruto AS (
    SELECT DISTINCT ON (ticker, date_trunc('month', trade_date))
           ticker,
           date_trunc('month', trade_date)::date AS mes,
           trade_date,
           {coluna_bruto} AS preco
      FROM {tabela_bruto}
     WHERE {coluna_bruto} > 0
     ORDER BY ticker, date_trunc('month', trade_date), trade_date DESC
)
SELECT h.ticker, h.date, h.close, b.preco, b.trade_date,
       h.close / b.preco AS fator
  FROM market.historical_prices h
  JOIN bruto b
    ON b.ticker = h.ticker
   AND b.mes = date_trunc('month', h.date)::date
 WHERE h.close > 0
"""

FONTES = (
    ("b3_security_history", "market.b3_security_history", "close_unitario"),
    ("fii_b3_security_history", "market.fii_b3_security_history", "close"),
)


def _valida_alvo(url: str) -> None:
    alvo = urlparse(url)
    host = alvo.hostname or ""
    porta = alvo.port or 5432
    if host not in HOSTS_LOCAIS or porta != PORTA_ARMAZEM:
        raise SystemExit(
            f"alvo recusado ({host}:{porta}): este script grava somente no "
            f"armazem local (porta {PORTA_ARMAZEM})"
        )


def _coletar(conn) -> tuple[list[dict], dict]:
    """Fator por (ticker, data) e o resumo da medicao.

    Acoes e FIIs vem de arquivos B3 distintos. Se os dois cobrissem o mesmo par
    -- nao deveriam --, a acao vence e o empate e contado; escolher por ordem
    de execucao esconderia a sobreposicao.
    """
    linhas: dict[tuple[str, object], dict] = {}
    conflitos = 0
    por_fonte: dict[str, int] = {}
    for nome, tabela, coluna in FONTES:
        sql = SQL_FATOR.format(tabela_bruto=tabela, coluna_bruto=coluna)
        n = 0
        for r in conn.execute(text(sql)).mappings():
            chave = (r["ticker"], r["date"])
            if chave in linhas:
                conflitos += 1
                continue
            linhas[chave] = {
                "ticker": r["ticker"],
                "date": r["date"],
                "close_ajustado": r["close"],
                "close_bruto": r["preco"],
                "fator": r["fator"],
                "trade_date": r["trade_date"],
                "fonte_bruto": nome,
            }
            n += 1
        por_fonte[nome] = n

    total_hp = conn.execute(
        text("SELECT count(*) FROM market.historical_prices WHERE close > 0")
    ).scalar_one()

    registros = list(linhas.values())
    fora = [x for x in registros if abs(float(x["fator"]) - 1.0) > TOL_FATOR_UNITARIO]
    resumo = {
        "linhas_historical_prices": int(total_hp),
        "com_fator": len(registros),
        "sem_contraparte_bruta": int(total_hp) - len(registros),
        "por_fonte": por_fonte,
        "conflito_entre_fontes": conflitos,
        "fator_unitario": len(registros) - len(fora),
        "fator_diferente_de_um": len(fora),
        "tickers_com_ajuste": len({x["ticker"] for x in fora}),
        "maior_fator": max((float(x["fator"]) for x in registros), default=None),
        "menor_fator": min((float(x["fator"]) for x in registros), default=None),
    }
    return registros, resumo


def _csv(registros: list[dict]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destino = BACKUP_DIR / f"fator_{carimbo}.csv"
    campos = ["ticker", "date", "close_ajustado", "close_bruto", "fator",
              "trade_date", "fonte_bruto"]
    with destino.open("w", newline="", encoding="utf-8") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos)
        escritor.writeheader()
        escritor.writerows(registros)
    return destino


def _gravar(conn, registros: list[dict]) -> int:
    conn.execute(text(DDL))
    conn.execute(text(COMENTARIO))
    conn.execute(text("TRUNCATE market.price_adjustment_factor"))
    if not registros:
        return 0
    sql = text(
        "INSERT INTO market.price_adjustment_factor "
        "(ticker, date, close_ajustado, close_bruto, fator, trade_date, fonte_bruto) "
        "VALUES (:ticker, :date, :close_ajustado, :close_bruto, :fator, "
        ":trade_date, :fonte_bruto)"
    )
    for i in range(0, len(registros), 5000):
        conn.execute(sql, registros[i:i + 5000])
    return len(registros)


def _relatar(resumo: dict, registros: list[dict], aplicou: bool) -> None:
    total = resumo["linhas_historical_prices"]
    com = resumo["com_fator"]
    pct = (100.0 * com / total) if total else 0.0
    print(f"\nmarket.historical_prices ......... {total:>9,} linhas com close > 0")
    print(f"  com contraparte bruta .......... {com:>9,} ({pct:.1f}%)")
    for nome, n in resumo["por_fonte"].items():
        print(f"    {nome:<28} {n:>9,}")
    print(f"  SEM contraparte (fica sem fator) {resumo['sem_contraparte_bruta']:>9,}")
    if resumo["conflito_entre_fontes"]:
        print(f"  conflito entre fontes .......... {resumo['conflito_entre_fontes']:>9,}")
    print(f"\n  fator ~1 (preco = negociado) ... {resumo['fator_unitario']:>9,}")
    print(f"  fator != 1 (preco AJUSTADO) .... {resumo['fator_diferente_de_um']:>9,}"
          f"  em {resumo['tickers_com_ajuste']} tickers")
    if resumo["maior_fator"] is not None:
        print(f"  faixa do fator ................. {resumo['menor_fator']:.6g}"
              f" .. {resumo['maior_fator']:.6g}")
    piores = sorted(registros, key=lambda x: -float(x["fator"]))[:5]
    if piores:
        print("\n  maiores fatores:")
        for x in piores:
            print(f"    {x['ticker']:<8} {x['date']}"
                  f"  ajustado {float(x['close_ajustado']):>18,.2f}"
                  f"  negociado {float(x['close_bruto']):>8,.2f}"
                  f"  fator {float(x['fator']):>15,.1f}")
    print(f"\n{'GRAVADO' if aplicou else 'SIMULACAO (nada gravado; use --apply)'}\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--apply", action="store_true",
                    help="grava market.price_adjustment_factor (default: simula)")
    ap.add_argument("--csv", action="store_true",
                    help="exporta o fator medido para migration/backup/")
    args = ap.parse_args()

    url = _warehouse_url()
    _valida_alvo(url)
    engine = create_engine(url)

    with engine.connect() as conn:
        registros, resumo = _coletar(conn)

    if args.csv:
        print(f"csv: {_csv(registros)}")

    if args.apply:
        with engine.begin() as conn:
            gravadas = _gravar(conn, registros)
        print(f"market.price_adjustment_factor: {gravadas:,} linhas")

    _relatar(resumo, registros, args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

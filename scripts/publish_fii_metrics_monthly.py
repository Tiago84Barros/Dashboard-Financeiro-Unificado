"""Publica a série mensal de fundamentos dos FIIs em ``data/public/fii_metrics_monthly.json.gz``.

Lê o armazém local (Docker ``dfu_warehouse``), só leitura. O Supabase passou
dos 500 MB e estourou a cota de egress (restrição em 14/10/2026), então a
``market.fii_metrics_monthly`` de lá parou em 05/2026 enquanto o armazém
estava em dia (9.016 linhas, 325 fundos, até 10/2026, ~1,4 MB). O app lê este
arquivo primeiro (``core.market_read.load_fii_metrics_mensal``) e só cai no
Supabase para o fundo que o arquivo não traz.

Por fundo, uma lista de meses com as colunas de ``COLUNAS`` e, junto,
``fechamento``: o fechamento do último pregão do mês na fita oficial da B3
(``market.fii_b3_security_history.close``, sem retroajuste, coleta mais
recente do mesmo pregão). O leitor divide por VPA para o P/VP -- a mesma conta
de antes, só que com o preço lido do armazém em vez do Supabase. Mês sem
fechamento na fita fica com ``null`` e o P/VP sai nulo (nunca herda preço
ajustado por split).

Recusa publicar (saída 1) se o último mês da série tiver mais de
``SERIE_MAXIMA_DIAS`` dias: renovar ``gerado_em`` sobre série parada faria o
arquivo parecer em dia.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CAMINHO_PADRAO = ROOT / "data" / "public" / "fii_metrics_monthly.json.gz"
VERSAO = 1
# O informe mensal da CVM chega com ~1 mês de atraso; 75 dias dá folga a um
# ciclo perdido sem deixar passar série realmente parada.
SERIE_MAXIMA_DIAS = 75
COLUNAS = ("ref_month", "vpa", "patrimonio_liquido", "num_cotistas",
           "dy_patrimonial_mes", "pct_imoveis", "pct_papel", "pct_caixa",
           "pct_fundos", "fechamento")

METODO = ("Mensal, por fundo. Colunas de market.fii_metrics_monthly (informe "
          "mensal da CVM) mais 'fechamento' = fechamento do último pregão do "
          "mês na fita oficial da B3 (market.fii_b3_security_history, sem "
          "retroajuste; coleta mais recente do mesmo pregão). P/VP = "
          "fechamento ÷ VPA, calculado pelo leitor; mês sem fechamento na "
          "fita fica sem P/VP.")

SQL_METRICAS = """
    SELECT ticker, ref_month, vpa, patrimonio_liquido, num_cotistas,
           dy_patrimonial_mes, pct_imoveis, pct_papel, pct_caixa, pct_fundos
      FROM market.fii_metrics_monthly
     ORDER BY ticker, ref_month
"""
SQL_FECHAMENTOS = """
    SELECT DISTINCT ON (ticker, date_trunc('month', trade_date))
           ticker, date_trunc('month', trade_date)::date AS mes, close
      FROM market.fii_b3_security_history
     WHERE close > 0
     ORDER BY ticker, date_trunc('month', trade_date), trade_date DESC,
              collected_at DESC, id DESC
"""


def _num(v, casas: int = 6):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, casas)


def _mes(d) -> str:
    return f"{d.year:04d}-{d.month:02d}-01"


def montar_payload(metricas: list[dict], fechamentos: list[dict],
                   agora: datetime) -> dict:
    """Puro. ``metricas``: linhas de fii_metrics_monthly; ``fechamentos``:
    ticker, mes (data qualquer do mês), close."""
    fech = {(str(r["ticker"]).strip().upper(), _mes(r["mes"])): _num(r["close"])
            for r in fechamentos}
    por_ticker: dict[str, list] = {}
    ultimo: date | None = None
    for r in metricas:
        tk = str(r["ticker"]).strip().upper()
        mes = r["ref_month"]
        if mes is None:
            continue
        ultimo = mes if ultimo is None or mes > ultimo else ultimo
        por_ticker.setdefault(tk, []).append([
            _mes(mes), _num(r["vpa"]), _num(r["patrimonio_liquido"], 2),
            None if r["num_cotistas"] is None else int(r["num_cotistas"]),
            _num(r["dy_patrimonial_mes"]), _num(r["pct_imoveis"]),
            _num(r["pct_papel"]), _num(r["pct_caixa"]), _num(r["pct_fundos"]),
            fech.get((tk, _mes(mes))),
        ])
    return {
        "versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
        "base_ate": ultimo.isoformat() if ultimo else None,
        "metodo": METODO, "colunas": list(COLUNAS),
        "n_tickers": len(por_ticker),
        "n_linhas": sum(len(v) for v in por_ticker.values()),
        "por_ticker": por_ticker,
    }


def serie_velha(payload: dict, hoje: date) -> str | None:
    """Motivo da recusa, ou ``None`` se a série está em dia."""
    base = payload.get("base_ate")
    if not base:
        return "armazém sem nenhuma linha em market.fii_metrics_monthly"
    idade = (hoje - date.fromisoformat(base)).days
    if idade > SERIE_MAXIMA_DIAS:
        return (f"último mês da série é {base} ({idade} dias); limite "
                f"{SERIE_MAXIMA_DIAS}. Rodar a ingestão de FIIs antes.")
    return None


def serializar(payload: dict, caminho: Path) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    bruto = json.dumps(payload, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
    # mtime=0: mesmo conteúdo, mesmos bytes (não suja o git à toa)
    with open(caminho, "wb") as fh, gzip.GzipFile(
            filename="", mode="wb", fileobj=fh, mtime=0) as gz:
        gz.write(bruto)


def _linhas(conn, sql: str) -> list[dict]:
    from sqlalchemy import text
    return [dict(r) for r in conn.execute(text(sql)).mappings()]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--saida", type=Path, default=CAMINHO_PADRAO)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    from sqlalchemy import create_engine, text

    from scripts.publish_fii_selection_from_local import _warehouse_url
    try:
        conn = create_engine(_warehouse_url()).connect()
    except Exception as exc:
        print(f"armazém local indisponível: {type(exc).__name__}: {exc}")
        return 2
    with conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        metricas = _linhas(conn, SQL_METRICAS)
        fechamentos = _linhas(conn, SQL_FECHAMENTOS)
    agora = datetime.now(timezone.utc)
    payload = montar_payload(metricas, fechamentos, agora)
    relatorio = {k: payload[k] for k in ("base_ate", "n_tickers", "n_linhas")}
    relatorio["linhas_sem_fechamento"] = sum(
        1 for v in payload["por_ticker"].values() for linha in v
        if linha[-1] is None)
    motivo = serie_velha(payload, agora.date())
    if motivo:
        relatorio["erro"] = motivo
        print(json.dumps(relatorio, ensure_ascii=False))
        return 1
    if not args.dry_run:
        serializar(payload, args.saida)
        relatorio["arquivo"] = str(args.saida)
        relatorio["bytes"] = args.saida.stat().st_size
    print(json.dumps(relatorio, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

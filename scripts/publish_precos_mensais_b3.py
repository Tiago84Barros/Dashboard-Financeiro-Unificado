"""Publica o fechamento mensal bruto da B3 em ``data/public/precos_mensais_b3.json.gz``.

Serve à Evolução Patrimonial de quem não tem foto de custódia: a carteira é
remontada mês a mês pelas negociações e movimentações, e cada posição é
avaliada pelo fechamento do último pregão do mês.

O preço precisa ser o da fita, sem retroajuste. ``market.historical_prices``
do Supabase mistura séries ajustadas e não ajustadas por desdobramento: BBAS3
aparece a 24,17 em 10/2023, quando o pregão fechou perto de 48. Quantidade
de então × preço retroajustado subavalia o patrimônio pela metade. Por isso o
arquivo lê o COTAHIST do armazém local (Docker ``dfu_warehouse``), só leitura:

* ações (``market.b3_security_history``, lote padrão): ``close_unitario``, o
  fechamento dividido pelo fator de cotação;
* FIIs (``market.fii_b3_security_history``): ``close``, coleta mais recente
  do mesmo pregão.

Por ticker: ``[mes_inicial, [fechamentos...]]``, um por mês corrido a partir de
``mes_inicial``; mês sem pregão fica ``null`` (o leitor herda o anterior por
poucos meses, nunca inventa).

Recusa publicar (saída 1) se o último pregão tiver mais de
``SERIE_MAXIMA_DIAS`` dias: renovar ``gerado_em`` sobre fita parada faria o
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

CAMINHO_PADRAO = ROOT / "data" / "public" / "precos_mensais_b3.json.gz"
VERSAO = 1
DESDE = date(2019, 1, 1)
# Pregão é diário; dez dias corridos cobrem feriado prolongado e uma rotina
# perdida sem deixar passar fita realmente parada.
SERIE_MAXIMA_DIAS = 10

METODO = ("Mensal, por ticker. Fechamento do último pregão do mês na fita "
          "oficial da B3 (COTAHIST), sem retroajuste por desdobramento: ações "
          "em market.b3_security_history.close_unitario (lote padrão, "
          "fechamento ÷ fator de cotação); FIIs em "
          "market.fii_b3_security_history.close (coleta mais recente do "
          "mesmo pregão). Mês sem pregão fica nulo.")

SQL_ACOES = """
    SELECT DISTINCT ON (ticker, date_trunc('month', trade_date))
           ticker, date_trunc('month', trade_date)::date AS mes,
           trade_date, close_unitario AS close
      FROM market.b3_security_history
     WHERE close_unitario > 0 AND trade_date >= :desde
     ORDER BY ticker, date_trunc('month', trade_date), trade_date DESC,
              collected_at DESC, id DESC
"""
SQL_FIIS = """
    SELECT DISTINCT ON (ticker, date_trunc('month', trade_date))
           ticker, date_trunc('month', trade_date)::date AS mes,
           trade_date, close
      FROM market.fii_b3_security_history
     WHERE close > 0 AND trade_date >= :desde
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
    if math.isnan(f) or math.isinf(f) or f <= 0:
        return None
    return round(f, casas)


def _indice(d: date) -> int:
    return d.year * 12 + d.month - 1


def _rotulo(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def montar_payload(linhas: list[dict], agora: datetime) -> dict:
    """Puro. ``linhas``: ticker, mes (data qualquer do mês), trade_date, close.

    Ticker repetido no mesmo mês (ação e FII com o mesmo código, o que a B3
    não emite, ou duas leituras) fica com o pregão mais recente.
    """
    melhor: dict[tuple[str, int], tuple[date, float]] = {}
    ultimo: date | None = None
    for r in linhas:
        preco = _num(r["close"])
        if preco is None or r["mes"] is None:
            continue
        tk = str(r["ticker"]).strip().upper()
        pregao = r.get("trade_date") or r["mes"]
        chave = (tk, _indice(r["mes"]))
        atual = melhor.get(chave)
        if atual is None or pregao > atual[0]:
            melhor[chave] = (pregao, preco)
        ultimo = pregao if ultimo is None or pregao > ultimo else ultimo

    meses: dict[str, dict[int, float]] = {}
    for (tk, i), (_, preco) in melhor.items():
        meses.setdefault(tk, {})[i] = preco
    por_ticker: dict[str, list] = {}
    for tk in sorted(meses):
        m = meses[tk]
        ini, fim = min(m), max(m)
        por_ticker[tk] = [_rotulo(ini), [m.get(i) for i in range(ini, fim + 1)]]
    return {
        "versao": VERSAO, "gerado_em": agora.isoformat(timespec="seconds"),
        "base_ate": ultimo.isoformat() if ultimo else None,
        "metodo": METODO, "n_tickers": len(por_ticker),
        "n_meses": sum(1 for v in melhor.values()),
        "por_ticker": por_ticker,
    }


def serie_velha(payload: dict, hoje: date) -> str | None:
    """Motivo da recusa, ou ``None`` se a fita está em dia."""
    base = payload.get("base_ate")
    if not base:
        return "armazém sem nenhum pregão na fita da B3"
    idade = (hoje - date.fromisoformat(base)).days
    if idade > SERIE_MAXIMA_DIAS:
        return (f"último pregão da fita é {base} ({idade} dias); limite "
                f"{SERIE_MAXIMA_DIAS}. Rodar a ingestão do COTAHIST antes.")
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
    return [dict(r) for r in conn.execute(text(sql), {"desde": DESDE}).mappings()]


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
        linhas = _linhas(conn, SQL_ACOES) + _linhas(conn, SQL_FIIS)
    agora = datetime.now(timezone.utc)
    payload = montar_payload(linhas, agora)
    relatorio = {k: payload[k] for k in ("base_ate", "n_tickers", "n_meses")}
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

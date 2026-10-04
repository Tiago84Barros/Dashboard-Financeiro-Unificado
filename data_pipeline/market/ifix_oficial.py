"""IFIX oficial da B3: fechamento mensal do índice (retorno total).

A brapi só serve o IFIX *spot* (sem histórico), e a coleta diária dele roda de
manhã — o "fechamento" gravado em ``market.historical_prices`` é, na prática,
um valor intradiário, e some inteiro nos dias em que o job cai (de 25/09 a
01/10/2026 não houve linha). A B3 publica a evolução mensal do índice em
``GetMonthlyEvolution``: o ``indexClosingRate`` de cada mês é o fechamento do
último pregão dele. É essa a série que fecha a atribuição dos FIIs contra o
IFIX mês a mês, e ela se refaz sozinha: cada execução regrava a janela inteira.

Módulo leve de propósito (só ``requests`` e SQL): roda no job diário de
cotações (``data_pipeline.jobs.update_b3_quotes``) sem importar a metodologia
de FIIs. ``data_pipeline.market.fii_pit`` usa as mesmas funções.

A resposta da B3 traz também o mês corrente, ainda aberto, com o valor do dia.
Ele sairia datado no fim do mês (no futuro) e passaria por fechamento — por
isso ``janela_meses_fechados`` corta no último mês encerrado.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import date, timedelta
from typing import Any

FONTE = "b3_official_ifix"
URL_MENSAL = (
    "https://sistemaswebb3-listados.b3.com.br/"
    "indexStatisticsProxy/IndexCall/GetMonthlyEvolution/"
)


def _fim_do_mes(ano: int, mes: int) -> date:
    return date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)


def janela_meses_fechados(hoje: date, meses: int = 13) -> tuple[date, date]:
    """``(início, fim)``: os ``meses`` últimos meses ENCERRADOS antes de ``hoje``."""
    fim = hoje.replace(day=1) - timedelta(days=1)
    ano, mes = fim.year, fim.month - (meses - 1)
    while mes <= 0:
        ano, mes = ano - 1, mes + 12
    return date(ano, mes, 1), fim


def parse_ifix_mensal(payload: Any, *, start: date, end: date) -> list[dict]:
    """``[{date, value}]`` de cada mês em ``[start, end]``, datado no fim do mês."""
    rows: list[dict] = []
    if not isinstance(payload, list):
        return rows
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            month = int(item.get("month"))
            year = int(item.get("year"))
            value = float(item.get("indexClosingRate"))
            reference = _fim_do_mes(year, month)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value) or value <= 0 or not start <= reference <= end:
            continue
        rows.append({"date": reference, "value": value})
    return sorted(rows, key=lambda row: row["date"])


def baixar_ifix_mensal(start: date, end: date) -> tuple[list[dict], str]:
    """Consulta a B3 e devolve as linhas do intervalo e o hash do conteúdo."""
    import requests

    query = {
        "index": "IFIX", "language": "pt-br",
        "dateInitial": start.isoformat(), "dateFinal": end.isoformat(),
    }
    encoded = base64.b64encode(
        json.dumps(query, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii")
    response = requests.get(URL_MENSAL + encoded, timeout=60)
    response.raise_for_status()
    content_hash = hashlib.sha256(response.content).hexdigest()
    return parse_ifix_mensal(response.json(), start=start, end=end), content_hash


def gravar_ifix_mensal(conn, rows: list[dict], content_hash: str) -> int:
    """UPSERT em ``market.historical_prices`` (a linha oficial prevalece no dia)."""
    from sqlalchemy import text

    if not rows:
        return 0
    conn.execute(text("""
        INSERT INTO market.assets (ticker,asset_type,exchange,currency,is_active)
        VALUES ('IFIX','other','B3','BRL',true)
        ON CONFLICT (ticker) DO UPDATE SET is_active=true,updated_at=now()
    """))
    payload = [{"date": r["date"].isoformat(), "value": float(r["value"])} for r in rows]
    conn.execute(text("""
        INSERT INTO market.historical_prices (
            ticker,date,close,adjusted_close,source,knowledge_at,
            availability_quality,content_hash
        ) SELECT 'IFIX',date,value,value,:fonte,
                 (date::timestamp + interval '23 hours 59 minutes') AT TIME ZONE 'America/Sao_Paulo',
                 'verified_publication',:content_hash
        FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS x(date date,value numeric)
        WHERE value IS NOT NULL AND value > 0
        ON CONFLICT (ticker,date) DO UPDATE
        SET close=EXCLUDED.close,adjusted_close=EXCLUDED.adjusted_close,
            source=EXCLUDED.source,knowledge_at=EXCLUDED.knowledge_at,
            availability_quality=EXCLUDED.availability_quality,
            content_hash=EXCLUDED.content_hash,updated_at=now()
    """), {"rows": json.dumps(payload), "content_hash": content_hash, "fonte": FONTE})
    return len(rows)


def ingerir_ifix_mensal(conn, *, start: date, end: date) -> dict:
    """Baixa e grava o intervalo numa conexão já aberta (levanta em erro de rede)."""
    rows, content_hash = baixar_ifix_mensal(start, end)
    if not rows:
        return {"status": "empty", "rows": 0, "content_hash": content_hash}
    gravar_ifix_mensal(conn, rows, content_hash)
    return {"status": "saved", "rows": len(rows), "content_hash": content_hash}


def atualizar_ifix_oficial(engine, *, hoje: date | None = None, meses: int = 13) -> dict:
    """Passo do job diário: nunca levanta — a falha volta nomeada no resultado."""
    start, end = janela_meses_fechados(hoje or date.today(), meses)
    out = {"inicio": start.isoformat(), "fim": end.isoformat()}
    try:
        rows, content_hash = baixar_ifix_mensal(start, end)
        if not rows:
            return {**out, "status": "empty", "rows": 0}
        with engine.begin() as conn:
            gravar_ifix_mensal(conn, rows, content_hash)
        return {**out, "status": "saved", "rows": len(rows),
                "ultimo": rows[-1]["date"].isoformat()}
    except Exception as exc:  # rede, JSON, banco: o job de cotações segue
        return {**out, "status": "failed", "rows": 0,
                "error": f"{type(exc).__name__}: {exc}"[:300]}

"""
core/ir_custo_inicial.py
Custo inicial declarado no IRPF para os ativos anteriores ao extrato da B3.

Cada linha é a posição que o contribuinte informou em Bens e Direitos: ticker,
data do saldo (31/12 do ano-base), quantidade e custo total. Mora em
``user_settings.extra_settings`` do usuário logado, nunca no repositório:
é dado pessoal, e o repositório é público.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

from core.user_accounts import _engine, _extra, locked_preferences, write_preferences
from core.user_context import require_user

FIELD = "ir_custo_inicial"
FONTE_PADRAO = "declarado no IRPF"
COLUNAS = ("ticker", "data", "quantidade", "custo_total", "fonte")


def _numero(v: str) -> Decimal:
    """Aceita 1.234,56 (planilha brasileira) e 1234.56."""
    v = (v or "").strip().replace("R$", "").replace(" ", "")
    if "," in v or re.fullmatch(r"\d{1,3}(\.\d{3})+", v):
        # "1.000" sem vírgula é milhar (quantidade da declaração), não 1,0.
        v = v.replace(".", "").replace(",", ".")
    try:
        return Decimal(v)
    except InvalidOperation:
        raise ValueError(f"número inválido: {v!r}") from None


def _data(v: str) -> date:
    v = (v or "").strip()
    try:
        if "/" in v:
            d, m, a = v.split("/")
            return date(int(a), int(m), int(d))
        return date.fromisoformat(v)
    except ValueError:
        raise ValueError(f"data inválida: {v!r}") from None


def ler_csv(conteudo: bytes | str) -> list[dict]:
    """CSV com cabeçalho ticker;data;quantidade;custo_total[;fonte].

    Recusa o arquivo inteiro na primeira linha inválida: meio arquivo gravado
    daria meia carteira com custo e o resto em silêncio sem.
    """
    if isinstance(conteudo, bytes):
        conteudo = conteudo.decode("utf-8-sig")
    amostra = conteudo.splitlines()[0] if conteudo.strip() else ""
    delim = ";" if amostra.count(";") >= amostra.count(",") else ","
    leitor = csv.DictReader(io.StringIO(conteudo), delimiter=delim)
    cab = {c.strip().lower() for c in leitor.fieldnames or []}
    faltam = [c for c in COLUNAS[:4] if c not in cab]
    if faltam:
        raise ValueError(f"colunas ausentes: {', '.join(faltam)}")
    linhas, vistos = [], set()
    for n, bruta in enumerate(leitor, start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in bruta.items()}
        if not any(r.values()):
            continue
        try:
            ticker = r["ticker"].upper()
            if not ticker:
                raise ValueError("ticker vazio")
            q, custo, d = _numero(r["quantidade"]), _numero(r["custo_total"]), _data(r["data"])
        except ValueError as exc:
            raise ValueError(f"linha {n}: {exc}") from None
        if q <= 0 or custo < 0:
            raise ValueError(f"linha {n}: quantidade deve ser positiva e custo não negativo")
        if (ticker, d) in vistos:
            raise ValueError(f"linha {n}: {ticker} repetido para {d.isoformat()}")
        vistos.add((ticker, d))
        linhas.append({"ticker": ticker, "data": d.isoformat(), "quantidade": format(q, "f"),
                       "custo_total": format(custo, "f"), "fonte": r.get("fonte") or FONTE_PADRAO})
    return sorted(linhas, key=lambda x: (x["ticker"], x["data"]))


def carregar() -> list[dict]:
    uid = require_user()
    with _engine().connect() as conn:
        value = conn.execute(text(
            "SELECT extra_settings FROM user_settings WHERE user_id = :uid"
        ), {"uid": uid}).scalar()
    linhas = _extra(value).get(FIELD)
    return linhas if isinstance(linhas, list) else []


def salvar(linhas: list[dict]) -> None:
    """Substitui a lista inteira (lista vazia apaga)."""
    uid = require_user()
    with _engine().begin() as conn:
        extra = locked_preferences(conn, uid)
        if linhas:
            extra[FIELD] = linhas
        else:
            extra.pop(FIELD, None)
        write_preferences(conn, uid, extra)

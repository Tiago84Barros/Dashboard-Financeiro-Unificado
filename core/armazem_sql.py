"""Consulta SQL só-leitura ao armazém, pelo túnel: as duas pontas num lugar só.

Contexto (15/11/2026): o Supabase deixou de receber atualização e o app4
passa a ler a B3 do armazém local. As leituras de ``core.market_read`` são
dezenas de SELECTs parametrizados; reescrever cada um como rota dedicada do
túnel duplicaria o SQL (e a correção de um ficaria sem o gêmeo). Esta rota
executa o SELECT que o app já tem, com quatro travas que não dependem do
texto da consulta estar certo:

1. **Papel restrito do Postgres** (``scripts/criar_papel_leitura_armazem.py``):
   USAGE e SELECT só nos schemas ``market`` e ``market_us`` e em
   ``public.setores``. Finanças pessoais, carteira, notícias e memória de
   mercado ficam fora do alcance, mesmo com um SELECT bem formado. Sem a URL
   desse papel (``ARMAZEM_SQL_DB_URL``) a rota responde 503; ela **nunca** cai
   para a URL do superusuário.
2. **Transação somente leitura** (sessão e transação), mais
   ``statement_timeout``.
3. **Teto de linhas**: passar dele é erro, não corte -- uma tabela cortada
   em silêncio viraria "a empresa tem menos histórico" na tela.
4. **Um comando só, começando por SELECT ou WITH.** É filtro de pedido mal
   feito, não a barreira de segurança (essa é o papel).

O servidor devolve colunas, o OID do tipo de cada uma e as linhas em JSON; o
cliente remonta o DataFrame com os mesmos tipos que ``pd.read_sql_query``
daria lendo o banco direto (data vira ``datetime.date``, carimbo vira
``datetime64``, ``numeric`` vira float), para que ``market_read`` não perceba
de onde veio a leitura.
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal

import pandas as pd

#: Tetos da rota. O maior SELECT de ``market_read`` (preços diários de toda a
#: B3 num ano) passa de 100 mil linhas; 400 mil deixa folga sem virar dump.
LINHAS_MAX = 400_000
SQL_MAX_CARACTERES = 20_000
PARAMS_MAX = 50
LISTA_MAX = 5_000
TIMEOUT_MS = 20_000

_INICIO_OK = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_COMENTARIO = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_LITERAL = re.compile(r"'(?:[^']|'')*'")

# OIDs do Postgres (pg_type) que pedem conversão no cliente.
_OID_DATA = {1082}
_OID_CARIMBO = {1114, 1184}
_OID_NUMERIC = {1700}


class ConsultaRecusada(ValueError):
    """Pedido que a rota não executa (vira HTTP 400, não 503)."""


def validar_sql(sql: object) -> str:
    """Devolve o SQL aceito ou levanta :class:`ConsultaRecusada`."""
    if not isinstance(sql, str) or not sql.strip():
        raise ConsultaRecusada("informe sql")
    if len(sql) > SQL_MAX_CARACTERES:
        raise ConsultaRecusada(f"sql com mais de {SQL_MAX_CARACTERES} caracteres")
    limpo = _LITERAL.sub("''", _COMENTARIO.sub(" ", sql)).strip().rstrip(";").strip()
    if not _INICIO_OK.match(limpo):
        raise ConsultaRecusada("só SELECT ou WITH")
    if ";" in limpo:
        raise ConsultaRecusada("um comando por chamada")
    return sql.strip().rstrip(";")


def _escalar_ok(valor: object) -> bool:
    return valor is None or isinstance(valor, (str, int, float, bool))


def validar_params(params: object) -> dict:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ConsultaRecusada("params deve ser um objeto")
    if len(params) > PARAMS_MAX:
        raise ConsultaRecusada(f"no máximo {PARAMS_MAX} parâmetros")
    saida = {}
    for chave, valor in params.items():
        if not isinstance(chave, str) or not chave.isidentifier():
            raise ConsultaRecusada("nome de parâmetro inválido")
        if isinstance(valor, list):
            if len(valor) > LISTA_MAX or not all(_escalar_ok(v) for v in valor):
                raise ConsultaRecusada(f"parâmetro {chave}: lista inválida")
        elif not _escalar_ok(valor):
            raise ConsultaRecusada(f"parâmetro {chave}: tipo não aceito")
        saida[chave] = valor
    return saida


def params_para_json(params: dict | None) -> dict:
    """Lado do app: datas e números do numpy viram JSON que o servidor aceita.

    Data vai como texto ISO; o Postgres a converte ao comparar com a coluna,
    como faria com o literal que o psycopg2 manda.
    """
    def _um(v):
        if isinstance(v, (_dt.datetime, _dt.date, pd.Timestamp)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return float(v)
        if hasattr(v, "item"):
            return v.item()
        return v

    saida = {}
    for chave, valor in (params or {}).items():
        if isinstance(valor, (list, tuple, set)):
            saida[chave] = [_um(v) for v in valor]
        else:
            saida[chave] = _um(valor)
    return saida


def executar(engine, sql: str, params: dict, *,
             linhas_max: int = LINHAS_MAX) -> dict:
    """Roda o SELECT numa transação somente leitura e devolve o corpo JSON."""
    from sqlalchemy import text

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text(f"SET LOCAL statement_timeout = {int(TIMEOUT_MS)}"))
            res = conn.execute(text(sql), params)
            colunas = list(res.keys())
            tipos = [getattr(d, "type_code", None) for d in
                     (res.cursor.description or [])]
            linhas = res.fetchmany(linhas_max + 1)
            if len(linhas) > linhas_max:
                raise ConsultaRecusada(
                    f"a consulta passa de {linhas_max} linhas; filtre mais")
    return {"colunas": colunas, "tipos": tipos,
            "linhas": [list(r) for r in linhas]}


def montar_frame(corpo: dict) -> pd.DataFrame:
    """Lado do app: o DataFrame que ``pd.read_sql_query`` daria lendo direto."""
    colunas = corpo.get("colunas")
    linhas = corpo.get("linhas")
    if not isinstance(colunas, list) or not isinstance(linhas, list):
        raise ValueError("resposta sem colunas ou linhas")
    tipos = corpo.get("tipos") or [None] * len(colunas)
    frame = pd.DataFrame.from_records(linhas, columns=colunas)
    for nome, oid in zip(colunas, tipos):
        if frame.empty:
            break
        serie = frame[nome]
        if oid in _OID_DATA:
            frame[nome] = serie.map(
                lambda v: _dt.date.fromisoformat(v) if isinstance(v, str) else v)
        elif oid in _OID_CARIMBO:
            # ISO8601: o isoformat omite a fração quando ela é zero, e a mesma
            # coluna traz "…:23.875634+00:00" e "…:23+00:00"; sem o formato
            # explícito o pandas infere pela 1ª linha e recusa as demais.
            frame[nome] = pd.to_datetime(serie, utc=(oid == 1184), format="ISO8601")
        elif oid in _OID_NUMERIC:
            frame[nome] = pd.to_numeric(serie, errors="coerce")
    return frame

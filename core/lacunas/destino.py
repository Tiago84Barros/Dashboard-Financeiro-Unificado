"""
core/lacunas/destino.py
Onde uma lacuna e gravada: arquivo JSONL local ou tabela ``app_lacunas``.

A escolha NAO usa ``core.destino_local.e_local(get_engine())``. Aquela guarda
responde "este banco e o armazem local?", e na maquina de desenvolvimento o
``get_engine()`` tambem aponta para o Supabase -- a regra mandaria tudo para a
nuvem. A pergunta aqui e outra: "este processo roda onde um agente de IA le o
disco?". Por isso a decisao olha o ambiente, nesta ordem:

1. ``LACUNAS_DESTINO`` (local | supabase | desligado), quando definida;
2. sob pytest, ``desligado`` -- a suite nao suja o log de verdade;
3. repositorio montado em ``/mount/src`` (Streamlit Community Cloud): supabase;
4. qualquer outro caso: local.

Na nuvem guarda-se UMA linha por impressao digital, com contador, e nao uma
linha por evento: o Supabase free ja esta perto do teto de 500 MB.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from sqlalchemy import text

from core.lacunas.evento import Lacuna, status_inicial

DESTINOS = ("local", "supabase", "desligado")

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_LOCAL = RAIZ / "local_staging" / "lacunas" / "eventos.jsonl"


def escolher_destino(env: Mapping[str, str] | None = None,
                     raiz: Path | None = None) -> str:
    env = os.environ if env is None else env
    forcado = (env.get("LACUNAS_DESTINO") or "").strip().lower()
    if forcado in DESTINOS:
        return forcado
    if env.get("PYTEST_CURRENT_TEST"):
        return "desligado"
    raiz_txt = str(RAIZ if raiz is None else raiz).replace("\\", "/")
    if raiz_txt.startswith("/mount/src"):
        return "supabase"
    return "local"


def gravar_local(lacuna: Lacuna, arquivo: Path | None = None) -> None:
    """Acrescenta o evento como uma linha JSON. O caminho padrao e lido na hora
    da chamada, nao na definicao, para poder ser trocado em teste."""
    arquivo = Path(arquivo) if arquivo is not None else ARQUIVO_LOCAL
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    with arquivo.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(lacuna), ensure_ascii=False) + "\n")


# Todas as expressoes do SET leem a linha ANTIGA (Postgres e SQLite), entao
# `reincidente` ve o status de antes de o CASE reabrir a lacuna. Reaberta volta
# ao status de nascimento (`excluded.status`): lacuna volta a `aberta`, detalhe
# tecnico volta a `legitima`.
_UPSERT = """
INSERT INTO app_lacunas (impressao, fonte, modulo, codigo, entidade,
    ultima_mensagem, contexto, primeira_vez, ultima_vez, ocorrencias,
    status, reincidente)
VALUES (:impressao, :fonte, :modulo, :codigo, :entidade,
    :mensagem, {contexto}, :ts, :ts, 1, :status_inicial, FALSE)
ON CONFLICT (impressao) DO UPDATE SET
    ocorrencias = app_lacunas.ocorrencias + 1,
    ultima_vez = excluded.ultima_vez,
    ultima_mensagem = excluded.ultima_mensagem,
    contexto = excluded.contexto,
    reincidente = app_lacunas.reincidente OR app_lacunas.status = 'resolvida',
    status = CASE WHEN app_lacunas.status = 'resolvida' THEN excluded.status
                  ELSE app_lacunas.status END
"""


def gravar_banco(engine, lacuna: Lacuna) -> None:
    """UPSERT de uma lacuna em ``app_lacunas`` (migration 077)."""
    postgres = engine.dialect.name == "postgresql"
    sql = _UPSERT.format(contexto="CAST(:contexto AS jsonb)" if postgres else ":contexto")
    parametros = asdict(lacuna)
    parametros["contexto"] = json.dumps(lacuna.contexto, ensure_ascii=False)
    parametros["status_inicial"] = status_inicial(lacuna.codigo)
    with engine.begin() as con:
        con.execute(text(sql), parametros)

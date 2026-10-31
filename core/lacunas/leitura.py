"""
core/lacunas/leitura.py
I/O do log de lacunas, compartilhado pelo sincronizador noturno
(``scripts/lacunas_sincronizar.py``) e pela aba Restricoes de Configuracoes.

Saiu do script em 05/10/2026 para a aba ler exatamente o que o corretor le --
dois leitores do mesmo log com SQL proprio cada um sao o caminho para o painel
mostrar uma fila e o corretor trabalhar outra.
    memoria: verificador-e-escritor-listas-diferentes
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime
from pathlib import Path

_log = logging.getLogger(__name__)

SQL_LER = """
SELECT impressao, fonte, modulo, codigo, entidade, ultima_mensagem, contexto,
       primeira_vez, ultima_vez, ocorrencias, status, reincidente, pr_url, nota_triagem
FROM app_lacunas
"""
SQL_ATUALIZAR = """
UPDATE app_lacunas
SET status = :status, pr_url = :pr_url, nota_triagem = :nota_triagem,
    reincidente = :reincidente
WHERE impressao = :impressao
"""


def ler_jsonl(caminho: Path) -> list[dict]:
    if not caminho.exists():
        return []
    abrir = gzip.open if caminho.suffix == ".gz" else open
    eventos = []
    with abrir(caminho, "rt", encoding="utf-8") as fh:
        for linha in fh:
            linha = linha.strip()
            if not linha:
                continue
            try:
                eventos.append(json.loads(linha))
            except json.JSONDecodeError:
                _log.warning("linha invalida ignorada em %s", caminho.name)
    return eventos


def ler_json(caminho: Path, padrao):
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return padrao


def gravar_json(caminho: Path, dado) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(caminho.suffix + ".tmp")
    tmp.write_text(json.dumps(dado, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    os.replace(tmp, caminho)


def eventos_locais(pasta: Path) -> list[dict]:
    eventos = []
    for gz in sorted(pasta.glob("eventos-*.jsonl.gz")):
        eventos.extend(ler_jsonl(gz))
    eventos.extend(ler_jsonl(pasta / "eventos.jsonl"))
    return eventos


def ler_cloud(engine) -> list[dict]:
    from sqlalchemy import text

    with engine.connect() as con:
        linhas = [dict(r._mapping) for r in con.execute(text(SQL_LER))]
    for linha in linhas:
        for campo in ("primeira_vez", "ultima_vez"):
            if isinstance(linha.get(campo), datetime):
                linha[campo] = linha[campo].isoformat()
        if isinstance(linha.get("contexto"), str):
            linha["contexto"] = json.loads(linha["contexto"] or "{}")
    return linhas


def gravar_cloud(engine, diferencas: list[dict]) -> None:
    if not diferencas:
        return
    from sqlalchemy import text

    with engine.begin() as con:
        con.execute(text(SQL_ATUALIZAR), diferencas)

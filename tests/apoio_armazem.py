"""Schemas descartáveis no armazém local para testes que precisam do Postgres.

Duas regras, cada uma fechando um jeito de o teste escrever fora do lugar:

- **Nome único por execução.** Com nome fixo, duas suítes rodando juntas
  (duas sessões, dois worktrees) apagavam o schema uma da outra no teardown.
- **``search_path`` sem ``public``.** Os módulos citam as tabelas sem schema;
  com ``{schema},public``, um schema apagado fazia a consulta cair nas tabelas
  reais de ``public`` do armazém. Sem ``public``, o teste falha com
  "relation does not exist" em vez de gravar no armazém de verdade.
"""
import os
import uuid


def schema_descartavel(prefixo: str) -> str:
    """``{prefixo}_{pid}_{6 hex}`` — não colide entre execuções simultâneas."""
    return f"{prefixo}_{os.getpid()}_{uuid.uuid4().hex[:6]}"


def opcoes_conexao(schema: str) -> dict:
    """``connect_args`` que prendem as consultas sem schema ao descartável."""
    return {"options": f"-csearch_path={schema}"}

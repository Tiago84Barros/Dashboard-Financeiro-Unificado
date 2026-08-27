# -*- coding: utf-8 -*-
"""O verificador de schema da vitrine EUA tem de olhar as colunas que ele grava.

Defeito real, medido em 27/08/2026 ao publicar: `_ensure_schema` decidia o que
faltava consultando um dicionario de TRES colunas escrito a mao, enquanto o
upsert montava o INSERT a partir de `_COLS`, com vinte e nove. A migration 051
(`is_investment_company`) existia, estava registrada em `_MIGRATIONS` -- e
`_MIGRATIONS` so roda quando a tabela NAO existe. Na vitrine ja criada o
verificador devolvia "verificado" e o primeiro lote do upsert morria com
`column "is_investment_company" does not exist`, com 2.616 linhas prontas.

Nada foi gravado (a falha veio no primeiro lote), mas a publicacao inteira
parou. A licao e a de sempre: quem verifica e quem escreve tem de ler a mesma
lista, ou concordam so enquanto alguem lembrar de editar as duas.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pub = importlib.import_module("scripts.publish_us_snapshot")


class _Res:
    def __init__(self, valor=None, muitos=()):
        self._v, self._m = valor, muitos

    def scalar(self):
        return self._v

    def scalars(self):
        return list(self._m)


class _Conn:
    def __init__(self, colunas):
        self._colunas = colunas

    def execute(self, stmt, *a, **k):
        sql = str(stmt)
        if "to_regclass" in sql:
            return _Res(valor="market_us.company_snapshots")
        if "information_schema.columns" in sql:
            return _Res(muitos=self._colunas)
        return _Res()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def __init__(self, colunas):
        self._colunas = colunas

    def connect(self):
        return _Conn(self._colunas)


def test_coluna_do_insert_que_falta_no_destino_dispara_ddl(monkeypatch):
    """O caso is_investment_company: presente em _COLS, ausente no destino."""
    executados = []
    monkeypatch.setattr(pub, "_exec_retry",
                        lambda eng, stmt, *a, **k: executados.append(str(stmt)))
    faltando_uma = [c for c in pub._COLS if c != "is_investment_company"]
    assert pub._ensure_schema(_Engine(faltando_uma)) != "verificado"
    assert executados, "nenhum DDL emitido para a coluna ausente"
    assert any("is_investment_company" in s for s in executados)


def test_destino_completo_nao_pega_lock_a_toa(monkeypatch):
    """A carga e longa e retomavel: sem coluna faltando, nada de DDL."""
    executados = []
    monkeypatch.setattr(pub, "_exec_retry",
                        lambda eng, stmt, *a, **k: executados.append(str(stmt)))
    assert pub._ensure_schema(_Engine(list(pub._COLS))) == "verificado"
    assert executados == []


def test_toda_coluna_gravada_tem_de_ser_verificada():
    """Prende a origem da lista, nao o seu conteudo de hoje.

    Se alguem voltar a manter a verificacao a mao, uma coluna nova de `_COLS`
    deixa de ser checada e a publicacao quebra de novo -- no destino, longe
    daqui. Este teste falha antes disso.
    """
    fonte = Path(pub.__file__).read_text(encoding="utf-8")
    assert "missing_columns = [column for column in _COLS if column not in columns]" in fonte

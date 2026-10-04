"""`:nome::tipo` dentro de `sqlalchemy.text()` não é um bind seguido de cast.

O regex de binds do SQLAlchemy exige que o nome NÃO seja seguido de `:` --
então, em `:uid::uuid`, ele recua um caractere e extrai o bind `ui`, deixando o
`d` solto no SQL. A chamada com `{"uid": ...}` falha com "A value is required
for bind parameter 'ui'". Foi assim que criar meta, salvar progresso e os
alertas R5/R6 ficaram mortos sem ninguém ver (o erro dos alertas ia para
`logger.debug`). A forma correta é `CAST(:uid AS uuid)`.

Este arquivo prende três coisas:

1. os binds de metas e alertas compilam com os nomes que o código passa;
2. nenhum literal SQL do repositório volta a usar `:nome::tipo`;
3. a falha de uma regra de alerta não aborta a transação das seguintes.
"""
from __future__ import annotations

import ast
import contextlib
import re
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

RAIZ = Path(__file__).resolve().parents[1]


def _binds(sql: str) -> set[str]:
    return set(text(sql)._bindparams)


# ── 1. Nomes dos binds de metas e alertas ─────────────────────────────────────

def test_binds_de_metas_compilam_com_os_nomes_passados():
    from core import metas

    assert _binds(metas._SQL_UPDATE_PROGRESSO) == {"novo_valor", "goal_id", "uid"}
    assert _binds(metas._SQL_INSERT_META) == {"uid", "nome", "tipo", "alvo", "atual", "prazo"}
    assert _binds(metas._SQL_METAS) == {"uid"}


def test_binds_de_alertas_compilam_com_os_nomes_passados():
    from core import alertas

    assert _binds(alertas._SQL_BUDGETS_COUNT) == {"uid"}
    assert _binds(alertas._SQL_CASHFLOW_MES) == {"uid"}
    assert _binds(alertas._SQL_BUDGET_USAGE) == {"uid"}
    assert _binds(alertas._SQL_GOALS) == {"uid"}


# ── 2. Varredura do repositório ───────────────────────────────────────────────

# Bind (não precedido de `:`, letra ou `\`) imediatamente seguido de `::`.
_BIND_COM_CAST_PG = re.compile(r"(?<![:\w\\]):([A-Za-z_]\w*)::")
# Só literais que parecem SQL: CSS como `:hover::after` também casaria o regex.
_PARECE_SQL = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|WHERE|VALUES)\b")
_PASTAS_IGNORADAS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__",
                     ".claude", "site-packages", ".pytest_cache", ".ruff_cache"}


def _arquivos_python() -> list[Path]:
    arquivos = []
    for caminho in RAIZ.rglob("*.py"):
        # Relativiza ANTES de filtrar: o repositório pode morar dentro de uma
        # pasta ignorada (ex.: worktree em `.claude/worktrees/`), e um filtro
        # sobre o caminho absoluto descartaria tudo e passaria por limpo.
        partes = caminho.relative_to(RAIZ).parts
        if any(p in _PASTAS_IGNORADAS for p in partes[:-1]):
            continue
        arquivos.append(caminho)
    return arquivos


def _literais(caminho: Path):
    try:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    except (SyntaxError, UnicodeDecodeError):
        return
    for no in ast.walk(arvore):
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            yield no.lineno, no.value


def test_nenhum_literal_sql_usa_bind_seguido_de_cast_postgres():
    arquivos = _arquivos_python()
    relativos = {p.relative_to(RAIZ).as_posix() for p in arquivos}

    # Prova de que a varredura olhou de verdade -- e olhou os arquivos do defeito.
    assert len(arquivos) > 200, f"varredura viu só {len(arquivos)} arquivos"
    assert {"core/metas.py", "core/alertas.py", "core/bank_statement_import.py"} <= relativos

    achados = []
    literais_sql = 0
    for caminho in arquivos:
        if caminho == Path(__file__).resolve():
            continue  # os exemplos do detector, logo abaixo, são de propósito
        for linha, valor in _literais(caminho):
            if not _PARECE_SQL.search(valor):
                continue
            literais_sql += 1
            for m in _BIND_COM_CAST_PG.finditer(valor):
                achados.append(f"{caminho.relative_to(RAIZ).as_posix()}:{linha}: {m.group(0)}")

    assert literais_sql > 100, f"só {literais_sql} literais SQL examinados"
    assert not achados, (
        "Use CAST(:nome AS tipo) -- `:nome::tipo` vira o bind `nom` no SQLAlchemy:\n"
        + "\n".join(achados)
    )


def test_varredura_detecta_o_padrao_quebrado():
    """O detector precisa acusar o caso real, senão o teste acima é vazio."""
    assert _BIND_COM_CAST_PG.search("WHERE user_id = :uid::uuid")
    assert not _BIND_COM_CAST_PG.search("WHERE user_id = CAST(:uid AS uuid)")
    assert not _BIND_COM_CAST_PG.search("SELECT id::text, NULL::uuid")
    # e o defeito é real: o SQLAlchemy extrai `ui`, não `uid`.
    assert _binds("WHERE user_id = :uid::uuid") == {"ui"}


# ── 3. Uma regra de alerta que falha não derruba as seguintes ─────────────────

class _ConexaoPostgresFalsa:
    """Imita a semântica do Postgres: erro aborta a transação até um ROLLBACK
    (ou ROLLBACK TO SAVEPOINT). Também confere que os binds compilados batem
    com os parâmetros passados, como o driver faria."""

    def __init__(self, respostas):
        self.respostas = respostas  # [(trecho do SQL, callable(result) | Exception)]
        self.abortada = False
        self.executados: list[str] = []

    def execute(self, stmt, params=None):
        if self.abortada:
            raise RuntimeError("InFailedSqlTransaction: current transaction is aborted")
        faltando = set(getattr(stmt, "_bindparams", {})) - set(params or {})
        if faltando:
            self.abortada = True
            raise RuntimeError(f"A value is required for bind parameter {sorted(faltando)}")
        sql = str(stmt)
        for trecho, resposta in self.respostas:
            if trecho in sql:
                self.executados.append(trecho)
                if isinstance(resposta, Exception):
                    self.abortada = True
                    raise resposta
                return resposta
        raise AssertionError(f"SQL inesperado: {sql[:80]}")

    @contextlib.contextmanager
    def begin_nested(self):
        try:
            yield
        except Exception:
            self.abortada = False  # ROLLBACK TO SAVEPOINT
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _EngineFalsa:
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


def _resultado(rows=(), scalar=None, one=None):
    return SimpleNamespace(fetchall=lambda: list(rows), scalar=lambda: scalar,
                           fetchone=lambda: one)


@pytest.fixture
def banco_falso(monkeypatch):
    import core.database as db
    from core.config import settings

    monkeypatch.setattr(settings, "OWNER_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(db, "get_database_storage_status", lambda: {"ok": False})

    def instalar(respostas):
        conn = _ConexaoPostgresFalsa(respostas)
        monkeypatch.setattr(db, "get_engine", lambda: _EngineFalsa(conn))
        return conn

    return instalar


def test_falha_do_r5_nao_impede_o_r6_de_disparar(banco_falso):
    from core import alertas

    cf = SimpleNamespace(net_cashflow=-100.0, total_income=1000.0, total_expenses_abs=1100.0)
    conn = banco_falso([
        ("v_budget_usage_mtd", _resultado()),
        ("FROM   financial_goals", _resultado()),
        ("asset_quotes", _resultado(scalar=10)),
        ("FROM budgets", RuntimeError("relation \"budgets\" does not exist")),
        ("v_monthly_cashflow", _resultado(one=cf)),
    ])

    titulos = [a["titulo"] for a in alertas._alertas_real()]

    assert "v_monthly_cashflow" in conn.executados
    assert "Saldo negativo no mês atual" in titulos


def test_r5_e_r6_disparam_com_os_binds_corretos(banco_falso):
    from core import alertas

    cf = SimpleNamespace(net_cashflow=-50.0, total_income=500.0, total_expenses_abs=550.0)
    banco_falso([
        ("v_budget_usage_mtd", _resultado()),
        ("FROM   financial_goals", _resultado()),
        ("asset_quotes", _resultado(scalar=10)),
        ("FROM budgets", _resultado(scalar=0)),
        ("v_monthly_cashflow", _resultado(one=cf)),
    ])

    titulos = [a["titulo"] for a in alertas._alertas_real()]

    assert "Orçamentos mensais não cadastrados" in titulos
    assert "Saldo negativo no mês atual" in titulos


def test_inserir_meta_e_atualizar_progresso_chegam_ao_banco(banco_falso, monkeypatch):
    from core import metas
    from core.config import settings

    monkeypatch.setattr(settings, "MOCK_MODE", False)
    monkeypatch.setattr(metas.get_metas, "clear", lambda: None)
    banco_falso([
        ("INSERT INTO financial_goals", _resultado()),
        ("UPDATE financial_goals", _resultado()),
    ])

    assert metas.inserir_meta("Reserva", "emergency_fund", 1000.0, 0.0, date(2027, 1, 1)) == (True, "")
    assert metas.atualizar_progresso("11111111-1111-1111-1111-111111111111", 50.0) == (True, "")

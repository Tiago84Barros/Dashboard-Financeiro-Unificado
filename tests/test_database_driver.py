"""O engine do app e o pipeline precisam usar o psycopg2 instalado.

Sem driver explícito na URL, o SQLAlchemy 2.1 escolhe o psycopg v3, que não
está nas dependências: o pipeline caiu com "No module named 'psycopg'" de 01/10
a 03/10/2026, porque requirements-pipeline.txt aceitava qualquer 2.x.
"""

import re
from pathlib import Path

import pytest

from core import database

RAIZ = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("url", [
    "postgresql://u:p@db.exemplo.supabase.co:5432/postgres",
    "postgres://u:p@localhost:5433/warehouse",
])
def test_url_postgres_sem_driver_ganha_psycopg2(url):
    assert database._com_driver_psycopg2(url).drivername == "postgresql+psycopg2"


@pytest.mark.parametrize("url", [
    "postgresql+psycopg2://u:p@localhost/db",
    "sqlite:///:memory:",
])
def test_url_com_driver_ou_sqlite_fica_como_esta(url):
    assert database._com_driver_psycopg2(url).drivername == url.split(":", 1)[0]


def test_get_engine_postgres_usa_psycopg2(monkeypatch):
    from core.config import settings

    monkeypatch.setattr(type(settings), "db_url",
                        property(lambda self: "postgresql://u:p@localhost:5433/db"))
    database.get_engine.clear()
    try:
        engine = database.get_engine()
        assert engine.dialect.driver == "psycopg2"
        engine.dispose()
    finally:
        database.get_engine.clear()


def _versao_sqlalchemy(arquivo: str) -> str:
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    achado = re.search(r"^sqlalchemy==(\S+)", texto, flags=re.MULTILINE | re.IGNORECASE)
    assert achado, f"{arquivo} precisa fixar a versão exata do sqlalchemy"
    return achado.group(1)


def test_pipeline_e_app_fixam_o_mesmo_sqlalchemy():
    assert _versao_sqlalchemy("requirements-pipeline.txt") == _versao_sqlalchemy("requirements.txt")

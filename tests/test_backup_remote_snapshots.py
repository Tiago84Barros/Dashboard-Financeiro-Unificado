from pathlib import Path

import pytest

from scripts.backup_remote_snapshots import SAFE_TABLE


@pytest.mark.parametrize("value", ["market.fii_selection_inputs", "market_us.company_snapshots"])
def test_snapshot_backup_accepts_qualified_safe_tables(value: str):
    assert SAFE_TABLE.fullmatch(value)


@pytest.mark.parametrize("value", ["public.x;drop table y", "x", "Public.Table", "a.../../b"])
def test_snapshot_backup_rejects_unsafe_table_names(value: str):
    assert not SAFE_TABLE.fullmatch(value)


def test_snapshot_backup_is_ignored_by_git():
    """Backups de produção nunca podem entrar no versionamento.

    Antes o teste exigia que o diretório EXISTISSE — fato da máquina local
    (ele é criado sob demanda e é gitignorado), o que reprovava em checkout
    limpo. O invariante real é a regra de ignore.
    """
    root = Path(__file__).resolve().parents[1]
    regras = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert any(linha.strip().rstrip("/") == "migration/backup" for linha in regras)

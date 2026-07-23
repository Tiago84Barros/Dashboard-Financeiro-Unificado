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
    root = Path(__file__).resolve().parents[1]
    assert (root / "migration" / "backup").is_dir()

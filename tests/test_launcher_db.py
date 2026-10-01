import sqlite3
from pathlib import Path

import pytest

from cold_steel.paradox.launcher_db import LauncherDbError, read_launcher
from conftest import SampleInstall, snapshot


@pytest.fixture
def db(sample_install: SampleInstall) -> Path:
    return sample_install.data_dir / "launcher-v2.sqlite"


def test_playsets_come_oldest_first_without_removed_ones(db: Path) -> None:
    data = read_launcher(db)
    assert [(p.name, p.active) for p in data.playsets] == [
        ("Main Playset", True),
        ("Second Playset", False),
    ]


def test_mods_come_in_load_order(db: Path) -> None:
    main = read_launcher(db).playsets[0]
    assert [(m.name, m.enabled) for m in main.mods] == [
        ("Alpha Interface", True),
        ("Gamma Soundtrack", False),
        ("My Local Tweaks", True),
        ("Unsubscribed Mod", True),
    ]


def test_never_changes_the_file(db: Path) -> None:
    before = snapshot(db.parent, recursive=False)
    read_launcher(db)
    assert snapshot(db.parent, recursive=False) == before


def test_a_layout_we_dont_know_stops_clearly(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE playsets_mods RENAME COLUMN position TO sortIndex")
    conn.close()

    with pytest.raises(LauncherDbError, match=r"playsets_mods\.position"):
        read_launcher(db)


def test_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(LauncherDbError, match="isn't there"):
        read_launcher(tmp_path / "launcher-v2.sqlite")

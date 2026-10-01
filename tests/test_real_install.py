"""Scans your real install and checks that nothing in it changed.

Skipped when there's no Stellaris on this machine. Our cache goes to a
temporary folder, so this test leaves no trace either.
"""

import os
from pathlib import Path

import pytest

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Scanner
from cold_steel.paradox.game import DEFAULT_STEAM_DIRS, GameNotFound, find_game
from conftest import snapshot

pytestmark = pytest.mark.real_install


def test_scanning_the_real_install_changes_nothing(tmp_path: Path) -> None:
    steam_dirs = (
        tuple([Path(os.environ["STEAM_DIR"])] if "STEAM_DIR" in os.environ else [])
        + DEFAULT_STEAM_DIRS
    )
    try:
        game = find_game(steam_dirs)
    except GameNotFound:
        pytest.skip("Stellaris isn't installed here")

    # Paradox's top-level files (the launcher database, dlc_load.json) and the
    # mod/*.mod descriptors. Mod folders are covered by Steam's read-only mount.
    watched = [game.data_dir, game.mod_dir]
    before = [snapshot(folder, recursive=False) for folder in watched]

    library = Scanner(steam_dirs, tmp_path / "mods.msgpack")(JobContext())

    assert library.mods
    assert [snapshot(folder, recursive=False) for folder in watched] == before

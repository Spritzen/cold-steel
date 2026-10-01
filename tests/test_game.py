import json
from pathlib import Path

import pytest

from cold_steel.paradox.game import GameNotFound, find_game, steam_libraries
from conftest import SampleInstall


def test_finds_stellaris_in_a_second_library(sample_install: SampleInstall) -> None:
    game = find_game([sample_install.steam_dir])

    assert game.library_dir == sample_install.root / "games"
    assert game.install_dir == sample_install.root / "games/steamapps/common/Stellaris"
    assert game.version == "v4.5.1"
    assert game.version_name == "Cygnus v4.5.1 (358e)"
    assert game.data_dir == sample_install.data_dir
    assert game.workshop_dir == sample_install.workshop_dir


def test_lists_every_library_main_first(sample_install: SampleInstall) -> None:
    assert steam_libraries(sample_install.steam_dir) == [
        sample_install.steam_dir,
        sample_install.root / "games",
    ]


def test_skips_folders_without_steam(sample_install: SampleInstall, tmp_path: Path) -> None:
    game = find_game([tmp_path / "nothing-here", sample_install.steam_dir])
    assert game.version == "v4.5.1"


def test_says_where_it_looked(tmp_path: Path) -> None:
    with pytest.raises(GameNotFound, match="nothing-here"):
        find_game([tmp_path / "nothing-here"])


def test_refuses_the_windows_build(sample_install: SampleInstall) -> None:
    settings = sample_install.root / "games/steamapps/common/Stellaris/launcher-settings.json"
    data = json.loads(settings.read_text())
    data["gameDataPath"] = "%USER_DOCUMENTS%/Paradox Interactive/Stellaris"
    settings.write_text(json.dumps(data))

    with pytest.raises(GameNotFound, match="Windows build"):
        find_game([sample_install.steam_dir])

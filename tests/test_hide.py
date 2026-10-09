"""Hiding other playsets' saves from the game while it runs (decision 91)."""

from pathlib import Path

import pytest

from cold_steel.core.hide import hidden_dir, hidden_now, hide, restore
from cold_steel.core.jobs import JobContext
from cold_steel.core.saves import SaveScanner
from cold_steel.paradox.game import find_game
from cold_steel.paradox.save import autosaves_to_cloud, local_save_dir
from conftest import SampleInstall, snapshot

UNE = "unitednationsofearth_-15512622"
ELVES = "divineelvenorder_-1997250795"


@pytest.mark.parametrize(
    ("text", "cloud"),
    [
        ('language="l_english"\nautosave_tocloud=no\n', False),
        ('autosave=4\nautosave_tocloud="no"\n', False),
        ("autosave_tocloud=yes\n", True),
        ('language="l_english"\n', True),  # a missing line means the game's default: yes
    ],
)
def test_cloud_autosaves_are_read_from_settings(tmp_path: Path, text: str, cloud: bool) -> None:
    (tmp_path / "settings.txt").write_text(text)
    assert autosaves_to_cloud(tmp_path) is cloud


def test_no_settings_file_means_cant_tell(tmp_path: Path) -> None:
    assert autosaves_to_cloud(tmp_path) is None


@pytest.fixture
def dirs(sample_install: SampleInstall) -> tuple[Path, Path, Path]:
    """`save games/`, `cold_steel_hidden_saves/` and hidden.json."""
    data = sample_install.data_dir
    return local_save_dir(data), hidden_dir(data), sample_install.root / "hidden.json"


def test_hiding_moves_whole_folders_and_restoring_puts_them_back(
    dirs: tuple[Path, Path, Path],
) -> None:
    saves, hidden, record = dirs
    before = snapshot(saves)
    # A save with no local folder (Steam Cloud only) is left alone.
    assert hide([UNE, "cloudonly_1"], saves, hidden, record) == (UNE,)
    assert not (saves / UNE).exists() and (hidden / UNE / "2201.01.26.sav").is_file()
    assert (saves / ELVES).is_dir()
    assert hidden_now(record) == (UNE,)

    result = restore(record)
    assert result.back == (UNE,) and result.stuck == ()
    assert snapshot(saves) == before  # every file as it was, timestamps too
    assert not record.exists() and not hidden.exists()


def test_a_folder_is_never_moved_onto_another(dirs: tuple[Path, Path, Path]) -> None:
    saves, hidden, record = dirs
    (hidden / ELVES).mkdir(parents=True)  # already there, somehow
    assert hide([ELVES], saves, hidden, record) == ()
    assert (saves / ELVES / "2200.01.01.sav").is_file()

    assert hide([UNE], saves, hidden, record) == (UNE,)
    (saves / UNE).mkdir()  # the game made a new folder of the same name meanwhile
    result = restore(record)
    assert result.back == () and result.stuck == (UNE,)
    assert (hidden / UNE / "2201.01.26.sav").is_file()
    assert hidden_now(record) == (UNE,)  # still listed, so it isn't forgotten


def test_a_failed_move_puts_back_what_moved(
    dirs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    saves, hidden, record = dirs
    before = snapshot(saves)
    rename = Path.rename

    def failing(self: Path, target: Path) -> Path:
        if self.name == UNE:
            raise OSError("no room")
        return rename(self, target)

    monkeypatch.setattr(Path, "rename", failing)
    with pytest.raises(OSError, match="no room"):
        hide(sorted([ELVES, UNE]), saves, hidden, record)  # ELVES moves first
    assert snapshot(saves) == before
    assert hidden_now(record) == ()


def test_the_list_is_written_before_anything_moves(
    dirs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    saves, hidden, record = dirs

    def crash(self: Path, target: Path) -> Path:
        assert hidden_now(record) == (UNE,)
        raise SystemExit  # Cold Steel stopped before the move

    monkeypatch.setattr(Path, "rename", crash)
    with pytest.raises(SystemExit):
        hide([UNE], saves, hidden, record)
    monkeypatch.undo()
    # Next start: the folder never moved, so it's just taken off the list.
    assert restore(record).back == ()
    assert (saves / UNE).is_dir() and not record.exists()


def test_hidden_saves_are_still_scanned_as_local(
    sample_install: SampleInstall, dirs: tuple[Path, Path, Path]
) -> None:
    saves, hidden, record = dirs
    game = find_game([sample_install.steam_dir])
    scanner = SaveScanner.for_game(game, sample_install.root / "saves.msgpack")
    before = {s.folder: [(f.name, f.cloud) for f in s.files] for s in scanner(JobContext())}
    hide([UNE, ELVES], saves, hidden, record)
    after = {s.folder: [(f.name, f.cloud) for f in s.files] for s in scanner(JobContext())}
    assert after == before

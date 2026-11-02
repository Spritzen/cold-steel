"""Hiding other playsets' saves from the game while it runs (decision 91)."""

from pathlib import Path

import pytest

from cold_steel.core.hide import HiddenFile, hidden_dir, hidden_now, hide, keys_in_game, restore
from cold_steel.core.jobs import JobContext
from cold_steel.core.saves import SaveScanner
from cold_steel.paradox.game import find_game
from cold_steel.paradox.save import autosaves_to_cloud, local_save_dir
from cold_steel.store.files import load_json, save_json
from conftest import SampleInstall, make_save, snapshot

UNE = "unitednationsofearth_-15512622"
ELVES = "divineelvenorder_-1997250795"


@pytest.mark.parametrize(
    ("text", "cloud"),
    [
        ('language="l_english"\nautosave_tocloud=no\n', False),
        ('autosave=4\nautosave_tocloud="no"\n', False),
        ("autosave_tocloud=yes\n", True),
        ('language="l_english"\n', False),  # the game leaves the line out when it's off
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
    assert hide([UNE, "cloudonly_1"], [], saves, hidden, record) == (UNE,)
    assert not (saves / UNE).exists() and (hidden / UNE / "2201.01.26.sav").is_file()
    assert (saves / ELVES).is_dir()
    assert hidden_now(record) == (UNE,)

    result = restore(record)
    assert result.back == (UNE,) and result.waiting == result.stuck == result.new == ()
    assert snapshot(saves) == before  # every file as it was, timestamps too
    assert not record.exists() and not hidden.exists()


def scan(sample_install: SampleInstall, record: Path) -> dict[str, tuple[str, str]]:
    """Each save by key: its folder and its newest file's empire and date."""
    game = find_game([sample_install.steam_dir])
    scanner = SaveScanner.for_game(game, sample_install.root / "saves.msgpack", record)
    found = {}
    for save in scanner(JobContext()):
        info = save.info
        assert info is not None
        found[save.key] = (save.folder, f"{info.empire} {info.date}")
    return found


def test_a_save_the_game_makes_with_a_hidden_saves_name_gets_its_own_key(
    sample_install: SampleInstall, dirs: tuple[Path, Path, Path]
) -> None:
    """One empire started in two playsets: the game names both games' folders alike."""
    saves, hidden, record = dirs
    hide([ELVES], [], saves, hidden, record, "second")
    make_save(saves / ELVES / "ironman.sav", 'name="Divine Elven Order"\ndate="2204.01.02"\n')

    result = restore(record)  # the game closed
    assert result.back == () and result.stuck == ()
    assert result.waiting == (ELVES,)  # nothing to tell anyone: it comes back when played
    assert result.new == (f"{ELVES}~2",) and result.playset == "second"
    assert (hidden / ELVES / "2200.01.01.sav").is_file()
    found = scan(sample_install, record)
    assert found[ELVES] == (ELVES, "Divine Elven Order 2200.01.01")
    assert found[f"{ELVES}~2"] == (ELVES, "Divine Elven Order 2204.01.02")


def test_each_save_of_one_name_is_in_the_game_when_its_playset_plays(
    sample_install: SampleInstall, dirs: tuple[Path, Path, Path]
) -> None:
    saves, hidden, record = dirs
    hide([ELVES], [], saves, hidden, record, "second")
    make_save(saves / ELVES / "ironman.sav", 'name="Divine Elven Order"\ndate="2204.01.02"\n')
    restore(record)
    first, second = ELVES, f"{ELVES}~2"

    # The first game's playset: its save takes the name back, the other waits.
    assert hide([second], [first], saves, hidden, record, "first") == (second,)
    assert (saves / ELVES / "2200.01.01.sav").is_file()
    assert (hidden / second / "ironman.sav").is_file()
    assert restore(record).waiting == (second,)
    assert scan(sample_install, record)[first][0] == ELVES

    # The second game's playset: swapped the other way.
    assert hide([first], [second], saves, hidden, record, "second") == (first,)
    assert (saves / ELVES / "ironman.sav").is_file()
    assert restore(record).waiting == (first,)
    found = scan(sample_install, record)
    assert found[second] == (ELVES, "Divine Elven Order 2204.01.02")
    assert found[first] == (ELVES, "Divine Elven Order 2200.01.01")

    # A save bound to no playset gives way to the played playset's own, not to another's.
    hide([], [], saves, hidden, record, "third")
    assert (saves / ELVES / "ironman.sav").is_file()  # second stays: first isn't wanted
    hide([], [first], saves, hidden, record, "first")
    assert (saves / ELVES / "2200.01.01.sav").is_file()
    assert (hidden / second / "ironman.sav").is_file()


def test_a_hidden_save_comes_back_once_its_name_is_free(dirs: tuple[Path, Path, Path]) -> None:
    saves, hidden, record = dirs
    hide([ELVES], [], saves, hidden, record, "second")
    make_save(saves / ELVES / "ironman.sav", 'name="Divine Elven Order"\ndate="2204.01.02"\n')
    restore(record)
    for file in (saves / ELVES).iterdir():  # the newer game deleted, from the game's menu
        file.unlink()
    (saves / ELVES).rmdir()
    assert restore(record).back == (ELVES,)
    assert (saves / ELVES / "2200.01.01.sav").is_file()
    assert not record.exists() and not hidden.exists()


def test_saves_left_by_the_old_list_are_sorted_out(
    sample_install: SampleInstall, dirs: tuple[Path, Path, Path]
) -> None:
    """Version 1 of the list knew no keys: a save left hidden when the game made
    one of the same name waits, and the new one gets the next key."""
    saves, hidden, record = dirs
    hidden.mkdir()
    (saves / ELVES).rename(hidden / ELVES)
    make_save(saves / ELVES / "ironman.sav", 'name="Divine Elven Order"\ndate="2204.01.02"\n')
    old = {"version": 1, "save_dir": str(saves), "hidden_dir": str(hidden), "folders": [ELVES]}
    save_json(record, old)
    assert keys_in_game(record, saves, hidden)[ELVES] == f"{ELVES}~2"
    result = restore(record)
    assert result.waiting == (ELVES,) and result.stuck == result.new == ()
    assert set(scan(sample_install, record)) >= {ELVES, f"{ELVES}~2"}
    saved = load_json(record, HiddenFile)
    assert saved is not None and saved.version == 2  # written in the new layout, and says so


def test_a_folder_is_never_moved_onto_another(dirs: tuple[Path, Path, Path]) -> None:
    saves, hidden, record = dirs
    (hidden / UNE).mkdir(parents=True)  # already there, somehow: it takes the key
    assert hide([UNE], [], saves, hidden, record) == ()
    assert (saves / UNE / "2201.01.26.sav").is_file()
    assert keys_in_game(record, saves, hidden)[UNE] == f"{UNE}~2"


def test_a_failed_move_back_is_reported(
    dirs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    saves, hidden, record = dirs
    hide([UNE], [], saves, hidden, record)

    def failing(self: Path, target: Path) -> Path:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "rename", failing)
    result = restore(record)
    assert result.stuck == (UNE,) and result.back == ()
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
        hide(sorted([ELVES, UNE]), [], saves, hidden, record)  # ELVES moves first
    assert snapshot(saves) == before
    assert hidden_now(record) == ()


def test_the_list_is_written_before_anything_moves(
    dirs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    saves, hidden, record = dirs

    def crash(self: Path, target: Path) -> Path:
        assert hidden_now(record) == (UNE,)
        raise SystemExit  # Cold Steel stopped before the move

    with monkeypatch.context() as patch, pytest.raises(SystemExit):
        patch.setattr(Path, "rename", crash)
        hide([UNE], [], saves, hidden, record)
    # Next start: the folder never moved, so it's just taken off the list.
    assert restore(record).back == ()
    assert (saves / UNE).is_dir() and not record.exists()


def test_a_crash_while_bringing_a_save_back_keeps_each_key(
    sample_install: SampleInstall, dirs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    saves, hidden, record = dirs
    hide([ELVES], [], saves, hidden, record, "second")
    make_save(saves / ELVES / "ironman.sav", 'name="Divine Elven Order"\ndate="2204.01.02"\n')
    restore(record)
    second = f"{ELVES}~2"
    rename = Path.rename

    def crash(self: Path, target: Path) -> Path:
        if target.parent == saves:  # the first game, coming back in
            raise SystemExit
        return rename(self, target)

    with monkeypatch.context() as patch, pytest.raises(SystemExit):
        patch.setattr(Path, "rename", crash)
        hide([second], [ELVES], saves, hidden, record, "first")
    assert not (saves / ELVES).exists()  # the second moved out, the first not in yet
    restore(record)  # the next start: the first comes back, under its own key
    assert (saves / ELVES / "2200.01.01.sav").is_file()
    assert (hidden / second / "ironman.sav").is_file()
    assert keys_in_game(record, saves, hidden) == {UNE: UNE, ELVES: ELVES}
    assert scan(sample_install, record)[second] == (ELVES, "Divine Elven Order 2204.01.02")


def test_hidden_saves_are_still_scanned_as_local(
    sample_install: SampleInstall, dirs: tuple[Path, Path, Path]
) -> None:
    saves, hidden, record = dirs
    game = find_game([sample_install.steam_dir])
    scanner = SaveScanner.for_game(game, sample_install.root / "saves.msgpack", record)
    before = {s.key: [(f.name, f.cloud) for f in s.files] for s in scanner(JobContext())}
    hide([UNE, ELVES], [], saves, hidden, record)
    after = {s.key: [(f.name, f.cloud) for f in s.files] for s in scanner(JobContext())}
    assert after == before

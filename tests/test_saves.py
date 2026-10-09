"""Saves: reading one save file's meta, the scan of both save folders, and binding."""

import os
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from cold_steel.core import saves as saves_module
from cold_steel.core.jobs import Cancelled, JobContext
from cold_steel.core.library import Scanner
from cold_steel.core.saves import (
    BindingBook,
    Save,
    SaveFile,
    SaveScanner,
    bound_saves,
    check_save,
    compare_mods,
    played_mods,
    played_since,
    suggest,
    unbound_saves,
)
from cold_steel.paradox.game import find_game
from cold_steel.paradox.save import SaveError, SaveInfo, cloud_save_dirs, read_save_info
from cold_steel.store.saves import Binding, load_bindings
from conftest import CLOUD_SAVES, OTHER_USER_SAVES, SampleInstall, make_save, snapshot

UNE = "unitednationsofearth_-15512622"
ELVES = "divineelvenorder_-1997250795"


def scanner(install: SampleInstall) -> SaveScanner:
    game = find_game((install.steam_dir,))
    return SaveScanner.for_game(game, install.cache_file.with_name("saves.msgpack"))


def scan(install: SampleInstall) -> tuple[Save, ...]:
    return scanner(install)(JobContext())


def test_reads_what_a_save_says_about_itself(sample_install: SampleInstall) -> None:
    info = read_save_info(sample_install.data_dir / f"save games/{UNE}/2201.01.26.sav")
    assert info == SaveInfo(
        empire="United Nations of Earth",
        date="2201.01.26",
        version="Cygnus v4.5.2",
        mods=("Alpha Interface", "Beta Ships"),
        dlcs=("Apocalypse", "Federations"),
    )


def test_a_file_that_isnt_a_save_raises(tmp_path: Path) -> None:
    junk = tmp_path / "junk.sav"
    junk.write_text("not a zip")
    with pytest.raises(SaveError, match="Not a readable save"):
        read_save_info(junk)

    no_meta = tmp_path / "no_meta.sav"
    with zipfile.ZipFile(no_meta, "w") as zf:
        zf.writestr("gamestate", "")
    with pytest.raises(SaveError, match="No meta"):
        read_save_info(no_meta)

    broken = tmp_path / "broken.sav"
    make_save(broken, 'name="Unclosed"\nmods={\n')
    with pytest.raises(SaveError, match="meta is broken"):
        read_save_info(broken)


def test_the_cloud_folder_is_the_last_steam_users(sample_install: SampleInstall) -> None:
    assert cloud_save_dirs(sample_install.steam_dir) == (sample_install.root / CLOUD_SAVES,)


def test_without_a_last_user_every_cloud_folder_is_read(sample_install: SampleInstall) -> None:
    (sample_install.steam_dir / "config/loginusers.vdf").unlink()
    assert cloud_save_dirs(sample_install.steam_dir) == (
        sample_install.root / CLOUD_SAVES,
        sample_install.root / OTHER_USER_SAVES,
    )


def test_saves_are_found_in_both_folders(sample_install: SampleInstall) -> None:
    saves = scan(sample_install)

    # Newest first. Another Steam user's save isn't listed.
    assert [s.folder for s in saves] == [UNE, ELVES]
    une = saves[0]
    assert une.empire == "United Nations of Earth"
    assert [(s.name, s.cloud) for s in une.files] == [
        ("2201.01.26", False),
        ("autosave_2201.01.01", True),
    ]
    assert une.newest.info is not None and une.newest.info.date == "2201.01.26"
    assert une.saved > saves[1].saved


def test_an_unreadable_save_is_listed_with_its_problem(sample_install: SampleInstall) -> None:
    folder = sample_install.data_dir / f"save games/{UNE}"
    (folder / "2201.02.01.sav").write_text("half written")
    (folder / "notes.txt").write_text("not a save")

    une = next(g for g in scan(sample_install) if g.folder == UNE)
    newest = une.newest
    assert newest.name == "2201.02.01"
    assert newest.info is None and "Not a readable save" in newest.problem
    # The save is still named by its newest file that can be read.
    assert une.empire == "United Nations of Earth"
    assert len(une.files) == 3


def test_find_game_remembers_the_steam_folder(sample_install: SampleInstall) -> None:
    assert find_game((sample_install.steam_dir,)).steam_dir == sample_install.steam_dir


def test_scanning_changes_nothing_but_the_cache(sample_install: SampleInstall) -> None:
    before = snapshot(sample_install.root)
    scan(sample_install)
    scan(sample_install)
    assert snapshot(sample_install.root) == before
    assert sample_install.cache_file.with_name("saves.msgpack").is_file()


def test_unchanged_saves_come_from_the_cache(
    sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = scan(sample_install)

    def no_reading(path: Path) -> SaveInfo:
        raise AssertionError(f"an unchanged save was read again: {path}")

    monkeypatch.setattr(saves_module, "read_save_info", no_reading)
    assert scan(sample_install) == first


def test_a_new_or_changed_save_is_read_again(
    sample_install: SampleInstall, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan(sample_install)
    une = sample_install.data_dir / f"save games/{UNE}"
    make_save(une / "2201.03.01.sav", 'name="United Nations of Earth"\ndate="2201.03.01"\n')
    changed = sample_install.root / CLOUD_SAVES / f"{UNE}/autosave_2201.01.01.sav"
    make_save(changed, 'name="United Nations of Earth"\ndate="2201.01.02"\n')
    os.utime(changed, ns=(1, 1))

    read: list[str] = []
    real = read_save_info

    def counting(path: Path) -> SaveInfo:
        read.append(path.name)
        return real(path)

    monkeypatch.setattr(saves_module, "read_save_info", counting)
    scan(sample_install)
    assert sorted(read) == ["2201.03.01.sav", "autosave_2201.01.01.sav"]


def test_missing_save_folders_are_no_saves(sample_install: SampleInstall) -> None:
    job = scanner(sample_install)
    job.local_dir = sample_install.root / "nowhere"
    job.cloud_dirs = ()
    assert job(JobContext()) == ()


def test_cancelling_stops_the_scan(sample_install: SampleInstall) -> None:
    ctx = JobContext()
    ctx.cancel()
    with pytest.raises(Cancelled):
        scanner(sample_install)(ctx)


def test_bind_move_and_unbind_saves(tmp_path: Path) -> None:
    path = tmp_path / "saves.json"
    book = BindingBook.open(path)
    assert book.playset_of(UNE) == ""

    book.bind([UNE, ELVES], "main")
    assert book.saves_of("main") == {UNE, ELVES}
    binding = book.get(UNE)
    assert binding is not None and binding.playset == "main" and binding.bound

    book.bind([ELVES], "second")  # a move
    assert book.saves_of("main") == {UNE}
    assert book.playset_of(ELVES) == "second"

    book.unbind([UNE])
    assert book.get(UNE) == Binding()  # kept, so it's never suggested again
    assert book.saves_of("main") == set()

    # Every change is saved at once.
    saved = load_bindings(path)
    assert saved is not None and saved.saves[ELVES].playset == "second"
    assert BindingBook.open(path).playset_of(ELVES) == "second"


def test_a_deleted_playsets_saves_are_forgotten(tmp_path: Path) -> None:
    book = BindingBook.open(tmp_path / "saves.json")
    book.bind([UNE, ELVES], "gone")
    assert book.forget_playset("gone") == 2
    assert book.get(UNE) is None and book.get(ELVES) is None
    assert book.forget_playset("gone") == 0


def test_an_unreadable_saves_file_is_kept(tmp_path: Path) -> None:
    path = tmp_path / "saves.json"
    path.write_text("{ not json")
    book = BindingBook.open(path)
    assert "couldn't be read" in book.problems[0]
    assert not path.exists()
    assert len(list(tmp_path.glob("saves.unreadable-*.json"))) == 1


def test_saves_split_into_bound_and_unbound(sample_install: SampleInstall, tmp_path: Path) -> None:
    saves = scan(sample_install)
    book = BindingBook.open(tmp_path / "saves.json")
    book.bind([ELVES], "main")
    book.bind([UNE], "deleted elsewhere")

    assert [s.folder for s in bound_saves(saves, book, "main")] == [ELVES]
    # A save bound to a playset that no longer exists counts as unbound.
    assert [s.folder for s in unbound_saves(saves, book, {"main"})] == [UNE]


def test_played_mods_are_what_a_save_file_lists(sample_install: SampleInstall) -> None:
    library = Scanner(*sample_install.scanner_args())(JobContext())
    main = next(p for p in library.launcher_playsets if p.name == "Main Playset")
    # Gamma is turned off, and Unsubscribed Mod isn't installed: the game loads neither.
    assert played_mods(main, library) == ("Alpha Interface", "My Local Tweaks")


def test_saves_written_since_play_with_no_binding(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    saves = scan(sample_install)
    book = BindingBook.open(tmp_path / "saves.json")
    since = saves[1].saved + 1  # after ELVES's last file, before UNE's
    assert played_since(saves, book, since) == [UNE]
    book.bind([UNE], "other")
    assert played_since(saves, book, since) == []  # already bound: left alone


def test_a_save_is_suggested_for_the_one_playset_with_its_mods(
    sample_install: SampleInstall, tmp_path: Path
) -> None:
    saves = scan(sample_install)
    book = BindingBook.open(tmp_path / "saves.json")
    played = {
        "une": ("Alpha Interface", "Beta Ships"),
        "elves": ("Gamma Soundtrack",),
        "elves copy": ("Gamma Soundtrack",),
        "reordered": ("Beta Ships", "Alpha Interface"),
    }
    # Two playsets match ELVES, so it gets no suggestion. Order counts.
    assert suggest(saves, book, played) == {UNE: "une"}

    book.unbind([UNE])  # by choice: never suggested again
    assert suggest(saves, book, played) == {}
    book.bind([UNE], "gone")  # bound to a playset that no longer exists
    assert suggest(saves, book, played) == {UNE: "une"}


def save_made_with(mods: tuple[str, ...], saved: int, version: str = "Cygnus v4.5.1") -> Save:
    info = SaveInfo(empire="Test", date="2200.01.01", version=version, mods=mods)
    return Save("test_1", (SaveFile(Path("test_1/2200.01.01.sav"), False, saved, info),))


def test_a_save_is_compared_with_its_playset() -> None:
    hour = 3600 * 10**9
    save = save_made_with(("A", "B"), saved=1_791_000_000 * 10**9)

    same = check_save(save, ("A", "B"), "v4.5.1")
    assert same.marks == ()
    changed = check_save(save, ("A", "C"), "v4.5.1")
    assert changed.marks == ("Mods differ",)
    assert changed.added == ("C",) and changed.removed == ("B",)
    assert changed.details() == ["Added since: C", "Removed since: B"]
    assert check_save(save, ("B", "A"), "v4.5.1").reordered

    # Built an hour after the save was written.
    built = datetime.fromtimestamp(save.saved / 1e9 + 3600).strftime("%Y-%m-%d %H:%M")
    assert check_save(save, ("A", "B"), "v4.5.1", build=built).older_build
    assert not check_save(save, ("A", "B"), "v4.5.1", build="").older_build  # kept
    assert check_save(save, ("A", "B"), "v4.5.1", patch_written=save.saved + hour).patch_changed
    assert not check_save(save, ("A", "B"), "v4.5.1", patch_written=save.saved - hour).marks

    newer_game = check_save(save, ("A", "B"), "v4.6.0")
    assert newer_game.marks == ("Older game",)
    assert not check_save(save, ("A", "B"), "v4.5.9").older_game  # the patch number doesn't count


def test_compare_mods() -> None:
    assert compare_mods(("A", "B"), ("A", "B")) == ((), (), False)
    assert compare_mods(("A", "B"), ("B", "A")) == ((), (), True)
    assert compare_mods(("A", "B"), ("A", "C", "D")) == (("C", "D"), ("B",), False)


def test_kept_saves_take_the_new_build(tmp_path: Path) -> None:
    book = BindingBook.open(tmp_path / "saves.json")
    book.bind([UNE, ELVES], "built")
    book.keep([UNE, "never bound"], "2026-10-09 15:00")
    une = book.get(UNE)
    assert une is not None and une.playset == "built" and une.build == "2026-10-09 15:00"
    assert book.get("never bound") is None  # keeping doesn't bind
    elves = book.get(ELVES)
    assert elves is not None and elves.build == ""

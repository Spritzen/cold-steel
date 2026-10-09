"""Everything that writes a Paradox file: launcher export, dlc_load.json,
continue_game.json and Play.

Each write must make a dated backup first, and write nothing if it can't
(decision 6).
"""

import sqlite3
import subprocess
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

import pytest

from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.core.play import PlayError, plan_play, play
from cold_steel.core.playsets import PlaysetBook
from cold_steel.core.sync import (
    LauncherDifference,
    SyncError,
    check_launcher_can_open,
    describe_difference,
    export_playset,
    import_playset,
    launcher_difference,
    sync_launcher,
)
from cold_steel.paradox import processes
from cold_steel.paradox.backup import backup_file, backups_of
from cold_steel.paradox.continue_game import ContinueGame
from cold_steel.paradox.descriptor import parse_descriptor
from cold_steel.paradox.dlc_load import DlcLoad, read_dlc_load, write_dlc_load
from cold_steel.paradox.launcher_db import ExportMod, read_launcher, write_playset
from conftest import SampleInstall, snapshot


@pytest.fixture
def library(sample_install: SampleInstall) -> Library:
    return Scanner(*sample_install.scanner_args())(JobContext())


@pytest.fixture
def book(tmp_path: Path, library: Library) -> PlaysetBook:
    return PlaysetBook.open(tmp_path / "playsets.json", library)


@pytest.fixture
def backups(tmp_path: Path) -> Path:
    return tmp_path / "backups"


def dump(db: Path) -> list[str]:
    with closing(sqlite3.connect(db)) as conn:
        return list(conn.iterdump())


# Backups


def test_backups_are_dated_copies_and_old_ones_are_pruned(tmp_path: Path, backups: Path) -> None:
    file = tmp_path / "dlc_load.json"
    for n in range(5):
        file.write_text(str(n))
        backup_file(file, backups, keep=3)
    kept = backups_of(file, backups)
    assert [p.read_text() for p in kept] == ["2", "3", "4"]
    assert all(p.name.startswith("dlc_load.20") for p in kept)


def test_nothing_to_back_up_is_fine(tmp_path: Path, backups: Path) -> None:
    assert backup_file(tmp_path / "missing.json", backups) is None


# dlc_load.json


def test_dlc_load_is_backed_up_before_it_is_written(
    sample_install: SampleInstall, backups: Path
) -> None:
    data = sample_install.data_dir
    (data / "dlc_load.json").write_text('{"enabled_mods":["mod/old.mod"],"disabled_dlcs":[]}')

    new = DlcLoad(("mod/ugc_1.mod", "mod/ugc_2.mod"), ("dlc/dlc002_arachnoid/dlc002.dlc",))
    backup = write_dlc_load(data, new, backups)

    assert backup is not None and "mod/old.mod" in backup.read_text()
    assert read_dlc_load(data) == new
    # Compact, in the launcher's key order.
    assert (data / "dlc_load.json").read_text().startswith('{"enabled_mods":["mod/ugc_1.mod"')


def test_no_backup_means_no_write(sample_install: SampleInstall, tmp_path: Path) -> None:
    data = sample_install.data_dir
    (data / "dlc_load.json").write_text("{}")
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("a file where the backup folder should be")

    with pytest.raises(OSError):
        write_dlc_load(data, DlcLoad(("mod/x.mod",)), blocked / "backups")
    assert (data / "dlc_load.json").read_text() == "{}"


# The launcher database


def test_export_backs_up_the_database_first(
    sample_install: SampleInstall, library: Library, book: PlaysetBook, backups: Path
) -> None:
    db = library.game.launcher_db
    before = dump(db)
    result = export_playset(book, book.playsets[1], library, backups)

    assert result.backup is not None and dump(result.backup) == before
    assert dump(db) != before
    original = db.with_name("launcher-v2.cold-steel-orig.sqlite")
    assert dump(original) == before

    # The one-time original is never overwritten.
    export_playset(book, book.playsets[0], library, backups)
    assert dump(original) == before
    assert len(backups_of(db, backups)) == 2


def test_export_replaces_the_launchers_copy(
    library: Library, book: PlaysetBook, backups: Path
) -> None:
    from cold_steel.core.playsets import move_mods, set_dlc_enabled

    main = book.playsets[0]
    changed = move_mods(main, ["local:my_local"], "workshop:2000000001")
    changed = set_dlc_enabled(changed, "dlc002_arachnoid", True)
    book.update(changed)
    export_playset(book, changed, library, backups)

    launcher = read_launcher(library.game.launcher_db)
    assert [p.name for p in launcher.playsets] == ["Main Playset", "Second Playset"]
    exported = launcher.playsets[0]
    assert [m.name for m in exported.mods] == [
        "My Local Tweaks",
        "Alpha Interface",
        "Gamma Soundtrack",
        "Unsubscribed Mod",  # the launcher still knows it, so it stays
    ]
    assert exported.disabled_dlcs == ("dlc032_cybernetics",)


def test_export_of_a_new_playset_adds_one(
    library: Library, book: PlaysetBook, backups: Path
) -> None:
    from cold_steel.core.playsets import add_mods

    fresh = book.create("Fresh")
    fresh = add_mods(fresh, ["workshop:2000000003", "workshop:2000000002"], library)
    book.update(fresh)
    result = export_playset(book, fresh, library, backups)

    # Beta Ships was never seen by the launcher, so it can't be in its playset yet.
    assert result.skipped == ("Beta Ships",)
    launcher = read_launcher(library.game.launcher_db)
    added = next(p for p in launcher.playsets if p.name == "Fresh")
    assert [m.name for m in added.mods] == ["Gamma Soundtrack"]
    stored = book.get(fresh.id)
    assert stored is not None and stored.launcher_id == added.id == result.launcher_id

    export_playset(book, stored, library, backups)  # again: replaces, doesn't add
    names = [p.name for p in read_launcher(library.game.launcher_db).playsets]
    assert names.count("Fresh") == 1


def test_export_refuses_while_the_launcher_runs(
    library: Library, book: PlaysetBook, backups: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = library.game.launcher_db
    before = snapshot(db.parent, recursive=False)
    monkeypatch.setattr(processes, "running", lambda names: names == processes.LAUNCHER)
    with pytest.raises(SyncError, match="launcher is open"):
        export_playset(book, book.playsets[0], library, backups)
    assert snapshot(db.parent, recursive=False) == before


def test_export_can_make_the_playset_the_launchers_active_one(
    library: Library, book: PlaysetBook, backups: Path
) -> None:
    before = {p.name: p.active for p in read_launcher(library.game.launcher_db).playsets}
    export_playset(book, book.playsets[1], library, backups)
    after = {p.name: p.active for p in read_launcher(library.game.launcher_db).playsets}
    assert after == before  # a plain export leaves the active one alone

    export_playset(book, book.playsets[1], library, backups, active=True)
    launcher = read_launcher(library.game.launcher_db)
    assert [p.name for p in launcher.playsets if p.active] == [book.playsets[1].name]


def test_the_launcher_opens_only_when_nothing_is_in_the_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for running, reason in (
        (processes.LAUNCHER, "already open"),
        (processes.GAME, "Stellaris is running"),
        (frozenset(), "Steam isn't running"),
    ):
        monkeypatch.setattr(processes, "running", lambda names, on=running: names == on)
        with pytest.raises(SyncError, match=reason):
            check_launcher_can_open()

    monkeypatch.setattr(processes, "running", lambda names: names == processes.STEAM)
    check_launcher_can_open()


def test_sync_makes_the_launchers_playsets_ours(
    library: Library, book: PlaysetBook, backups: Path
) -> None:
    db = library.game.launcher_db
    before = dump(db)
    main, second = book.playsets
    book.rename(main.id, "Main, renamed")
    book.delete(second.id)
    fresh = book.create("Fresh")
    book.set_active(fresh.id)
    # Imported twice: both copies point at the same launcher playset.
    again = import_playset(book, library.launcher_playsets[0])

    result = sync_launcher(book, library, backups)

    assert result.backup is not None and dump(result.backup) == before
    assert result.removed == 1  # Second Playset
    launcher = read_launcher(db)
    assert [p.name for p in launcher.playsets] == ["Main, renamed", "Fresh", again.name]
    assert [p.name for p in launcher.playsets if p.active] == ["Fresh"]
    assert len({p.id for p in launcher.playsets}) == 3
    # Each remembers its launcher playset, so the next sync replaces, not adds.
    assert [p.launcher_id for p in book.playsets] == [p.id for p in launcher.playsets]
    sync_launcher(book, library, backups)
    assert read_launcher(db).playsets == launcher.playsets


def test_sync_keeps_the_launchers_active_one_if_it_stays(
    library: Library, book: PlaysetBook, backups: Path
) -> None:
    book.delete(book.playsets[0].id)  # the active one, Main Playset
    book.create("Fresh")
    sync_launcher(book, library, backups)
    launcher = read_launcher(library.game.launcher_db)
    assert [p.name for p in launcher.playsets if p.active] == ["Second Playset"]


def test_sync_refuses_while_the_launcher_runs_or_with_nothing_to_sync(
    library: Library, book: PlaysetBook, backups: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = library.game.launcher_db
    before = snapshot(db.parent, recursive=False)
    monkeypatch.setattr(processes, "running", lambda names: names == processes.LAUNCHER)
    with pytest.raises(SyncError, match="launcher is open"):
        sync_launcher(book, library, backups)
    monkeypatch.setattr(processes, "running", lambda names: False)
    for playset in book.playsets:
        book.delete(playset.id)
    with pytest.raises(SyncError, match="no playsets"):
        sync_launcher(book, library, backups)
    assert snapshot(db.parent, recursive=False) == before


def test_a_failed_backup_leaves_the_database_alone(library: Library, tmp_path: Path) -> None:
    from cold_steel.paradox.launcher_db import LauncherDbError

    db = library.game.launcher_db
    before = dump(db)
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("")
    mod = ExportMod("Alpha", "2000000001", "", "", True)
    with pytest.raises(LauncherDbError, match="nothing was written"):
        write_playset(db, name="X", mods=[mod], backup_dir=blocked / "backups")
    assert dump(db) == before


# Play


def test_the_play_plan_lists_turned_on_mods_in_order(library: Library, book: PlaysetBook) -> None:
    plan = plan_play(book.playsets[0], library)
    # Gamma is turned off; the unsubscribed mod can't load.
    assert plan.load.enabled_mods == ("mod/ugc_2000000001.mod", "mod/my_local.mod")
    assert plan.skipped == ("Unsubscribed Mod",)
    assert plan.load.disabled_dlcs == (
        "dlc/dlc002_arachnoid/dlc002.dlc",
        "dlc/dlc032_machine_age/dlc032.dlc",
    )
    assert plan.new_descriptors == {}


def test_play_adds_a_mod_file_the_launcher_never_wrote(library: Library, book: PlaysetBook) -> None:
    from cold_steel.core.playsets import add_mods

    playset = add_mods(book.create("Beta"), ["workshop:2000000002"], library)
    plan = plan_play(playset, library)
    assert plan.load.enabled_mods == ("mod/ugc_2000000002.mod",)
    (path, text), *_ = plan.new_descriptors.items()
    assert path == library.game.mod_dir / "ugc_2000000002.mod"
    desc = parse_descriptor(text)
    assert desc.name == "Beta Ships"
    assert desc.remote_file_id == "2000000002"
    assert desc.path.endswith("281990/2000000002")


class FakePopen:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        FakePopen.calls.append({"args": args, **kwargs})


@pytest.fixture
def game_exe(library: Library) -> Path:
    exe = library.game.exe
    exe.write_text("#!/bin/sh\n")
    return exe


def test_play_writes_dlc_load_then_starts_the_game(
    library: Library,
    book: PlaysetBook,
    backups: Path,
    game_exe: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(processes, "running", lambda names: names == processes.STEAM)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    FakePopen.calls = []
    plan = plan_play(book.playsets[0], library)
    play(plan, library.game, backups)

    assert read_dlc_load(library.game.data_dir) == plan.load
    (call,) = FakePopen.calls
    assert call["args"] == [str(game_exe), "-gdpr-compliant"]
    assert call["cwd"] == library.game.install_dir
    assert call["start_new_session"] is True


def test_continue_backs_up_continue_game_then_skips_the_menu(
    library: Library,
    book: PlaysetBook,
    backups: Path,
    game_exe: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(processes, "running", lambda names: names == processes.STEAM)
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    FakePopen.calls = []
    path = library.game.data_dir / "continue_game.json"
    path.write_text('{\n\t"title":\t"save games/old_1/2200.01.01"\n}\n')
    target = ContinueGame("une_1", "autosave_2201.01.01", "United Nations of Earth", "2201.01.01")
    plan = replace(plan_play(book.playsets[0], library), continue_from=target)
    play(plan, library.game, backups)

    assert path.read_text() == (
        "{\n"
        '\t"title":\t"save games/une_1/autosave_2201.01.01",\n'
        '\t"desc":\t"United Nations of Earth",\n'
        '\t"date":\t"2201.01.01"\n'
        "}\n"
    )
    (backup,) = backups_of(path, backups)
    assert "old_1" in backup.read_text()
    (call,) = FakePopen.calls
    assert call["args"] == [str(game_exe), "-gdpr-compliant", "--continuelastsave"]


@pytest.mark.parametrize(
    ("running", "message"),
    [
        (processes.GAME | processes.STEAM, "already running"),
        (frozenset(), "Steam isn't running"),
    ],
)
def test_play_refuses_without_writing(
    library: Library,
    book: PlaysetBook,
    backups: Path,
    game_exe: Path,
    monkeypatch: pytest.MonkeyPatch,
    running: frozenset[str],
    message: str,
) -> None:
    monkeypatch.setattr(processes, "running", lambda names: bool(names & running))
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    FakePopen.calls = []
    before = snapshot(library.game.data_dir)
    with pytest.raises(PlayError, match=message):
        play(plan_play(book.playsets[0], library), library.game, backups)
    assert snapshot(library.game.data_dir) == before
    assert FakePopen.calls == []


# Spotting running programs


def test_running_reads_proc(tmp_path: Path) -> None:
    for pid, name in (("12", "bash"), ("345", "dowser"), ("self", "ignored")):
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "comm").write_text(name + "\n")
    assert processes.running(processes.LAUNCHER, tmp_path)
    assert not processes.running(processes.GAME, tmp_path)


def test_a_launcher_copy_that_drifted_is_noticed(
    sample_install: SampleInstall, library: Library, book: PlaysetBook, backups: Path
) -> None:
    from cold_steel.core.playsets import move_mods, remove_mods, set_enabled

    main = book.playsets[0]
    assert not launcher_difference(main, library)  # just imported: the same
    assert not launcher_difference(book.create("Fresh"), library)  # never exported

    changed = move_mods(main, ["workshop:2000000001"], None)  # to the end
    changed = remove_mods(changed, ["workshop:2000000003"])
    changed = set_enabled(changed, ["workshop:2000000001"], False)
    book.update(changed)
    book.rename(main.id, "Renamed")
    stored = book.get(main.id)
    assert stored is not None

    diff = launcher_difference(stored, library)
    assert diff == LauncherDifference(
        missing=(),
        extra=("Gamma Soundtrack",),
        switched=("Alpha Interface",),
        order=True,
        name="Main Playset",
    )
    assert describe_difference(diff) == (
        "The launcher's copy of this playset is out of date: it also has Gamma Soundtrack; "
        "it has a different on/off setting for Alpha Interface; it has the mods in a "
        "different order; it is called \u201cMain Playset\u201d. Starting it from the "
        "launcher plays that copy. Export to launcher, or File \u203a Sync launcher, "
        "updates it."
    )

    # Exported, and read again: the same.
    export_playset(book, stored, library, backups)
    again = Scanner(*sample_install.scanner_args())(JobContext())
    stored = book.get(main.id)
    assert stored is not None and not launcher_difference(stored, again)

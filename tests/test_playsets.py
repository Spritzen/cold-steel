"""Our own playsets: storage, and every change to them."""

from pathlib import Path

import pytest

from cold_steel.core import playsets as ops
from cold_steel.core.jobs import JobContext
from cold_steel.core.library import Library, Scanner
from cold_steel.core.playsets import PlaysetBook
from cold_steel.store.playsets import Playset, PlaysetEntry, load_playsets
from conftest import SampleInstall


@pytest.fixture
def library(sample_install: SampleInstall) -> Library:
    return Scanner(*sample_install.scanner_args())(JobContext())


@pytest.fixture
def book(tmp_path: Path, library: Library) -> PlaysetBook:
    return PlaysetBook.open(tmp_path / "playsets.json", library)


def keys(playset: Playset) -> list[str]:
    return [e.key for e in playset.entries]


def test_the_first_open_copies_the_launchers_playsets(
    tmp_path: Path, book: PlaysetBook, library: Library
) -> None:
    assert [p.name for p in book.playsets] == ["Main Playset", "Second Playset"]
    assert book.active == library.launcher_active
    assert book.playsets[0].launcher_id == library.launcher_playsets[0].id
    # Saved at once, and read back the same.
    assert PlaysetBook.open(tmp_path / "playsets.json").playsets == book.playsets


def test_later_opens_dont_import_again(tmp_path: Path, book: PlaysetBook, library: Library) -> None:
    book.delete(book.playsets[0].id)
    again = PlaysetBook.open(tmp_path / "playsets.json", library)
    assert [p.name for p in again.playsets] == ["Second Playset"]


def test_an_unreadable_file_is_kept_not_overwritten(tmp_path: Path, library: Library) -> None:
    path = tmp_path / "playsets.json"
    path.write_text("{ not json")
    book = PlaysetBook.open(path, library)

    kept = list(tmp_path.glob("playsets.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_text() == "{ not json"
    assert "couldn't be read" in book.problems[0]
    assert len(book.playsets) == 2  # started fresh from the launcher


def test_create_copy_rename_delete(tmp_path: Path, book: PlaysetBook) -> None:
    main = book.playsets[0]
    new = book.create("Fresh")
    copy = book.copy(main.id, "Main copy")
    book.rename(new.id, "Renamed")

    names = [p.name for p in book.playsets]
    assert names == ["Main Playset", "Main copy", "Second Playset", "Renamed"]
    assert keys(book.playsets[1]) == keys(main)
    assert copy.id != main.id and copy.launcher_id == ""  # a copy is a new launcher playset

    book.set_active(copy.id)
    book.delete(copy.id)
    assert book.active == ""
    saved = load_playsets(tmp_path / "playsets.json")
    assert saved is not None and [p.name for p in saved.playsets] == [
        "Main Playset",
        "Second Playset",
        "Renamed",
    ]


def test_unused_name(book: PlaysetBook) -> None:
    assert book.unused_name("Fresh") == "Fresh"
    assert book.unused_name("Main Playset") == "Main Playset (2)"
    book.create("Main Playset (2)")
    assert book.unused_name("Main Playset") == "Main Playset (3)"


def test_add_remove_and_turn_off(book: PlaysetBook, library: Library) -> None:
    second = book.playsets[1]
    added = ops.add_mods(second, ["workshop:2000000002", "workshop:2000000003"], library)
    # Already there: stays where it was. New: at the end, turned on, named.
    assert keys(added) == ["workshop:2000000003", "workshop:2000000002"]
    assert added.entries[1] == PlaysetEntry("workshop:2000000002", True, "Beta Ships")

    off = ops.set_enabled(added, ["workshop:2000000002"], False)
    assert [e.enabled for e in off.entries] == [True, False]
    assert keys(ops.remove_mods(off, ["workshop:2000000003"])) == ["workshop:2000000002"]


ORDER = ["a", "b", "c", "d", "e"]


def playset_of(order: list[str]) -> Playset:
    return Playset(id="x", name="x", entries=tuple(PlaysetEntry(k) for k in order))


@pytest.mark.parametrize(
    ("moving", "before", "expected"),
    [
        (["d"], "b", ["a", "d", "b", "c", "e"]),
        (["e", "b"], "a", ["b", "e", "a", "c", "d"]),  # keeps their own order
        (["a"], None, ["b", "c", "d", "e", "a"]),  # to the end
        (["b", "c"], "c", ["a", "b", "c", "d", "e"]),  # dropped onto itself: stays
        (["a", "c"], "c", ["b", "a", "c", "d", "e"]),
    ],
)
def test_move(moving: list[str], before: str | None, expected: list[str]) -> None:
    assert keys(ops.move_mods(playset_of(ORDER), moving, before)) == expected


def test_dlc_on_and_off() -> None:
    playset = playset_of([])
    off = ops.set_dlc_enabled(playset, "dlc002_arachnoid", False)
    assert off.disabled_dlcs == ("dlc002_arachnoid",)
    assert ops.set_dlc_enabled(off, "dlc002_arachnoid", True).disabled_dlcs == ()


def test_missing_mods_keep_their_names(book: PlaysetBook, library: Library) -> None:
    missing = ops.missing_mods(book.playsets, library)
    assert [(m.key, m.name, m.installed) for m in missing] == [
        ("workshop:2000000099", "Unsubscribed Mod", False)
    ]

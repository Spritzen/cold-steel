"""Hiding other playsets' saves from the game's Load menu (decision 91).

    hide(folders, local_save_dir(data_dir), hidden_dir(data_dir), record)
    ... the game runs ...
    restore(record)

Before Play starts the game, each local save bound to another playset is moved
from `save games/` to `cold_steel_hidden_saves/` beside it. Both are in the
game's data folder, on one disk, so each move is a rename: instant, nothing is
copied and no file changes. When the game closes they're moved back.

The list of what's about to move is written to `hidden.json` before anything
moves, so a crash can't lose track of a save: whatever is on the list is put
back when Cold Steel next starts. A folder is never moved onto one that's
already there, either way.
"""

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

HIDDEN_FOLDER = "cold_steel_hidden_saves"
HIDDEN_VERSION = 1


class HiddenFile(msgspec.Struct):
    version: int = HIDDEN_VERSION
    save_dir: str = ""  # where they came from: `save games/`
    hidden_dir: str = ""  # where they are: `cold_steel_hidden_saves/`
    folders: list[str] = msgspec.field(default_factory=list)  # save folders, by name


@dataclass(frozen=True)
class Restored:
    back: tuple[str, ...] = ()  # save folders moved back
    # Still hidden: a folder of the same name is in `save games/` again, or the
    # move failed. They stay on the list.
    stuck: tuple[str, ...] = ()
    hidden_dir: Path = field(default_factory=Path)


def hidden_dir(data_dir: Path) -> Path:
    """Where hidden saves go: `<Paradox data>/cold_steel_hidden_saves/`."""
    return data_dir / HIDDEN_FOLDER


def hidden_file() -> Path:
    return paths.data_dir() / "hidden.json"


def hidden_now(record: Path) -> tuple[str, ...]:
    """The save folders on the list: hidden, or about to be."""
    data = load_json(record, HiddenFile)
    return tuple(data.folders) if data else ()


def hide(folders: Iterable[str], save_dir: Path, hidden: Path, record: Path) -> tuple[str, ...]:
    """Move these saves' folders out of `save_dir` into `hidden`. Returns those
    moved. A folder not in `save_dir`, or already in `hidden`, stays put, and
    nothing moves while the list holds saves hidden from another data folder.

    Raises OSError if the list can't be written (nothing moved) or a move
    fails (everything moved is put back first).
    """
    moving = [f for f in folders if (save_dir / f).is_dir() and not (hidden / f).exists()]
    if not moving:
        return ()
    data = load_json(record, HiddenFile) or HiddenFile()
    if data.folders and (data.save_dir, data.hidden_dir) != (str(save_dir), str(hidden)):
        return ()  # saves still hidden from another data folder: theirs to put back first
    listed = data.folders + [f for f in moving if f not in data.folders]
    save_json(record, HiddenFile(save_dir=str(save_dir), hidden_dir=str(hidden), folders=listed))
    moved: list[str] = []
    try:
        hidden.mkdir(exist_ok=True)
        for folder in moving:
            (save_dir / folder).rename(hidden / folder)
            moved.append(folder)
    except OSError:
        restore(record)
        raise
    return tuple(moved)


def restore(record: Path) -> Restored:
    """Move every save on the list back to where it came from, unless a folder
    of the same name is there by then. Those stay hidden, and on the list."""
    data = load_json(record, HiddenFile)
    if data is None or not data.folders:
        return Restored()
    save_dir, hidden = Path(data.save_dir), Path(data.hidden_dir)
    back: list[str] = []
    stuck: list[str] = []
    for folder in data.folders:
        source, target = hidden / folder, save_dir / folder
        if not source.exists():
            continue  # never moved, as when Cold Steel stopped just before
        if target.exists():
            stuck.append(folder)
            continue
        try:
            source.rename(target)
        except OSError:
            stuck.append(folder)
        else:
            back.append(folder)
    if stuck:
        save_json(record, msgspec.structs.replace(data, folders=stuck))
    else:
        record.unlink(missing_ok=True)
        with contextlib.suppress(OSError):  # not empty: something else is in it
            hidden.rmdir()
    return Restored(tuple(back), tuple(stuck), hidden)

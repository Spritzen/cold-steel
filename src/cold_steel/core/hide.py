"""Keeping other playsets' saves out of the game's Load menu (decision 91).

    hide(away, wanted, local_save_dir(data_dir), hidden_dir(data_dir), record, playset)
    ... the game runs ...
    restore(record)

Each save has a key: its folder's name, or `<name>~2`, `<name>~3`… when another
save already has that name, as two games started with one empire do (decision
129). Bindings (core/saves.py) and the hidden folder go by key. The game only
ever sees the plain name.

Before Play starts the game, each save bound to another playset (`away`) is
moved from `save games/<name>` to `cold_steel_hidden_saves/<key>`. Then each
hidden save that isn't away comes back where its name is free, the played
playset's own (`wanted`) first. One of those takes the place of a same-named
save bound to no playset, which is hidden in turn. Both folders are in the
game's data folder, on one disk, so each move is a rename: instant, nothing is
copied and no file changes.

When the game closes, hidden saves are moved back, except where a save of the
same name is in `save games/`. That one waits, hidden, until its playset is
played again. Folders the game made while it ran are returned as new, to be
bound to the playset it played.

`hidden.json` lists the hidden saves, the folders in `save games/` whose key
isn't their name, and, while the game runs, the playset it was started with and
the folders it started with. It's written before anything moves, and what's on
disk settles any doubt, so a crash can't lose track of a save. A folder is
never moved onto one that's already there.
"""

import contextlib
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

HIDDEN_FOLDER = "cold_steel_hidden_saves"
HIDDEN_VERSION = 2
SEPARATOR = "~"  # between a folder's name and its number in a key: never in a game's folder name


class HiddenFile(msgspec.Struct):
    version: int = HIDDEN_VERSION
    save_dir: str = ""  # `save games/`
    hidden_dir: str = ""  # `cold_steel_hidden_saves/`
    folders: list[str] = msgspec.field(default_factory=list)  # hidden saves, by key
    # Folders in `save games/` whose save's key isn't their name: name -> key.
    names: dict[str, str] = msgspec.field(default_factory=dict)
    playset: str = ""  # while the game runs: the playset Play started it with
    present: list[str] = msgspec.field(default_factory=list)  # and the folders it started with


@dataclass(frozen=True)
class Restored:
    back: tuple[str, ...] = ()  # saves moved back, by key
    # Still hidden because a save of the same name is in `save games/`. Normal:
    # each comes back when its playset is played.
    waiting: tuple[str, ...] = ()
    stuck: tuple[str, ...] = ()  # still hidden because the move failed
    new: tuple[str, ...] = ()  # saves the game made while it ran, by key
    playset: str = ""  # the playset it ran with
    hidden_dir: Path = field(default_factory=Path)


def hidden_dir(data_dir: Path) -> Path:
    """Where hidden saves go: `<Paradox data>/cold_steel_hidden_saves/`."""
    return data_dir / HIDDEN_FOLDER


def hidden_file() -> Path:
    return paths.data_dir() / "hidden.json"


def folder_of(key: str) -> str:
    """The folder name the game knows a save by."""
    return key.partition(SEPARATOR)[0]


def hidden_now(record: Path) -> tuple[str, ...]:
    """The hidden saves, by key, as the list has them."""
    data = load_json(record, HiddenFile)
    return tuple(data.folders) if data else ()


def restore_pending(record: Path) -> bool:
    """Whether restore has anything to do: saves are hidden, or the game Play
    started hasn't been seen to close."""
    data = load_json(record, HiddenFile)
    return data is not None and bool(data.folders or data.playset)


def keys_in_game(record: Path, save_dir: Path, hidden: Path) -> dict[str, str]:
    """Each folder in `save_dir`, by name, with its save's key. Reads, never writes."""
    data = load_json(record, HiddenFile)
    if data is None or (data.save_dir, data.hidden_dir) != (str(save_dir), str(hidden)):
        data = HiddenFile()
    return _settle(data, save_dir, hidden)[1]


def hide(
    away: Iterable[str],
    wanted: Iterable[str],
    save_dir: Path,
    hidden: Path,
    record: Path,
    playset: str = "",
) -> tuple[str, ...]:
    """Move the saves in `away` (keys) out of `save_dir` into `hidden`, then
    bring back hidden saves whose name is free, those in `wanted` first.
    Remember `playset` and what `save_dir` holds, so restore can tell which
    saves the game makes. Returns the keys moved out.

    Nothing moves while the list holds saves hidden from another data folder.
    Raises OSError if the list can't be written (nothing moved) or a move fails
    (what moved out is put back first).
    """
    data = load_json(record, HiddenFile)
    if data is None or (data.save_dir, data.hidden_dir) != (str(save_dir), str(hidden)):
        if data is not None and data.folders:
            return ()  # saves still hidden from another data folder: theirs to put back first
        data = HiddenFile(save_dir=str(save_dir), hidden_dir=str(hidden))
    data, keys = _settle(data, save_dir, hidden)
    away, wanted = set(away), set(wanted)

    out = [(name, key) for name, key in keys.items() if key in away]
    held = {name: key for name, key in keys.items() if key not in away}
    into: list[tuple[str, str]] = []
    for key in sorted(
        (k for k in data.folders if k not in away), key=lambda k: (k not in wanted, k)
    ):
        name = folder_of(key)
        holder = held.get(name)
        if holder is not None and (key not in wanted or holder in wanted):
            continue  # the name is taken by a save with as good a claim
        if holder is not None:
            out.append((name, holder))  # bound to no playset: it waits, hidden, instead
        held[name] = key
        into.append((name, key))

    try:
        # Listed before they move. A key found in the hidden folder overrides
        # `names`, so whichever moves finished, the list is right.
        data.folders = sorted({*data.folders, *(key for _, key in out)})
        save_json(record, data)
        if out:
            hidden.mkdir(exist_ok=True)
        for name, key in out:
            (save_dir / name).rename(hidden / key)
        data = _settle(data, save_dir, hidden)[0]
        data.names.update({name: key for name, key in into if key != name})
        save_json(record, data)
        for name, key in into:
            (hidden / key).rename(save_dir / name)
    except OSError:
        restore(record)
        raise
    data = _settle(data, save_dir, hidden)[0]
    data.playset, data.present = playset, sorted(_folders(save_dir))
    save_json(record, data)
    return tuple(key for _, key in out)


def restore(record: Path) -> Restored:
    """Move every hidden save back where no save of the same name is, and say
    which saves the game made while it ran. The rest stay hidden, and listed."""
    data = load_json(record, HiddenFile)
    if data is None:
        return Restored()
    save_dir, hidden = Path(data.save_dir), Path(data.hidden_dir)
    data, keys = _settle(data, save_dir, hidden)
    present = set(data.present)
    new = tuple(keys[n] for n in sorted(keys) if n not in present) if data.playset else ()
    playset = data.playset

    taken = set(keys)
    into: list[tuple[str, str]] = []
    waiting: list[str] = []
    for key in data.folders:  # sorted, so a key without a number goes first
        name = folder_of(key)
        if name in taken:
            waiting.append(key)
        else:
            taken.add(name)
            into.append((name, key))
    data.names.update({name: key for name, key in into if key != name})
    data.playset, data.present = "", []
    save_json(record, data)  # listed before they move, as in hide

    back: list[str] = []
    stuck: list[str] = []
    for name, key in into:
        try:
            (hidden / key).rename(save_dir / name)
        except OSError:
            stuck.append(key)
        else:
            back.append(key)
    data = _settle(data, save_dir, hidden)[0]
    if data.folders or data.names:
        save_json(record, data)
    else:
        record.unlink(missing_ok=True)
    if not data.folders:
        with contextlib.suppress(OSError):  # not empty: something else is in it
            hidden.rmdir()
    return Restored(tuple(back), tuple(waiting), tuple(stuck), new, playset, hidden)


def _settle(data: HiddenFile, save_dir: Path, hidden: Path) -> tuple[HiddenFile, dict[str, str]]:
    """The list made to agree with what's on disk, and the key of each folder in
    `save_dir`, by name. A folder whose name a hidden save already has as its key
    gets the next free number, as when the game made it while that one was hidden."""
    stash = _folders(hidden)
    game = _folders(save_dir)
    names = {
        n: k for n, k in data.names.items() if n in game and k not in stash and folder_of(k) == n
    }
    used = stash | set(names.values()) | {n for n in game if n not in names and n not in stash}
    for name in sorted(game - names.keys()):
        if name in stash:
            number = 2
            while f"{name}{SEPARATOR}{number}" in used:
                number += 1
            names[name] = f"{name}{SEPARATOR}{number}"
            used.add(names[name])
    settled = msgspec.structs.replace(data, folders=sorted(stash), names=names)
    return settled, {n: names.get(n, n) for n in game}


def _folders(base: Path) -> set[str]:
    try:
        with os.scandir(base) as it:
            return {d.name for d in it if d.is_dir()}
    except OSError:
        return set()

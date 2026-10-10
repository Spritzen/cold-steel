"""Each playset's empire list, kept in `~/.local/share/cold-steel/empires/`.

    empires/<playset id>.txt   one playset's empires, in the game's own format
    empires/loose.txt          empires that are in no playset's list yet
    empires/lists.json         each list's empire file format, the game it last
                               played, and the mods each empire's things came from
    empires/history/<id>.json  a note on each of a list's versions (its backups)
    empires/checked.json       the game and mods the last empire check saw
    empires_in_game.json       whose list the game's empire file holds

A list is the game's empire file for that playset: Play copies it into the
game, and copies the game's file back when the game closes (decision 107).
Each empire's block is kept byte for byte, as the game wrote it.
"""

from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

STATE_VERSION = 1
LOOSE = "loose"  # the name of the list of empires in no playset's list


class EmpireState(msgspec.Struct):
    """Whose list the game's empire file holds, as Cold Steel last left it."""

    version: int = STATE_VERSION
    owner: str = ""  # the id of the playset whose list is in the game's file; "" for none
    file: str = ""  # the game's empire file
    digest: int = 0  # xxhash of that file's bytes, when Cold Steel last wrote or read it
    running: bool = False  # Play put the list in, and the game hasn't closed since


class Source(msgspec.Struct, frozen=True):
    """A mod that defined something an empire uses."""

    mod: str  # its key, without a pinned copy's suffix
    name: str
    version: str  # its descriptor's version: "3.2". Many Workshop mods have none
    updated: str = ""  # the date of its newest file, which a Steam update changes: "2026-09-22"


class UseSources(msgspec.Struct, frozen=True):
    folder: str  # the folder that defines it: "common/planet_classes"
    key: str  # "pc_pd_cold_superhabitable"
    mods: tuple[Source, ...] = ()  # empty: no installed mod defined it even then


class Sources(msgspec.Struct, frozen=True):
    """The mods that supplied an empire's modded things, recorded when it
    entered its list (Phase 10)."""

    digest: int  # the block they were recorded for (definitions.digest)
    uses: tuple[UseSources, ...] = ()
    # Found by the first check, for an empire already in its list: the mods
    # installed then, not what it was made with (decision 116).
    later: bool = False
    # It entered the list before the mods were known: the next check records them.
    pending: bool = False


class ListInfo(msgspec.Struct, frozen=True):
    """What a list's blocks were written by."""

    # The empire file format they're in: "3.4", from user_empire_designs_v3.4.txt.
    # "" until known. Play won't give a list to a game that uses another (decision 112).
    format: str = ""
    played: str = ""  # the game version that last played it: "v4.5.2"
    sources: dict[str, Sources] = msgspec.field(default_factory=dict)  # by empire name


class ListsFile(msgspec.Struct):
    version: int = STATE_VERSION
    lists: dict[str, ListInfo] = msgspec.field(default_factory=dict)  # by owner


class VersionNote(msgspec.Struct, frozen=True):
    note: str = ""  # what came next: "Imported 3 from Cold Steel Mix", "Before Play, game v4.5.2"
    format: str = ""  # the list's empire file format then


class HistoryFile(msgspec.Struct):
    version: int = STATE_VERSION
    notes: dict[str, VersionNote] = msgspec.field(default_factory=dict)  # by backup's file name


class Checked(msgspec.Struct):
    """What the last empire check saw, to tell when the game or a mod changed."""

    version: int = STATE_VERSION
    game: str = ""  # "v4.5.2"
    layers: dict[str, int] = msgspec.field(default_factory=dict)  # each mod's index fingerprint


def lists_file(root: Path) -> Path:
    return root / "lists.json"


def history_file(owner: str, root: Path) -> Path:
    return root / "history" / f"{owner}.json"


def checked_file(root: Path) -> Path:
    return root / "checked.json"


def empires_dir() -> Path:
    return paths.data_dir() / "empires"


def list_file(owner: str, root: Path) -> Path:
    """A playset's empire list, or the loose one."""
    return root / f"{owner}.txt"


def state_file() -> Path:
    return paths.data_dir() / "empires_in_game.json"


def old_bindings_file(root: Path) -> Path:
    """Where an earlier build of Phase 9 kept empire bindings, beside the lists'
    folder `root`. Read once, to make the first lists, then renamed."""
    return root.parent / "empires.json"


def load_state(path: Path) -> EmpireState:
    """An empty state when there's no file yet, or it can't be read."""
    return load_json(path, EmpireState) or EmpireState()


def save_state(state: EmpireState, path: Path) -> None:
    save_json(path, state)

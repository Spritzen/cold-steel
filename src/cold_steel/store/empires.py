"""Each playset's empire list, kept in `~/.local/share/cold-steel/empires/`.

    empires/<playset id>.txt   one playset's empires, in the game's own format
    empires/loose.txt          empires that are in no playset's list yet
    empires/lists.json         each list's empire file format, and the game it last played
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


class ListInfo(msgspec.Struct, frozen=True):
    """What a list's blocks were written by."""

    # The empire file format they're in: "3.4", from user_empire_designs_v3.4.txt.
    # "" until known. Play won't give a list to a game that uses another (decision 112).
    format: str = ""
    played: str = ""  # the game version that last played it: "v4.5.2"


class ListsFile(msgspec.Struct):
    version: int = STATE_VERSION
    lists: dict[str, ListInfo] = msgspec.field(default_factory=dict)  # by owner


def lists_file(root: Path) -> Path:
    return root / "lists.json"


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

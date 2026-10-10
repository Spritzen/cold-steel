"""Which playsets each empire belongs to, saved as `~/.local/share/cold-steel/empires.json`.

Keyed by the empire's name, its key in the game's empire file. An empire can
belong to several playsets (decision 96). Losing this file loses nothing in
game: only the links between empires and playsets.
"""

from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

EMPIRES_VERSION = 1


class EmpireBinding(msgspec.Struct, frozen=True):
    # The ids of the playsets it belongs to. Empty: you unbound it from the
    # last one, so it's never suggested for a playset again.
    playsets: tuple[str, ...] = ()
    bound: str = ""  # when it was last bound, as "2026-10-10 14:03"


class EmpireBindingsFile(msgspec.Struct):
    version: int = EMPIRES_VERSION
    empires: dict[str, EmpireBinding] = msgspec.field(default_factory=dict)  # by name


def empire_bindings_file() -> Path:
    return paths.data_dir() / "empires.json"


def load_empire_bindings(path: Path | None = None) -> EmpireBindingsFile | None:
    """None when there's no file yet, or it can't be read."""
    return load_json(path or empire_bindings_file(), EmpireBindingsFile)


def save_empire_bindings(data: EmpireBindingsFile, path: Path | None = None) -> None:
    save_json(path or empire_bindings_file(), data)

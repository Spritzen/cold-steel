"""Which playset each game belongs to, saved as `~/.local/share/cold-steel/saves.json`.

Keyed by game folder (`commonwealthofman_1251622081`), which covers the game's
saves in the local folder and in Steam Cloud's (decision 80). Losing this file
loses nothing in game: only the links between games and playsets.
"""

from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

BINDINGS_VERSION = 1


class Binding(msgspec.Struct, frozen=True):
    # The id of the playset the game belongs to. Empty: you unbound it, so it's
    # never suggested for a playset again.
    playset: str = ""
    bound: str = ""  # when, as "2026-10-09 14:03"
    # For a built playset: the build (BuildRecord.built) last accepted for this game.
    build: str = ""


class BindingsFile(msgspec.Struct):
    version: int = BINDINGS_VERSION
    games: dict[str, Binding] = msgspec.field(default_factory=dict)  # by game folder


def bindings_file() -> Path:
    return paths.data_dir() / "saves.json"


def load_bindings(path: Path | None = None) -> BindingsFile | None:
    """None when there's no file yet, or it can't be read."""
    return load_json(path or bindings_file(), BindingsFile)


def save_bindings(data: BindingsFile, path: Path | None = None) -> None:
    save_json(path or bindings_file(), data)

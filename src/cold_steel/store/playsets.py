"""Cold Steel's own playsets, saved as `~/.local/share/cold-steel/playsets.json`.

The launcher database is something we sync with, not where our playsets live:
its layout changes between launcher versions.
"""

from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

PLAYSETS_VERSION = 1


class PlaysetEntry(msgspec.Struct, frozen=True):
    key: str  # a Mod.key: "workshop:<id>" or "local:<name of the .mod file>"
    enabled: bool = True
    name: str = ""  # kept so a mod that's gone from disk still shows its name


class Pin(msgspec.Struct, frozen=True):
    """A Workshop mod the playset plays from a saved copy, not Steam's folder."""

    key: str  # the Workshop mod's Mod.key
    snapshot: str  # the copy's id, in the snapshot store


class Playset(msgspec.Struct, frozen=True):
    id: str
    name: str
    entries: tuple[PlaysetEntry, ...] = ()  # in load order
    disabled_dlcs: tuple[str, ...] = ()  # DLC folder names, e.g. "dlc033_cosmic_storms"
    launcher_id: str = ""  # the launcher playset this was imported from or exported to
    pins: tuple[Pin, ...] = ()  # empty: every mod plays from Steam's folder


class PlaysetFile(msgspec.Struct):
    version: int = PLAYSETS_VERSION
    active: str = ""  # the id of the playset Play uses
    playsets: list[Playset] = msgspec.field(default_factory=list)


def playsets_file() -> Path:
    return paths.data_dir() / "playsets.json"


def load_playsets(path: Path | None = None) -> PlaysetFile | None:
    """None when there's no file yet, or it can't be read."""
    return load_json(path or playsets_file(), PlaysetFile)


def save_playsets(data: PlaysetFile, path: Path | None = None) -> None:
    save_json(path or playsets_file(), data)

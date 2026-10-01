"""A playset's conflict choices, saved as
`~/.local/share/cold-steel/resolutions/<playset id>.json`.

Each choice records every version of the object at the moment it was made, so
a later change to any of them can be noticed.
"""

from pathlib import Path

import msgspec

from cold_steel.store import paths
from cold_steel.store.files import load_json, save_json

RESOLUTIONS_VERSION = 1


class Seen(msgspec.Struct, frozen=True):
    """One version of an object, as it was when the choice was made."""

    layer: str  # "game" or a Mod.key
    path: str
    digest: int  # the object's digest, or the file's hash


class Resolution(msgspec.Struct, frozen=True):
    kind: str  # as in Conflict.kind: "file", or a kind of object
    key: str  # as in Conflict.key
    # The chosen version. Both are empty for the user's own version.
    layer: str = ""
    path: str = ""
    text: str = ""  # the user's own version
    seen: tuple[Seen, ...] = ()  # every version when the choice was made

    @property
    def own(self) -> bool:
        return not self.layer


class Ignore(msgspec.Struct, frozen=True):
    """One ignore rule. Set `kind` and `key` for one conflict, `kind` alone for
    every conflict of a type, or `mod` alone for every conflict of a mod."""

    kind: str = ""
    key: str = ""
    mod: str = ""


class ResolutionFile(msgspec.Struct):
    version: int = RESOLUTIONS_VERSION
    resolutions: list[Resolution] = msgspec.field(default_factory=list)
    ignored: list[Ignore] = msgspec.field(default_factory=list)
    # Which choices the patch mod was last made from (see core.resolve.choices_digest).
    built: int = 0


def resolutions_dir() -> Path:
    return paths.data_dir() / "resolutions"


def resolutions_file(playset_id: str, folder: Path | None = None) -> Path:
    return (folder or resolutions_dir()) / f"{playset_id}.json"


def load_resolutions(path: Path) -> ResolutionFile | None:
    """None when there's no file yet, or it can't be read."""
    return load_json(path, ResolutionFile)


def save_resolutions(data: ResolutionFile, path: Path) -> None:
    save_json(path, data)

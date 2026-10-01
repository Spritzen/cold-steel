"""A playset's choices for its conflicts: which version wins, the user's own
version, and which conflicts to ignore.

    book = ResolutionBook.open(resolutions_file(playset.id))
    book.choose(conflict, conflict.claims[0], index)
    book.state(conflict, index)  -> NONE, CHOSEN or STALE

A choice is STALE when any version of the object changed after it was made: a
mod or the game updated, or a mod was added or removed. Its choice was made
against text that's no longer there, so it needs another look before the patch
mod uses it again.

Every change is saved at once.
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal

import msgspec
import xxhash

from cold_steel.core.conflicts import Claim, Conflict
from cold_steel.core.index import Index
from cold_steel.store.resolutions import (
    Ignore,
    Resolution,
    ResolutionFile,
    Seen,
    load_resolutions,
    resolutions_file,
    save_resolutions,
)

type State = Literal["none", "chosen", "stale"]
NONE: State = "none"
CHOSEN: State = "chosen"
STALE: State = "stale"


class ResolutionBook:
    def __init__(self, data: ResolutionFile, path: Path) -> None:
        self._data = data
        self._path = path
        self.problems: list[str] = []
        self._by_key = {(r.kind, r.key): r for r in data.resolutions}

    @classmethod
    def open(cls, path: Path) -> ResolutionBook:
        """Load a playset's choices. A file that can't be read is renamed, not
        overwritten, so nothing in it is lost."""
        data = load_resolutions(path)
        problems: list[str] = []
        if data is None and path.exists():
            stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            kept = path.with_name(f"{path.stem}.unreadable-{stamp}{path.suffix}")
            path.replace(kept)
            problems.append(f"Your conflict choices couldn't be read. They were kept as {kept}.")
        book = cls(data or ResolutionFile(), path)
        book.problems = problems
        return book

    @property
    def path(self) -> Path:
        return self._path

    @property
    def resolutions(self) -> tuple[Resolution, ...]:
        return tuple(self._data.resolutions)

    @property
    def ignored(self) -> tuple[Ignore, ...]:
        return tuple(self._data.ignored)

    # Choices

    def get(self, kind: str, key: str) -> Resolution | None:
        return self._by_key.get((kind, key))

    def choose(self, conflict: Conflict, claim: Claim, index: Index) -> Resolution:
        """Make this version win."""
        resolution = Resolution(
            conflict.kind, conflict.key, claim.layer, claim.path, seen=seen(conflict, index)
        )
        self._put(resolution)
        return resolution

    def write_own(self, conflict: Conflict, text: str, index: Index) -> Resolution:
        """Use the user's own version instead of any mod's."""
        resolution = Resolution(conflict.kind, conflict.key, text=text, seen=seen(conflict, index))
        self._put(resolution)
        return resolution

    def clear(self, kind: str, key: str) -> None:
        self._data.resolutions = [
            r for r in self._data.resolutions if not (r.kind == kind and r.key == key)
        ]
        self._save()

    def clear_all(self) -> None:
        self._data.resolutions = []
        self._save()

    def state(self, conflict: Conflict, index: Index) -> State:
        resolution = self.get(conflict.kind, conflict.key)
        if resolution is None:
            return NONE
        return CHOSEN if is_current(resolution, conflict, index) else STALE

    def _put(self, resolution: Resolution) -> None:
        kept = [
            r
            for r in self._data.resolutions
            if not (r.kind == resolution.kind and r.key == resolution.key)
        ]
        self._data.resolutions = [*kept, resolution]
        self._save()

    # Ignoring

    def ignore(self, rule: Ignore) -> None:
        if rule not in self._data.ignored:
            self._data.ignored.append(rule)
            self._save()

    def stop_ignoring(self, rule: Ignore) -> None:
        self._data.ignored = [r for r in self._data.ignored if r != rule]
        self._save()

    def ignored_by(self, conflict: Conflict) -> Ignore | None:
        """The rule that hides this conflict, if one does."""
        for rule in self._data.ignored:
            if rule.key:
                if rule.kind == conflict.kind and rule.key == conflict.key:
                    return rule
            elif rule.kind:
                if rule.kind == conflict.kind:
                    return rule
            elif rule.mod and rule.mod in conflict.mods:
                return rule
        return None

    # The patch mod

    @property
    def built(self) -> int:
        return self._data.built

    def mark_built(self, digest: int) -> None:
        self._data.built = digest
        self._save()

    def _save(self) -> None:
        self._by_key = {(r.kind, r.key): r for r in self._data.resolutions}
        save_resolutions(self._data, self._path)


def seen(conflict: Conflict, index: Index) -> tuple[Seen, ...]:
    """Every version of the conflict's object or file, as it is now."""
    return tuple(Seen(c.layer, c.path, _digest(c, index)) for c in conflict.claims)


def is_current(resolution: Resolution, conflict: Conflict, index: Index) -> bool:
    """True if no version changed, appeared or went away since the choice."""
    return set(resolution.seen) == set(seen(conflict, index))


def chosen_claim(resolution: Resolution, conflict: Conflict) -> Claim | None:
    """The version the user picked, or None for their own version or one that's gone."""
    if resolution.own:
        return None
    return next(
        (c for c in conflict.claims if c.layer == resolution.layer and c.path == resolution.path),
        None,
    )


def choices_digest(resolutions: tuple[Resolution, ...]) -> int:
    """Changes whenever a choice does. The patch mod records the one it was made from."""
    ordered = sorted(resolutions, key=lambda r: (r.kind, r.key))
    return xxhash.xxh3_64_intdigest(msgspec.msgpack.encode(ordered))


def copy_resolutions(from_id: str, to_id: str, folder: Path | None = None) -> None:
    """Give a copied playset the same choices."""
    source = resolutions_file(from_id, folder)
    if source.exists():
        shutil.copyfile(source, resolutions_file(to_id, folder))


def _digest(claim: Claim, index: Index) -> int:
    if claim.definition is not None:
        return claim.definition.digest
    entry = index.layers[claim.layer].files.get(claim.path)
    if entry is None:
        return 0
    # Pictures and sounds aren't read, so they have no hash. Their size and
    # timestamp stand in: a re-download may flag one that didn't really change.
    return entry.hash or xxhash.xxh3_64_intdigest(msgspec.msgpack.encode(entry.stamp))
